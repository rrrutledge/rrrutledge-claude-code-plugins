"""Drainer 48-hour acknowledgement sweep - every deterministic step of it.

What the sweep is for is in engine/ack-review.md. This module holds everything about it that can be
written down in advance:

- the eligibility test (still waiting, needs-you, a real person, 48 business hours old, no reply from
  Russell since, not already acked, not screen-flagged, not a self-email), with the deterministic vetoes
  that sit on top of triage's `sender` call;
- the business-hours clock;
- the once-a-day sweep the digest launcher runs before it starts the review, which writes the due list
  and nothing else;
- the ack ledger reads (writes go through seen-state.js, the one writer of seen.json);
- the review session's script steps: re-checking each due item, sending an approved ack through the
  source's own send path, writing the ledger, clearing a reply that fully handles its item, and recording
  the send-confidence outcome.

Drafting the ack, the "deeply personal" call, the "does a short reply fully handle this" call, and
presenting the batch stay with the review session (engine/ack-review.md).

CLI (the review session and workers run these):
    python ack_sweep.py prepare   --repo <repo>                                  # re-check the due list, print the batch
    python ack_sweep.py send      --repo <repo> --key <k> --body-file <f> [--handles] [--scored-body <f0>]
    python ack_sweep.py skip      --repo <repo> --key <k> [--body-file <f>]
    python ack_sweep.py hand-done --repo <repo> --key <k>
    python ack_sweep.py lookup    --repo <repo> --item <captured item json>     # the ledger entry a worker checks
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
PROVIDERS_DIR = os.path.join(SKILL_DIR, "providers")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from provider_base import (load_ack_ledger, load_seen, received_dt, run_node, self_directed,  # noqa: E402
                           ProviderError, NO_WINDOW, _plugins_root)

SEEN_STATE = os.path.join(SCRIPT_DIR, "seen-state.js")
DUE_FILE = "ack-due.json"
VERDICTS_FILE = "judged-verdicts.json"
ACK_THRESHOLD_HOURS = 48
# A message from Russell this long after a ledger entry is a real reply, not the ack itself.
SUPERSEDE_GRACE = timedelta(minutes=5)
ENUMERATE_PAGE_SIZE = 500

# The local part of a sender address that only machines send from.
_NO_REPLY_RX = re.compile(
    r"(^|[._+-])(no-?reply|do-?not-?reply|notifications?|notify|mailer-daemon|bounces?|postmaster|"
    r"automated|alerts?)([._+-]|$)", re.I)
_LINKEDIN_THREAD_RX = re.compile(r"https?://(?:www\.)?linkedin\.com/comm/messaging/thread/[^\s\"'<>)\]]+", re.I)


# ---------------------------------------------------------------------------- the clock

def business_hours_between(start, end):
    """Hours between two moments counting weekdays only, in the machine's local time zone."""
    if not start or not end or end <= start:
        return 0.0
    cur = start.astimezone().replace(tzinfo=None)
    stop = end.astimezone().replace(tzinfo=None)
    total = 0.0
    while cur < stop:
        seg_end = min(stop, datetime.combine(cur.date() + timedelta(days=1), time()))
        if cur.weekday() < 5:
            total += (seg_end - cur).total_seconds() / 3600
        cur = seg_end
    return total


def _parse(ts):
    return received_dt({"received": ts}) if isinstance(ts, str) else None


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _local(dt):
    return dt.astimezone().strftime("%a %b %d %I:%M %p %Z").replace(" 0", " ") if dt else ""


# ---------------------------------------------------------------------------- eligibility

def _slack_conversation(item):
    """A Slack thread item's conversation is its thread; a DM, group DM, or channel item's is the whole
    channel, since its item id moves to the newest message every time another one arrives."""
    channel = item.get("channel")
    if not channel:
        return None
    return f"{channel}|{item['threadTs']}" if item.get("threadTs") else channel


# The sources the sweep covers, each with the item field (on an enumerated item and a captured record
# alike) naming the conversation the item belongs to.
CONVERSATIONS = {
    "outlook-graph": lambda it: it.get("conversationId"),
    "gmail": lambda it: it.get("threadId"),
    "slack": _slack_conversation,
}


def conversation_key(provider, item):
    """`<source>|<conversation>`, or None for a source or item the sweep doesn't cover."""
    read = CONVERSATIONS.get(provider.name) if provider else None
    conv = read(item) if read else None
    return f"{provider.name}|{conv}" if conv else None


def wait_started(item):
    """When the person started waiting: the oldest message in a Slack unread span, else the arrival."""
    span = [dt for dt in (_parse(m.get("received")) for m in item.get("unread") or []) if dt]
    return min(span) if span else received_dt(item)


def is_relay(provider, item):
    """True when the item is a real person's message delivered through a notification relay: the
    correspondent resolves to a person behind the shared sender address."""
    return str(provider.correspondent(item) or "").startswith("relay|")


def veto(provider, item):
    """The first reason this item can't get an ack, from what is known without reading the thread, or
    None. Triage's `sender` call is one input; the deterministic vetoes sit on top of it, so a confident
    wrong verdict still can't ack a bot."""
    if item.get("_bucket") != "needs-you":
        return "not needs-you"
    if item.get("_screen"):
        return "flagged by the security screen"
    if self_directed(item):
        return "self-email"
    if item.get("_sender") != "person":
        return "sender not judged a person"
    if item.get("fromBot"):
        return "bot or app sender"
    local = (item.get("fromAddress") or "").split("@")[0]
    if local and _NO_REPLY_RX.search(local) and not is_relay(provider, item):
        return "no-reply sender"
    return None


def thread_verdict(info, clock_start, ledger_entry, relay=False):
    """The reason the live thread rules the item out, or None when it is still eligible.

    Any message from Russell after the clock started means he already answered, and an ack already sent
    is such a message, so a lost ledger still can't produce a second ack. A ledger entry suppresses the
    conversation until Russell has since sent a real reply on it."""
    if info.get("listUnsubscribe") and not relay:
        return "bulk mail (List-Unsubscribe)"
    mine = [t for t in (_parse(m.get("sent")) for m in info.get("messages") or []
                        if m.get("fromMe") and not m.get("isDraft")) if t]
    if any(t > clock_start for t in mine):
        return "Russell already replied"
    if ledger_entry:
        at = _parse(ledger_entry.get("at"))
        if not at or not any(t > at + SUPERSEDE_GRACE for t in mine):
            return f"already in the ack ledger ({ledger_entry.get('status')})"
    return None


# ---------------------------------------------------------------------------- state files

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_due(runtime_dir):
    return _read_json(os.path.join(runtime_dir, DUE_FILE), {}).get("due") or []


# ---------------------------------------------------------------------------- the daily sweep

def _public_item(item):
    """The enumerated item as the due list stores it, for the review to re-check and send from."""
    return {k: v for k, v in item.items() if k not in ("_correspondent",)}


def _apply_verdict(item, triage, screen):
    """Stamp the poller's cached triage and screen verdicts onto an enumerated item, the fields `veto`
    reads. Returns False when either verdict is missing, so an unjudged item is never acked."""
    if not triage or not screen:
        return False
    item["_bucket"], item["_sender"] = triage.get("bucket"), triage.get("sender")
    if screen.get("flagged"):
        item["_screen"] = {"flagged": True}
    return True


def sweep(waiting, providers_by_name, runtime_dir, active_correspondents, now=None, dry_run=False):
    """Compute and write the due list from the needs-you items still waiting (enumerated, not yet
    dispatched). Sends nothing, drafts nothing. Returns the due list.

    Only cheap work runs for an item under the threshold: comparing its arrival time to now. The
    live-thread read runs only for a conversation already past 48 business hours that passes every other
    test. An item whose correspondent has a worker open is skipped: that person's real reply is already in
    progress."""
    now = now or datetime.now(timezone.utc)
    ledger = load_ack_ledger(runtime_dir)
    convs = {}
    for it in waiting:
        p = providers_by_name.get(it.get("_source"))
        key = conversation_key(p, it)
        if not key or veto(p, it):
            continue
        corr = p.correspondent(it)
        if corr and corr in (active_correspondents or ()):
            continue
        start = wait_started(it)
        if start:
            convs.setdefault(key, []).append((it, start, corr))

    candidates = []
    for key, members in convs.items():
        clock = min(start for _, start, _ in members)
        hours = business_hours_between(clock, now)
        if hours < ACK_THRESHOLD_HOURS:
            continue
        target, _, corr = max(members, key=lambda m: m[0].get("received") or "")
        p = providers_by_name[target["_source"]]
        info = p.thread_state(target)
        if info is None:
            continue  # couldn't read the thread: no ack on a guess
        relay = is_relay(p, target)
        if thread_verdict(info, clock, ledger.get(key), relay=relay):
            continue
        candidates.append({
            "key": key, "source": target["_source"], "itemId": target["_id"],
            "from": target.get("from"), "subject": target.get("subject"),
            "correspondent": corr, "clockStart": _iso(clock), "businessHours": round(hours, 1),
            "link": info.get("link") or "", "hasDraft": bool(info.get("hasDraft")),
            "sendPath": "by-hand" if relay else "api",
            "received": target.get("received"), "item": _public_item(target), "covers": [],
        })

    # One ack per person per batch, on their most recent waiting thread; the rest ride along as covered.
    by_person = {}
    for c in candidates:
        by_person.setdefault(c["correspondent"] or c["key"], []).append(c)
    due = []
    for group in by_person.values():
        group.sort(key=lambda c: c.get("received") or "", reverse=True)
        lead = group[0]
        lead["covers"] = [{"key": c["key"], "itemId": c["itemId"], "subject": c["subject"]} for c in group[1:]]
        due.append(lead)
    due.sort(key=lambda c: c["clockStart"] or "")

    if dry_run:
        for c in due:
            print(f"  ack due: [{c['source']}] {c['from']} | {c['subject']} - waited {c['businessHours']} "
                  f"business hours ({c['sendPath']})")
    else:
        _write_json(os.path.join(runtime_dir, DUE_FILE), {"updated": _iso(now), "due": due})
    return due


def daily_sweep(review, now=None, dry_run=False):
    """The once-a-day sweep the digest launcher runs before it starts the review: every in-scope source's
    live listing, minus what the poller already dispatched, judged by the verdicts the poller cached for
    each held item. Returns the due list, or [] (written as empty) when the open-worker scan fails, since
    that scan is what proves no real reply is already in progress."""
    opened = review.open_correspondents()
    if opened is None:
        return sweep([], review.providers, review.runtime_dir, set(), now=now, dry_run=dry_run)
    verdicts = _read_json(os.path.join(review.runtime_dir, VERDICTS_FILE), {})
    waiting = []
    for name, p in review.providers.items():
        if name not in CONVERSATIONS:
            continue  # out of the sweep's scope; skip its listing entirely
        seen = load_seen(review.runtime_dir, name)
        try:
            listing = review.listing(name)
        except Exception as e:  # one dark source never stops the others' acks
            print(f"ack sweep: could not list {name} - {e}")
            continue
        for it in listing:
            entry = verdicts.get(it["_id"]) or {}
            if it["_id"] not in seen and _apply_verdict(it, (entry.get("triage") or {}).get("verdict"),
                                                        (entry.get("screen") or {}).get("verdict")):
                waiting.append(it)
    return sweep(waiting, review.providers, review.runtime_dir, opened, now=now, dry_run=dry_run)


# ---------------------------------------------------------------------------- the review (script steps)

def _doc_authoring_script(rel):
    """A document-authoring plugin script outside its skills/ tree (hooks/verify_gate.py,
    send-confidence/send_confidence.py): the dev-repo sibling, else the highest installed version."""
    root = _plugins_root(__file__)
    if not root:
        return None
    sibling = os.path.join(root, "document-authoring", rel)
    if os.path.exists(sibling):
        return sibling
    matches = glob.glob(os.path.join(root, "cache", "*", "document-authoring", "*", rel))

    def version(path):
        ver = path[:-(len(rel) + 1)].rsplit(os.sep, 1)[-1]
        return tuple(int(n) for n in re.findall(r"\d+", ver))

    return max(matches, key=version) if matches else None


def _python(script, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)


def has_receipt(body_file):
    """True when the body file carries a fresh writing-review receipt. The send runs inside this script,
    out of sight of the PreToolUse gate on a direct send command, so the script asks the gate itself and
    refuses when it can't."""
    gate = _doc_authoring_script(os.path.join("hooks", "verify_gate.py"))
    return bool(gate) and _python(gate, "check", body_file).returncode == 0


# The send-confidence channel each source's ack is scored under.
_SCORE_CHANNELS = {"outlook-graph": "outlook-personal", "gmail": "gmail", "slack": "slack"}

ACK_ASK = ("Acknowledge this message in Russell's voice: one short positive line that responds to the "
           "substance of what the person said, then one light line saying Russell will take a look. "
           "No date, no commitment, and no answer to their asks.")


def stage_for_scoring(runtime_dir, entry, current, body_file, inputs, verb):
    """Record and score the ack draft Russell reviewed, the way the stage gate does for a staged
    draft. The send runs inside this script, out of the gate's sight, so the script hands the gate
    its stage itself - with the ack's ask and the message it answers as the scoring context.
    Fail-open."""
    gate = _doc_authoring_script(os.path.join("hooks", "verify_gate.py"))
    if not gate:
        return "send-confidence: not installed; stage not recorded."
    meta = {"channel": _SCORE_CHANNELS.get(entry.get("source"), "other"),
            "recipient": current.get("fromAddress") or current.get("channel"),
            "thread_ref": current.get("_id"), "stage_kind": "draft",
            "script": "ack_sweep.py", "verb": verb,
            "session_kind": "background" if os.environ.get("CLAUDE_JOB_DIR") else "live",
            "ask": ACK_ASK, "inputs": inputs}
    meta_path = os.path.join(runtime_dir, "ack-stage-meta.tmp.json")
    _write_json(meta_path, meta)
    res = _python(gate, "stage", body_file, "--meta", meta_path)
    return (res.stdout + res.stderr).strip()


def record_outcome(*args):
    """Record a send-confidence outcome; returns the command's output (it may print a learning-session
    spawn command for the session to run). Fail-open, like every send-confidence step."""
    sc = _doc_authoring_script(os.path.join("send-confidence", "send_confidence.py"))
    if not sc:
        return "send-confidence: not installed; outcome not recorded."
    res = _python(sc, "outcome", *args)
    return (res.stdout + res.stderr).strip()


def ledger_set(runtime_dir, key, entry):
    tmp = os.path.join(runtime_dir, "ack-ledger-entry.tmp.json")
    _write_json(tmp, entry)
    res = run_node([SEEN_STATE, "ack-set", runtime_dir, key, tmp])
    if res.returncode != 0:
        raise ProviderError(f"ledger write failed for {key}: {res.stderr.strip()[:300]}", kind="config")


def _load_verdict(runtime_dir, item_id):
    """The cached triage and screen verdicts for an item id (whatever their age), or (None, None)."""
    entry = _read_json(os.path.join(runtime_dir, VERDICTS_FILE), {}).get(item_id) or {}
    return (entry.get("triage") or {}).get("verdict"), (entry.get("screen") or {}).get("verdict")


class Review:
    """The review session's deterministic side: one live listing per source, re-checks against it, and
    the per-source sends."""

    def __init__(self, repo):
        from drainer_config import read_config, provider_search_dirs, ensure_main_worktree
        from provider_base import load_providers
        source_repo = os.path.abspath(repo)
        self.cfg = read_config(ensure_main_worktree(source_repo), runtime_root=source_repo)
        self.runtime_dir = self.cfg["runtime_dir"]
        self.providers = {p.name: p for p in load_providers(
            self.cfg["providers"], provider_search_dirs(PROVIDERS_DIR, self.cfg["local_dir"]), cfg=self.cfg)}
        self._listings = {}
        self._open = None

    def listing(self, source):
        if source not in self._listings:
            p = self.providers[source]
            items = p.enumerate(ENUMERATE_PAGE_SIZE)
            for it in items:
                it["_id"], it["_source"] = p.stable_id(it), source
            self._listings[source] = items
        return self._listings[source]

    def open_correspondents(self):
        """Correspondents with a live worker session, read the way the poller reads them."""
        if self._open is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("drainer_run_poller", os.path.join(SCRIPT_DIR, "run-poller.py"))
            poller = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(poller)
            live = poller.live_session_ids()
            # A failed session scan can't prove no worker is open, so it rules every item out.
            self._open = None if live is None else poller.open_correspondents(self.runtime_dir, live)
        return self._open

    def recheck(self, entry):
        """Re-run eligibility for one due entry against the live source. Returns (current_item, reason,
        info): the conversation's newest still-waiting item and its fresh thread read, or None and why it
        stopped qualifying."""
        p = self.providers.get(entry["source"])
        if not p:
            return None, f"provider {entry['source']} not loaded", None
        seen = load_seen(self.runtime_dir, p.name)
        try:
            waiting = [it for it in self.listing(p.name)
                       if conversation_key(p, it) == entry["key"] and it["_id"] not in seen]
        except ProviderError as e:
            return None, f"could not list {p.name}: {e}", None
        if not waiting:
            return None, "no longer waiting (dispatched, handled, or read)", None
        current = max(waiting, key=lambda it: it.get("received") or "")
        prior = entry.get("item") or {}
        if current["_id"] == prior.get("_id"):
            for k in ("_bucket", "_kind", "_sender", "_screen", "_complexity"):
                if k in prior:
                    current[k] = prior[k]
        elif not _apply_verdict(current, *_load_verdict(self.runtime_dir, current["_id"])):
            return None, "a newer message hasn't been judged yet", None
        reason = veto(p, current)
        if reason:
            return None, reason, None
        opened = self.open_correspondents()
        if opened is None:
            return None, "couldn't scan for open worker sessions", None
        corr = p.correspondent(current)
        if corr and corr in opened:
            return None, "a worker is already open for this person", None
        info = p.thread_state(current)
        if info is None:
            return None, "could not read the thread", None
        clock = _parse(entry.get("clockStart")) or wait_started(current)
        reason = thread_verdict(info, clock, load_ack_ledger(self.runtime_dir).get(entry["key"]),
                                relay=is_relay(p, current))
        if reason:
            return None, reason, None
        return current, None, info

    def entry(self, key):
        for e in load_due(self.runtime_dir):
            if e["key"] == key:
                return e
        raise SystemExit(f"no due entry {key} (re-run prepare)")


def _waited(hours):
    return f"{hours / 24:.1f} business days"


def cmd_prepare(review):
    out = []
    for n, entry in enumerate(load_due(review.runtime_dir), 1):
        current, reason, info = review.recheck(entry)
        row = {"n": n, "key": entry["key"], "source": entry["source"], "from": entry.get("from"),
               "subject": entry.get("subject"), "waited": _waited(entry.get("businessHours") or 0),
               "waitingSince": _local(_parse(entry.get("clockStart"))), "sendPath": entry["sendPath"],
               "covers": entry.get("covers") or []}
        if not current:
            row.update(status="dropped", reason=reason)
        else:
            p = review.providers[entry["source"]]
            text = p.triage_text(current)
            link = info.get("link") or entry.get("link") or ""
            if entry["sendPath"] == "by-hand":
                m = _LINKEDIN_THREAD_RX.search(text or "")
                link = m.group(0) if m else link
            linkedin = "linkedin.com" in (current.get("fromAddress") or "").lower()
            row.update(status="ready", link=link, hasDraft=bool(info.get("hasDraft")), message=text,
                       linkedin=linkedin)
        out.append(row)
    print(json.dumps(out, indent=2))


def _now():
    return datetime.now(timezone.utc)


def _cover_entries(review, entry, at):
    for c in entry.get("covers") or []:
        ledger_set(review.runtime_dir, c["key"], {"status": "covered", "itemId": c["itemId"], "at": at,
                                                  "coveredBy": entry["key"]})


def cmd_send(review, key, body_file, handles=False, scored_body=None):
    entry = review.entry(key)
    if entry["sendPath"] != "api":
        raise SystemExit(f"{key} has no programmatic send path; Russell sends it by hand (hand-done)")
    if not has_receipt(body_file):
        raise SystemExit(f"{body_file} has no fresh writing-review receipt; review and mint it first")
    current, reason, _ = review.recheck(entry)
    if not current:
        print(json.dumps({"key": key, "sent": False, "reason": reason}))
        sys.exit(3)
    p = review.providers[entry["source"]]
    ref = p.send_reply(current, body_file)
    at = _iso(_now())
    with open(body_file, encoding="utf-8") as f:
        text = f.read().strip()
    result = {"key": key, "sent": True, "ref": ref}
    if handles:
        # The short reply IS the item's handling: clear it the way a worker completes one, and record it
        # seen so it never dispatches. It never reaches the ledger.
        result["cleared"] = bool(p.clear(current))
        res = run_node([SEEN_STATE, "record", review.runtime_dir, p.name, current["_id"], "ack-handled"])
        result["recordedSeen"] = res.returncode == 0
    else:
        ledger_set(review.runtime_dir, key, {"status": "sent", "itemId": current["_id"], "at": at,
                                             "ref": ref, "text": text})
        _cover_entries(review, entry, at)
    staged = stage_for_scoring(review.runtime_dir, entry, current, scored_body or body_file,
                               p.triage_text(current), "send")
    result["sendConfidence"] = "\n".join(filter(None, [
        staged, record_outcome("--body-file", scored_body or body_file, "--sent-file", body_file)]))
    print(json.dumps(result, indent=2))


def cmd_skip(review, key, body_file=None):
    entry = review.entry(key)
    ledger_set(review.runtime_dir, key, {"status": "skipped", "itemId": entry["itemId"], "at": _iso(_now())})
    result = {"key": key, "skipped": True}
    if body_file:
        staged = stage_for_scoring(review.runtime_dir, entry, {"_id": entry["itemId"]}, body_file, None, "skip")
        result["sendConfidence"] = "\n".join(filter(None, [staged, record_outcome(
            "--body-file", body_file, "--discarded", "--moot",
            "--reason", "ack skipped in review (about the recipient, not the text)")]))
    print(json.dumps(result, indent=2))


def cmd_hand_done(review, key):
    entry = review.entry(key)
    at = _iso(_now())
    ledger_set(review.runtime_dir, key, {"status": "sent", "itemId": entry["itemId"], "at": at,
                                         "ref": {"byHand": True}})
    _cover_entries(review, entry, at)
    print(json.dumps({"key": key, "recorded": "sent by hand"}))


def lookup(runtime_dir, providers_by_name, record):
    """The ledger entry for a captured item's conversation, or None."""
    key = conversation_key(providers_by_name.get(record.get("source")), record)
    entry = load_ack_ledger(runtime_dir).get(key) if key else None
    return {"key": key, **entry} if entry else None


def cmd_lookup(repo, item_file):
    from drainer_config import read_config, provider_search_dirs, ensure_main_worktree
    from provider_base import load_providers
    with open(item_file, encoding="utf-8") as f:
        record = json.load(f)
    source_repo = os.path.abspath(repo)
    cfg = read_config(ensure_main_worktree(source_repo), runtime_root=source_repo)
    providers = {p.name: p for p in load_providers(
        [record.get("source")], provider_search_dirs(PROVIDERS_DIR, cfg["local_dir"]), cfg=cfg)}
    print(json.dumps(lookup(cfg["runtime_dir"], providers, record) or {}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["prepare", "send", "skip", "hand-done", "lookup"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--key")
    ap.add_argument("--body-file")
    ap.add_argument("--scored-body")
    ap.add_argument("--handles", action="store_true")
    ap.add_argument("--item")
    args = ap.parse_args()
    if args.command == "lookup":
        return cmd_lookup(args.repo, args.item)
    review = Review(args.repo)
    if args.command == "prepare":
        return cmd_prepare(review)
    if not args.key:
        raise SystemExit(f"{args.command} needs --key")
    if args.command == "send":
        if not args.body_file:
            raise SystemExit("send needs --body-file")
        return cmd_send(review, args.key, args.body_file, args.handles, args.scored_body)
    if args.command == "skip":
        return cmd_skip(review, args.key, args.body_file)
    return cmd_hand_done(review, args.key)


if __name__ == "__main__":
    main()
