"""teams poller adapter — Microsoft Teams chats/channels via the teams skill's teams-chat.js.

All teams mechanics live HERE, alongside the prose contract in `teams-provider.md`: locating the teams
skill's teams-chat.js, the enumerate read, the conversation-id scheme, and the captured item shape. The poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through the
`ProviderBase` interface — it contains no Teams specifics.

This is the Teams sibling of `slack-adapter.py`: it wraps a sibling skill's read CLI. teams-chat.js talks
the Teams internal REST services with tokens sniffed from the live Teams web session; the one-time sniff
needs `playwright` (resolved from the home repo's node_modules via NODE_PATH). CLEAR (mark-read) and
DRAFT-MODE stay browser-driven in the worker — see `teams-provider.md`.
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
from provider_base import ProviderBase, ProviderError, run_node, slug, find_skill_script  # noqa: E402

# Stable per-machine home (teams-chat.js caches its sniffed tokens here, absolutely). We also run node
# from here for a consistent cwd.
TOKEN_HOME = os.path.join(os.path.expanduser("~"), ".claude", "drainer")
# Per-conversation message-ID horizons — the highest message ID captured per conversation,
# written by capture() and read by triage_text() to know where to stop fetching.
_HORIZONS = os.path.join(TOKEN_HOME, "teams-msg-horizons.json")


class Provider(ProviderBase):
    name = "teams"

    def __init__(self):
        self.teamsjs = self._find_teams_js()
        self.repo = None  # set by configure(cfg); the home repo's node_modules carries playwright
        os.makedirs(TOKEN_HOME, exist_ok=True)

    def configure(self, cfg):
        """Learn the home repo from the poller so the token sniff can resolve `playwright` from its
        node_modules (see _node_kw). No-op contract on ProviderBase, so this is purely additive."""
        self.repo = cfg.get("repo")

    @staticmethod
    def _find_teams_js():
        path = find_skill_script(__file__, "teams", os.path.join("scripts", "teams-chat.js"))
        if not path:
            raise ProviderError(
                "Could not locate the teams skill's teams-chat.js for the teams provider.", kind="config")
        return path

    def _node_kw(self):
        """run_node kwargs: NODE_PATH so the token sniff resolves `playwright` from the home repo.

        configure(cfg) supplies the home repo, whose node_modules carries playwright. When tokens are
        already cached, node never loads playwright.
        """
        env = dict(os.environ)
        repo = self.repo
        if repo:
            node_modules = os.path.join(repo, "node_modules")
            existing = env.get("NODE_PATH")
            env["NODE_PATH"] = node_modules + (os.pathsep + existing if existing else "")
        return {"cwd": TOKEN_HOME, "env": env}

    def enumerate(self, limit):
        res = run_node([self.teamsjs, "enumerate", "--top", str(limit)], **self._node_kw())
        if res.returncode != 0:
            raise ProviderError(f"teams enumerate failed (signed in to Teams web?): {res.stderr.strip()[:300]}", kind="auth")
        items = json.loads(res.stdout or "[]")
        # The engine's triage payload reads top-level from/subject/received/preview (the slack
        # convention). Teams' native shape is label + nested lastMessage, so surface those fields at
        # the top level for triage; capture/stable_id still read the native label/lastMessage below.
        for it in items:
            lm = it.get("lastMessage") or {}
            it.setdefault("subject", it.get("label"))
            it.setdefault("from", lm.get("from") or it.get("label"))
            it.setdefault("received", lm.get("time"))
            it.setdefault("preview", lm.get("preview"))
        return items

    @staticmethod
    def _load_horizons():
        try:
            with open(_HORIZONS, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_horizon(conv_id, msg_id):
        horizons = Provider._load_horizons()
        horizons[conv_id] = msg_id
        tmp = _HORIZONS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(horizons, f, indent=2)
        os.replace(tmp, _HORIZONS)

    def _new_msgs_since(self, conv_id, last_msg_id, batch_size=20, max_messages=200):
        """Fetch messages newer than last_msg_id, paginating in batches of batch_size.

        Grows the window until a message with id <= last_msg_id appears (the known
        boundary) or the conversation start is reached. Returns messages oldest-first.
        If last_msg_id is None, fetches up to max_messages from the start.
        """
        top = 0
        msgs = []
        while top < max_messages:
            top = min(top + batch_size, max_messages)
            show = run_node([self.teamsjs, "messages", conv_id, "--top", str(top)], **self._node_kw())
            if show.returncode != 0:
                break
            try:
                msgs = json.loads(show.stdout or "[]")
            except ValueError:
                break
            if not msgs:
                break
            if last_msg_id is not None:
                try:
                    if any(int(m.get("id", "0")) <= int(last_msg_id) for m in msgs):
                        break  # reached the known boundary
                except (ValueError, TypeError):
                    break
            if len(msgs) < top:
                break  # reached the beginning of the conversation
        if last_msg_id is not None:
            try:
                new = [m for m in msgs if int(m.get("id", "0")) > int(last_msg_id)]
            except (ValueError, TypeError):
                new = msgs
        else:
            new = msgs
        return list(reversed(new))

    def triage_text(self, item):
        # For meeting chats, surface messages newer than the last captured message so triage
        # sees all new content — not just the lastMessage preview.
        if item.get("type") != "meeting":
            return item.get("preview") or ""
        conv_id = item.get("id")
        if not conv_id:
            return item.get("preview") or ""
        last_msg_id = self._load_horizons().get(conv_id)
        new_msgs = self._new_msgs_since(conv_id, last_msg_id)
        if not new_msgs:
            return item.get("preview") or ""
        return "\n".join(f"{m.get('from', '?')}: {m.get('text', '')}" for m in new_msgs)

    def stable_id(self, item):
        lm = item.get("lastMessage") or {}
        digits = "".join(c for c in (lm.get("time") or "") if c.isdigit())
        when = f"{digits[:8]}-{digits[8:12]}"  # YYYYMMDD-HHMM
        who = slug(item.get("label") or lm.get("from") or "")
        words3 = slug("-".join((lm.get("preview") or "").split()[:3]))
        return f"{self.name}-{when}-{who}-{words3}".strip("-")[:72]

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        msg_file = os.path.join(items_dir, f"{iid}.msg.md")
        conv_id = item["id"]  # IC3 conversation id (messages/CLEAR handle)
        lm = item.get("lastMessage") or {}
        # Pull recent messages; teams-chat.js now tags each with unread:true/false using the IC3
        # consumptionhorizon so we can separate new messages from context-only ones.
        show = run_node([self.teamsjs, "messages", conv_id, "--top", "20"], **self._node_kw())
        msgs = []
        if show.returncode == 0:
            try:
                msgs = json.loads(show.stdout or "[]")
            except ValueError:
                msgs = []
        header = (f"# {item.get('label')}\n\nChat/From: {item.get('label')}\n"
                  f"Type: {item.get('type')}\nLatest: {lm.get('time')}\nLink: {item.get('deepLink')}\n\n---\n\n")
        if msgs:
            # Reverse so oldest-first; determine whether horizon data is available.
            ordered = list(reversed(msgs))
            horizon_known = any(m.get("unread") is not None for m in ordered)
            def fmt_msg(m):
                if horizon_known:
                    tag = "[NEW] " if m.get("unread") else "[context] "
                else:
                    tag = ""
                return f"**{tag}{m.get('from')}** ({m.get('time')}):\n{m.get('text')}"
            body = "\n\n".join(fmt_msg(m) for m in ordered)
        else:
            body = lm.get("preview") or "(could not load messages)"
        with open(msg_file, "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
        # Advance the per-conversation horizon to the highest message ID captured, so triage_text
        # knows where to stop fetching on the next cycle (independent of Teams' read state).
        msg_ids = [int(m["id"]) for m in msgs if m.get("id", "").isdigit()]
        if msg_ids:
            self._save_horizon(conv_id, str(max(msg_ids)))
        # Record the first unread message id so the worker knows the exact boundary.
        first_unread_id = next((m["id"] for m in reversed(msgs) if m.get("unread")), None)
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "from": lm.get("from") or item.get("label"), "subject": item.get("label"),
            "chatType": item.get("type"), "received": lm.get("time"),
            "snippet": lm.get("preview"), "url": item.get("deepLink"),
            "messageId": conv_id, "convId": conv_id,
            "firstUnreadMessageId": first_unread_id,
            "msgFile": msg_file, "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
