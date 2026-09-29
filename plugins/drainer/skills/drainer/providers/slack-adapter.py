"""slack poller adapter — a Slack workspace via the slack skill's slack.js (Web API).

All slack mechanics live HERE, alongside the prose contract in `slack-provider.md`: locating slack.js,
the `--list-unread --json` enumerate, the `<channel>:<ts>` id scheme, and the captured item shape. The
poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through the `ProviderBase`
interface — it contains no Slack specifics.

This is the API sibling of `gmail-adapter.py` / `outlook-graph-adapter.py`: same operations, a
different transport. slack.js talks the Slack Web API with a personal xoxc token + xoxd `d` cookie from
the environment (SLACK_BOT_TOKEN / SLACK_COOKIE_D / SLACK_TEAM_ID).

**Self-heal on `invalid_auth` lives in slack.js itself, not here.** That xoxc/cookie pair rotates
periodically (usually just the `d` session cookie, not the token); `slack.js`'s `call()` now retries
ONCE on `invalid_auth` by re-deriving a fresh pair from the persistent, already-logged-in
browser-chauffeur browser (the sibling `slack-token-refresh.js` - a plain, deterministic script, no AI
involved) before giving up, and persists the fresh pair to the Windows registry so the next process
(this poller's next 5-minute cycle, a worker session, a manual run) picks it up too. This adapter never
drives that recovery itself - putting it in `slack.js`'s own call layer means EVERY command that funnels
through it (list-unread, show, mark, react, send, ...) self-heals, not just this adapter's `enumerate`.

What this adapter DOES do is tell "already tried and failed" apart from an ordinary transient auth blip,
so only the former earns an immediate diagnostic session instead of waiting for the once-a-day digest -
see `_UNRECOVERABLE_MARKER` and the `kind="auth-unrecoverable"` it raises below, and
run-poller.py's `AUTH_UNRECOVERABLE_ALERT_THRESHOLD`.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, run_node, find_skill_script  # noqa: E402

# slack.js's exact wording (see its `call()`) when ITS OWN internal auto-refresh already ran and still
# didn't clear invalid_auth - the strong signal that a human is needed now, not just another cycle's
# ordinary auth retry. Keep this string and slack.js's in sync if either changes.
_UNRECOVERABLE_MARKER = "auto-refresh did not fix it"


def _attachment_lines(files):
    """Render slack.js's per-message `files` metadata (name/mimetype/permalink) as body lines so a
    worker always knows a message carried an image/file, and where to look - see the handoff this
    closes: a text-only capture silently dropped a screenshot the sender's message depended on."""
    if not files:
        return ""
    return "\n" + "\n".join(
        f"[Attachment: {f.get('name', '(unnamed)')} ({f.get('mimetype', '')})] {f.get('permalink', '')}"
        for f in files
    )


class Provider(ProviderBase):
    name = "slack"

    def __init__(self):
        self.slackjs = self._find_slack_js()

    @staticmethod
    def _find_slack_js():
        path = find_skill_script(__file__, "slack", os.path.join("scripts", "slack.js"))
        if not path:
            raise ProviderError("Could not locate the slack skill's slack.js for the slack provider.",
                                kind="config")
        return path

    def triage_signal(self, item):
        """Merge the base self-email signal with Slack's own `knownContact` flag, so the model's own
        reasoning (and the one-line `reason` it returns) already reflects that Russell has messaged
        this person/thread before - not just the deterministic post-triage override in run-poller.py
        that guarantees the outcome either way (see `_apply_slack_overrides`)."""
        base = super().triage_signal(item)
        if item.get("knownContact"):
            return {**(base or {}), "knownContact": True}
        return base

    def enumerate(self, limit):
        res = run_node([self.slackjs, "--list-unread", "--json", f"--top={limit}"])
        if res.returncode != 0:
            stderr = res.stderr.strip()[:300]
            # slack.js already tried its own internal auto-refresh and it still didn't clear invalid_auth
            # (see the module docstring) - a stronger signal than an ordinary transient auth blip, worth
            # escalating to Russell immediately rather than waiting for the once-a-day digest.
            kind = "auth-unrecoverable" if _UNRECOVERABLE_MARKER in stderr else "auth"
            raise ProviderError(f"slack enumerate failed (auth/token+cookie?): {stderr}", kind=kind)
        return json.loads(res.stdout or "[]")

    def clear(self, item):
        """Mark an fyi/junk Slack conversation read at triage time (the provider CLEAR: advance the read
        cursor via `slack.js --mark`). Reversible and non-destructive - a newer message re-surfaces it.
        Returns True on success, False on failure - see ProviderBase.clear for why the poller can call
        this safely. Only ever runs on fyi/junk items (the poller's queue path), never needs-you (those
        clear via their worker after every ask in the span is handled), so advancing the read cursor over
        the unread span here buries no unhandled ask - the same reason email's fyi/junk archive at triage
        is safe. Slack marks read in-process over the Web API, so unlike Teams it needs no batch worker."""
        cmd = [self.slackjs, "--mark", f"--channel={item['channel']}", f"--ts={item['ts']}"]
        thread_ts = item.get("threadTs")
        if thread_ts:
            cmd.append(f"--thread-ts={thread_ts}")
        res = run_node(cmd)
        return res.returncode == 0

    def stable_id(self, item):
        # <channel>:<ts> is already unique per message (a Slack ts is unique within a channel); slugify
        # to a filesystem-safe id and keep it stable across cycles so seen-state dedups on it.
        #
        # One item per conversation, keyed to its latest ts — deliberately NOT one item per distinct ask.
        # A conversation that accreted several asks between reads (the graphics/logo/case-study/channel/line
        # burst) stays a single item whose body carries the whole unread span (see `capture`), and the
        # worker handles every ask before clearing (worker-core §2/§6). Splitting one conversation into
        # per-ask items would mean guessing task boundaries semantically at poll time (lossy and brittle)
        # and would fragment the read cursor, which advances per conversation, not per message. The
        # capture-the-whole-span approach is the robust path.
        raw = f"{self.name}-{item.get('channel')}-{item.get('ts')}"
        return re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-")[:72]

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        body_file = os.path.join(items_dir, f"{iid}.slack.md")
        channel, ts = item["channel"], item["ts"]
        thread_ts = item.get("threadTs") or ""
        show_cmd = [self.slackjs, "--show", f"--channel={channel}", f"--ts={ts}", "--json"]
        if thread_ts:
            show_cmd.append(f"--thread-ts={thread_ts}")
        show = run_node(show_cmd)
        text, permalink, shown_files = item.get("preview", ""), "", []
        if show.returncode == 0:
            try:
                shown = json.loads(show.stdout or "{}")
                text = shown.get("text") or text
                permalink = shown.get("permalink") or ""
                shown_files = shown.get("files") or []
            except ValueError:
                pass
        # Write the FULL unread span into the body, not only the newest message. One DM/channel/thread can
        # accrete several distinct asks between reads, and CLEAR (advancing the read cursor to `ts`) drops
        # every still-unread message under it — so the worker must see them all here to handle each one
        # before clearing. enumerate already computed this span (same last_read snapshot, no extra API
        # call); fall back to the single shown message when it's absent (older slack.js or a lone message).
        unread = item.get("unread") or []
        if len(unread) > 1:
            parts = [f"**{m.get('from') or item.get('from') or '?'}** "
                     f"({(m.get('received') or '')[:16].replace('T', ' ')}):\n{m.get('text', '')}"
                     f"{_attachment_lines(m.get('files'))}"
                     for m in unread]
            body = (f"{len(unread)} unread messages since your last read, oldest first. Group them into "
                    "distinct asks first - several rapid-fire messages on one topic are one ask; different "
                    "topics are separate asks (the timestamps below are a tiebreaker: minutes apart leans "
                    "one ask, hours or days apart leans separate). Then handle each group as its own unit "
                    "(do the work, draft any reply). The item is not done, and you must not CLEAR it, until "
                    "every group is completed, staged as a draft, or tracked on a follow-up card.\n\n"
                    + "\n\n".join(parts))
        else:
            body = text + _attachment_lines(shown_files)
        with open(body_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('subject')}\n\nFrom: {item.get('from')}\n"
                    f"Channel: {item.get('channelName')} ({channel})\nReceived: {item.get('received')}\n"
                    f"Unread messages: {item.get('unreadCount')}\n"
                    f"Link: {permalink}\nMessageRef: {channel}:{ts}\n\n---\n\n{body}\n")
        # Every attachment across the captured span, flattened - shown_files for the single-message case,
        # each unread message's own files for the multi-message case. Kept on the record for other tooling;
        # the body lines above are what a worker actually reads.
        all_files = list(shown_files) if len(unread) <= 1 else [
            f for m in unread for f in (m.get("files") or [])
        ]
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "triageReason": item.get("_triageReason"),
            "from": item.get("from"), "subject": item.get("subject"), "received": item.get("received"),
            "snippet": item.get("preview"), "url": permalink, "messageId": f"{channel}:{ts}",
            "channel": channel, "ts": ts, "threadTs": thread_ts,
            "unreadCount": item.get("unreadCount"),
            "channelType": item.get("channelType"), "channelName": item.get("channelName"),
            "knownContact": bool(item.get("knownContact")),
            "teamId": os.environ.get("SLACK_TEAM_ID"),
            "bodyFile": body_file,
            "ts_captured": datetime.now(timezone.utc).isoformat(),
        }
        if all_files:
            record["files"] = all_files
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
