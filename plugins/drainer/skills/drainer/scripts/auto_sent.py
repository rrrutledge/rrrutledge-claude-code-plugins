"""Everything the pod sent without Russell that no seen digest has listed, as the digest's fixed section.

send-confidence is the one gate an unreviewed send passes through: whatever goes out on the model's
say-so is logged there with `disposition: "auto-sent"` (`record_auto_sent`), by the script that sent it,
at the moment it sent. So this reads that log, and every auto-sent action reaches the digest whether or
not its worker closed out an item, and whatever kind of send it was.

The digest launcher (`run-digest.py`) renders the section into the digest seed. The session presents the
entries, turns each `[raw context ...]` line into one sentence or drops it, and keeps the undo commands
(listed under a line marking them for the session alone) from Russell. A send undone since (`slack_reaction.py undo`) is listed as undone. The section lists
every auto-sent draft id missing from `<runtime_dir>/auto-sent-reported.json`, and the digest session
adds the ids it listed by running `reported` once Russell has seen the section, so a digest that never
reaches him leaves its sends for the next one.
A queued auto-handle item with an `autoSent` list and an empty `dispositionReason` did nothing but send,
so the seed names it and the digest leaves it out of Auto-handled.

CLI:
    python auto_sent.py reported <runtime_dir> <draft_id>...     # record these sends as seen
"""
import datetime
import json
import os
import sys

REPORTED_FILE = "auto-sent-reported.json"
NOTHING = "Sent without you since the last digest: nothing."


def read_reported(runtime_dir):
    try:
        with open(os.path.join(runtime_dir, REPORTED_FILE), encoding="utf-8") as f:
            return set(json.load(f).get("draft_ids") or [])
    except (OSError, ValueError, AttributeError):
        return set()


def add_reported(runtime_dir, draft_ids):
    ids = read_reported(runtime_dir) | set(draft_ids)
    os.makedirs(runtime_dir, exist_ok=True)
    with open(os.path.join(runtime_dir, REPORTED_FILE), "w", encoding="utf-8") as f:
        json.dump({"draft_ids": sorted(ids)}, f)


def collect(sc, reported):
    """Each send logged auto-sent and not yet reported, oldest first, with whether it was undone since."""
    events = sc.load_events()
    drafts = sc.fold_drafts(events)
    out = []
    for e in events:
        did = e.get("draft_id")
        if e.get("event") != "outcome" or e.get("disposition") != "auto-sent" or not did or did in reported:
            continue
        try:
            ts = datetime.datetime.fromisoformat(e.get("ts")).astimezone()
        except (TypeError, ValueError):
            continue
        d = drafts.get(did) or {}
        out.append({"draft_id": did, "ts": ts, "scored": d.get("scored") or {},
                    "undone": (d.get("outcome") or {}).get("disposition") == "discarded"})
    return sorted(out, key=lambda s: s["ts"])


EMOJI = {
    "heart": "❤️", "+1": "👍", "thumbsup": "👍", "-1": "👎", "thumbsdown": "👎", "pray": "🙏",
    "raised_hands": "🙌", "tada": "🎉", "clap": "👏", "eyes": "👀", "white_check_mark": "✅",
    "heavy_check_mark": "✔️", "smile": "😄", "slightly_smiling_face": "🙂", "grinning": "😀",
    "joy": "😂", "100": "💯", "fire": "🔥", "rocket": "🚀", "star": "⭐", "sparkles": "✨",
    "muscle": "💪", "ok_hand": "👌", "wave": "👋", "thinking_face": "🤔", "raised_hand": "✋",
    "heart_eyes": "😍", "blush": "😊", "sunglasses": "😎", "partying_face": "🥳", "bulb": "💡",
    "memo": "📝", "point_up": "☝️", "handshake": "🤝", "party_popper": "🎉",
}
QUOTE_LIMIT = 300


def emoji_display(name):
    """The Unicode emoji for a Slack shortcode, or a plain-words note for one with no Unicode form (a
    custom workspace emoji, or one this table doesn't carry)."""
    base = (name or "?").split("::")[0]
    return EMOJI.get(base) or f"custom :{base}: emoji"


def _clip(text):
    text = " ".join((text or "").split())
    return text if len(text) <= QUOTE_LIMIT else text[:QUOTE_LIMIT].rstrip() + "..."


def _day(ts):
    """A Slack ts as "Nov 12", in the machine's local time."""
    try:
        d = datetime.datetime.fromtimestamp(float(ts)).astimezone()
    except (TypeError, ValueError, OSError):
        return None
    return d.strftime("%b %d").replace(" 0", " ")


def slack_json(*args):
    """One slack.js call with --json, parsed; raises when the call fails or prints no JSON."""
    from provider_base import find_skill_script, run_node
    path = find_skill_script(__file__, "slack", os.path.join("scripts", "slack.js"))
    if not path:
        raise RuntimeError("the slack skill's slack.js was not found")
    res = run_node([path, *args, "--json"])
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip()[-200:])
    return json.loads(res.stdout)


def reaction_facts(reaction, call=slack_json):
    """The raw facts the digest shows for a reaction: where it went, whose message and what it said, and
    the message it answered (the thread parent, or the previous message in a DM). Each lookup that fails
    just leaves its fact out, so a Slack outage degrades the line instead of losing the send."""
    channel, ts, thread_ts = reaction.get("channel"), reaction.get("ts"), reaction.get("thread_ts")
    facts = {"where": channel or "?", "dm": str(channel or "").startswith("D")}
    try:
        name = call(f"--resolve-name={channel}")["name"]
        facts["where"] = f"DM with {name}" if facts["dm"] else name
    except Exception:
        pass
    try:
        msg = call("--show", f"--channel={channel}", f"--ts={ts}", *([f"--thread-ts={thread_ts}"] if thread_ts else []))
        facts.update(author=msg.get("from"), text=msg.get("text"), day=_day(ts))
    except Exception:
        pass
    try:
        if thread_ts and thread_ts != ts:
            facts["parent"] = call("--show", f"--channel={channel}", f"--ts={thread_ts}")
        elif facts["dm"]:
            history = call("--history", f"--channel={channel}", "--limit=100")
            before = [m for m in history if float(m["ts"]) < float(ts)]
            if before:
                facts["parent"] = before[-1]
    except Exception:
        pass
    return facts


def describe(sent, facts=None):
    """One send as the digest shows it: where, what was sent, and (for a reaction) the message it went on
    with the message it answered as raw context for the digest session to turn into one line or drop."""
    scored = sent["scored"]
    r = scored.get("reaction")
    undone = " (undone since)" if sent["undone"] else ""
    if not r:
        when = sent["ts"].strftime("%b %d").replace(" 0", " ")
        return (f"- **{scored.get('channel') or '?'}** - sent a {scored.get('message_type') or 'message'} "
                f"to {scored.get('recipient') or '?'} ({when}){undone}")
    f = facts
    emoji = emoji_display(r.get("emoji"))
    on = f" on {f['author']}" if f.get("author") else ""
    day = f" ({f['day']})" if f.get("day") else ""
    lines = [f"- **{f['where']}** - {emoji}{on}{day}{undone}:"]
    if f.get("text"):
        lines.append(f'  > "{_clip(f["text"])}"')
    else:
        lines.append("  > (message text unavailable)")
    parent = f.get("parent")
    if parent:
        who = parent.get("from") or "someone"
        lines.append(f'  [raw context, {who}: "{_clip(parent.get("text"))}"]')
    return "\n".join(lines)


def breaker(call):
    """`call` that gives up for the rest of the run after its first failure, so a Slack outage costs one
    failed call instead of several per send."""
    down = []

    def guarded(*args):
        if down:
            raise RuntimeError("slack unavailable")
        try:
            return call(*args)
        except Exception:
            down.append(True)
            raise
    return guarded


def covered_queue_ids(queue):
    """The queued auto-handle items whose only action was a send, which this section or an earlier one
    lists."""
    return [entry.get("id") for entry in queue
            if (entry.get("item") or {}).get("triage") == "auto-handle" and entry["item"].get("autoSent")
            and not (entry["item"].get("dispositionReason") or "").strip()]


def section(sc, runtime_dir, queue, undo_command, call=slack_json):
    """The seed's block for the digest's "Sent without you" section, and the draft ids it lists for
    `reported` to record. With send-confidence missing nothing could have gone out on its own.
    The entries are what Russell sees; the undo commands follow under a line marking them for the digest
    session alone."""
    sends = collect(sc, read_reported(runtime_dir)) if sc else []
    lines = [f"Sent without you since the last digest ({len(sends)}):"] if sends else [NOTHING]
    undos = []
    call = breaker(call)
    for n, s in enumerate(sends, 1):
        r = s["scored"].get("reaction")
        facts = reaction_facts(r, call) if r else None
        lines += ["    " + e for e in describe(s, facts).split("\n")]
        if r and not s["undone"]:
            undos.append(f"    entry {n} above: {undo_command(s['draft_id'])}")
    if undos:
        lines.append("    Undo commands, for you alone, not for Russell (run one when he asks to take a send back):")
        lines += undos
    covered = covered_queue_ids(queue)
    if covered:
        lines.append("    Queue items that only repeat a send listed here or in an earlier digest: "
                     + ", ".join(covered))
    return "\n".join(lines), [s["draft_id"] for s in sends]


def main():
    if len(sys.argv) < 4 or sys.argv[1] != "reported":
        raise SystemExit("usage: auto_sent.py reported <runtime_dir> <draft_id>...")
    add_reported(sys.argv[2], sys.argv[3:])
    print(f"Sent without you reported: {len(sys.argv) - 3} send(s).")


if __name__ == "__main__":
    main()
