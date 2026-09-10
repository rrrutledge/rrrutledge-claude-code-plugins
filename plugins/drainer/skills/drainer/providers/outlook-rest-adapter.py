"""outlook-rest poller adapter — Outlook mail via the ms-rest skill's outlook-mail.js.

All outlook-rest mechanics live HERE, alongside the prose contract in `outlook-rest-provider.md`:
locating ms-rest's outlook-mail.js, the `enumerate` read, the Outlook REST id scheme, and the captured
item shape. The poller (`scripts/run-poller.py`) loads this adapter dynamically and drives it through
the `ProviderBase` interface — it contains no Outlook specifics.

This is the REST-transport sibling of the Graph-transport `outlook-graph-adapter.py`: same
operations, a different transport (the ms-rest plugin's Outlook REST v2.0 path with a bearer token
sniffed from the live Outlook web session, rather than ms-graph/MSAL). It reads whatever Outlook
account is signed into Outlook web, so it suits a work mailbox with no personal Graph app. The
one-time token sniff needs `playwright`; steady-state cycles use the cached token and touch no
browser. See `outlook-rest-provider.md` for AUTH/CLEAR/DRAFT.
"""
import json
import os
import sys
from datetime import datetime, timezone

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, run_node, slug, find_skill_script  # noqa: E402

# Stable per-machine home for the sniffed-token cache. ms-rest caches at <cwd>/.tmp/outlook-token.json,
# so we run its node calls from here to keep the token stable across cycles (instead of wherever the
# scheduled task happened to start). Survives plugin version bumps and is shared across repos.
TOKEN_HOME = os.path.join(os.path.expanduser("~"), ".claude", "drainer")


class Provider(ProviderBase):
    name = "outlook-rest"

    def __init__(self):
        self.mailjs = self._find_mail_js()
        self.repo = None  # set by configure(cfg); the home repo's node_modules carries playwright
        os.makedirs(TOKEN_HOME, exist_ok=True)

    def configure(self, cfg):
        """Learn the home repo from the poller so the token sniff can resolve `playwright` from its
        node_modules (see _node_kw). No-op contract on ProviderBase, so this is purely additive."""
        self.repo = cfg.get("repo")

    @staticmethod
    def _find_mail_js():
        path = find_skill_script(__file__, "ms-rest", "outlook-mail.js")
        if not path:
            raise ProviderError("Could not locate ms-rest outlook-mail.js for outlook-rest.", kind="config")
        return path

    def _node_kw(self):
        """run_node kwargs: stable cwd for the token cache + NODE_PATH so the sniff resolves `playwright`.

        configure(cfg) supplies the home repo, whose node_modules carries playwright (the ms-rest token
        sniff requires it). When the token is already cached, node never loads playwright.
        """
        env = dict(os.environ)
        repo = self.repo
        if repo:
            node_modules = os.path.join(repo, "node_modules")
            existing = env.get("NODE_PATH")
            env["NODE_PATH"] = node_modules + (os.pathsep + existing if existing else "")
        return {"cwd": TOKEN_HOME, "env": env}

    def _from_str(self, frm):
        """enumerate/get return `from` as {name,address} (case varies); render 'Name <address>'."""
        if isinstance(frm, dict):
            name = frm.get("name") or frm.get("Name") or ""
            addr = frm.get("address") or frm.get("Address") or ""
            return f"{name} <{addr}>".strip() if (name or addr) else ""
        return frm or ""

    def _from_addr(self, frm):
        if isinstance(frm, dict):
            return frm.get("address") or frm.get("Address") or ""
        return frm or ""

    def enumerate(self, limit):
        # outlook-mail.js enumerate pages the WHOLE inbox newest-first (no --top); slice to `limit`.
        res = run_node([self.mailjs, "enumerate"], **self._node_kw())
        if res.returncode != 0:
            raise ProviderError(f"outlook-rest enumerate failed (signed in to Outlook web?): {res.stderr.strip()[:300]}", kind="auth")
        items = json.loads(res.stdout or "[]")
        return items[:limit]

    def stable_id(self, item):
        digits = "".join(c for c in (item.get("received") or "") if c.isdigit())
        recv = f"{digits[:8]}-{digits[8:12]}"  # YYYYMMDD-HHMM
        sender = slug(self._from_addr(item.get("from")).split("@")[0])
        subj3 = slug("-".join((item.get("subject") or "").split()[:3]))
        return f"{self.name}-{recv}-{sender}-{subj3}".strip("-")[:72]

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        email_file = os.path.join(items_dir, f"{iid}.email.md")
        rest_id = item["id"]  # the Outlook REST id (CLEAR/get handle)
        show = run_node([self.mailjs, "get", rest_id], **self._node_kw())
        body = "(could not load body)"
        if show.returncode == 0:
            try:
                full = json.loads(show.stdout or "{}")
                body = full.get("body") or full.get("preview") or body
            except ValueError:
                body = show.stdout or body
        from_str = self._from_str(item.get("from"))
        with open(email_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('subject')}\n\nFrom: {from_str}\nReceived: {item.get('received')}\n"
                    f"Link: {item.get('webLink')}\nMessageId: {rest_id}\n\n---\n\n{body}\n")
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "from": from_str, "subject": item.get("subject"), "received": item.get("received"),
            "snippet": item.get("preview"), "url": item.get("webLink"), "messageId": rest_id,
            "emailFile": email_file, "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
