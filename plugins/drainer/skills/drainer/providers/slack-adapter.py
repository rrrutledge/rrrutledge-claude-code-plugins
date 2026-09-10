"""slack poller adapter — a Slack workspace via the slack skill's slack.js (Web API).

All slack mechanics live HERE, alongside the prose contract in `slack-provider.md`: locating slack.js,
the `--list-unread --json` enumerate, the `<channel>:<ts>` id scheme, and the captured item shape. The
poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through the `ProviderBase`
interface — it contains no Slack specifics.

This is the API sibling of `gmail-adapter.py` / `outlook-graph-adapter.py`: same operations, a
different transport. slack.js talks the Slack Web API with a personal xoxc token + xoxd `d` cookie from
the environment (SLACK_BOT_TOKEN / SLACK_COOKIE_D / SLACK_TEAM_ID).
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

    def enumerate(self, limit):
        res = run_node([self.slackjs, "--list-unread", "--json", f"--top={limit}"])
        if res.returncode != 0:
            raise ProviderError(
                f"slack enumerate failed (auth/token+cookie?): {res.stderr.strip()[:300]}", kind="auth")
        return json.loads(res.stdout or "[]")

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
        text, permalink = item.get("preview", ""), ""
        if show.returncode == 0:
            try:
                shown = json.loads(show.stdout or "{}")
                text = shown.get("text") or text
                permalink = shown.get("permalink") or ""
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
                     for m in unread]
            body = (f"{len(unread)} unread messages since your last read, oldest first. Group them into "
                    "distinct asks first - several rapid-fire messages on one topic are one ask; different "
                    "topics are separate asks (the timestamps below are a tiebreaker: minutes apart leans "
                    "one ask, hours or days apart leans separate). Then handle each group as its own unit "
                    "(do the work, draft any reply). The item is not done, and you must not CLEAR it, until "
                    "every group is completed, staged as a draft, or tracked on a follow-up card.\n\n"
                    + "\n\n".join(parts))
        else:
            body = text
        with open(body_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('subject')}\n\nFrom: {item.get('from')}\n"
                    f"Channel: {item.get('channelName')} ({channel})\nReceived: {item.get('received')}\n"
                    f"Unread messages: {item.get('unreadCount')}\n"
                    f"Link: {permalink}\nMessageRef: {channel}:{ts}\n\n---\n\n{body}\n")
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "from": item.get("from"), "subject": item.get("subject"), "received": item.get("received"),
            "snippet": item.get("preview"), "url": permalink, "messageId": f"{channel}:{ts}",
            "channel": channel, "ts": ts, "threadTs": thread_ts,
            "unreadCount": item.get("unreadCount"),
            "channelType": item.get("channelType"), "channelName": item.get("channelName"),
            "teamId": os.environ.get("SLACK_TEAM_ID"),
            "bodyFile": body_file,
            "ts_captured": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
