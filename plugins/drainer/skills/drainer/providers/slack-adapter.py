"""slack poller adapter — a Slack workspace via the slack skill's slack.js (Web API).

All slack mechanics live HERE, alongside the prose contract in `slack-provider.md`: locating slack.js,
the `--list-unread --json` enumerate, the `<channel>:<ts>` id scheme, and the captured item shape. The
poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through the `ProviderBase`
interface — it contains no Slack specifics.

This is the API sibling of `gmail-adapter.py` / `outlook-graph-adapter.py`: same operations, a
different transport. slack.js talks the Slack Web API with a personal xoxc token + xoxd `d` cookie from
the environment (SLACK_BOT_TOKEN / SLACK_COOKIE_D / SLACK_TEAM_ID).

**Self-heal on `invalid_auth`** (`attempt_recovery`, below): that xoxc/cookie pair rotates periodically —
usually just the `d` session cookie, not the token — and slack.js then fails every call until someone
re-derives it. Rather than waiting for a human to notice and sniff a fresh one out of DevTools, this
adapter re-derives it itself from the persistent, already-logged-in browser-chauffeur browser via the
slack skill's `slack-token-refresh.js` (a plain, deterministic script — no AI in the loop). It only ever
reads a value Slack's own client already keeps for an authenticated session; it cannot manufacture a
session that was never there. When that browser's OWN Slack session has fully logged out, there is
nothing to read, `attempt_recovery` returns False, and the poller's normal auth-failure escalation takes
over (see run-poller.py's AUTH_FAILURE_ALERT_THRESHOLD) — so a genuinely expired browser session still
reaches Russell instead of failing silently, it just takes one more unattended step to get there.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    import winreg
except ImportError:  # pragma: no cover - this adapter only ever runs on Russell's Windows machine
    winreg = None

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, run_node, find_skill_script  # noqa: E402

# The specific, narrow symptom attempt_recovery targets — slack.js's exact wording (see its `call()`)
# when the Slack API rejects the current token+cookie pair. Deliberately NOT triggered on every kind="auth"
# failure: a plain network blip should just retry next cycle on its own, not spin up a browser tab.
_INVALID_AUTH_RE = re.compile(r"invalid_auth", re.IGNORECASE)


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

    def attempt_recovery(self, error_message):
        """Self-heal an `invalid_auth` failure by re-deriving the xoxc token + `d` cookie from the
        persistent browser-chauffeur browser's own (already-authenticated) Slack session — see the module
        docstring. Returns True only once the fresh pair is both applied to THIS process's environment (so
        the poller's immediate retry this cycle sees it) and persisted to the Windows User-scope registry
        (so the next 5-minute poller cycle — a fresh `pythonw` process launched by Task Scheduler, which
        reads its environment from the registry at launch, not from sourcing $PROFILE — sees it too,
        without waiting for anyone to open a new terminal). Never logs or returns either value; only a
        status string ever leaves this method's own process boundary in the caller's direction."""
        if not _INVALID_AUTH_RE.search(error_message or ""):
            return False  # not the specific symptom this recovery targets — e.g. a network blip
        team_id = os.environ.get("SLACK_TEAM_ID")
        if not team_id:
            return False
        refresh_js = find_skill_script(__file__, "slack", os.path.join("scripts", "slack-token-refresh.js"))
        if not refresh_js:
            return False  # a missing helper is a config problem, not something recovery can fix
        res = run_node([refresh_js, f"--team={team_id}"])
        if res.returncode != 0:
            return False
        try:
            result = json.loads(res.stdout or "{}")
        except ValueError:
            return False
        if result.get("status") != "ok":
            return False  # most commonly "not_logged_in" — the browser's own session has expired too
        token, cookie = result.get("token"), result.get("cookie")
        if not token or not cookie:
            return False
        os.environ["SLACK_BOT_TOKEN"] = token
        os.environ["SLACK_COOKIE_D"] = cookie
        self._persist_to_registry(SLACK_BOT_TOKEN=token, SLACK_COOKIE_D=cookie)
        return True

    @staticmethod
    def _persist_to_registry(**values):
        """Write `values` into `HKEY_CURRENT_USER\\Environment` (Windows User-scope env vars), matching
        each name's existing registry value type when it already has one. A no-op when `winreg` isn't
        available (any non-Windows run) — this adapter only ever runs on Russell's Windows machine, so
        that path exists purely as a defensive fallback, not a supported target."""
        if winreg is None:
            return
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                              winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        try:
            for name, value in values.items():
                try:
                    _, existing_type = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    existing_type = winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, existing_type, value)
        finally:
            key.Close()

    def enumerate(self, limit):
        res = run_node([self.slackjs, "--list-unread", "--json", f"--top={limit}"])
        if res.returncode != 0:
            raise ProviderError(
                f"slack enumerate failed (auth/token+cookie?): {res.stderr.strip()[:300]}", kind="auth")
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
