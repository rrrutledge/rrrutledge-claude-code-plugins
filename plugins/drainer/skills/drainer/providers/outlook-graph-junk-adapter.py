"""outlook-graph-junk poller adapter — Junk Email folder via the ms-graph skill's mail.js.

Sibling of outlook-graph-adapter.py: same mailbox, same mail.js, same id scheme and captured item
shape — the only difference is which folder gets enumerated (Junk Email instead of Inbox), so
`name` alone drives the id prefix and body-file naming inherited by stable_id()/capture()'s shape.
Kept as its own adapter file (not a subclass) per the provider pattern in engine/provider.md: each
source's mechanics live in one self-contained two-file provider.
"""
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, ProviderError, run_node, slug, find_skill_script  # noqa: E402


class Provider(ProviderBase):
    name = "outlook-graph-junk"

    def __init__(self):
        self.mailjs = self._find_mail_js()

    @staticmethod
    def _find_mail_js():
        path = find_skill_script(__file__, "ms-graph", os.path.join("scripts", "mail.js"))
        if not path:
            raise ProviderError("Could not locate ms-graph mail.js for outlook-graph-junk.", kind="config")
        return path

    def enumerate(self, limit):
        res = run_node([self.mailjs, "--list-junk", "--json", f"--top={limit}"])
        if res.returncode != 0:
            raise ProviderError(f"outlook-graph-junk enumerate failed (auth?): {res.stderr.strip()[:300]}",
                                kind="auth")
        return json.loads(res.stdout or "[]")

    def stable_id(self, item):
        # Timestamp to the second (plus ms when Graph supplies them) so two messages from the same
        # sender with the same opening subject in the same minute don't collide and silently drop one.
        digits = "".join(c for c in (item.get("received") or "") if c.isdigit())
        recv = f"{digits[:8]}-{digits[8:14]}{digits[14:17]}"  # YYYYMMDD-HHMMSS(ms)
        sender = slug((item.get("fromAddress") or item.get("from") or "").split("@")[0])
        subj3 = slug("-".join((item.get("subject") or "").split()[:3]))
        return f"{self.name}-{recv}-{sender}-{subj3}".strip("-")[:72]

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        email_file = os.path.join(items_dir, f"{iid}.email.md")
        show = run_node([self.mailjs, f"--show={item['id']}"])
        body = show.stdout if show.returncode == 0 else "(could not load body)"
        with open(email_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('subject')}\n\nFrom: {item.get('from')}\nReceived: {item.get('received')}\n"
                    f"Link: {item.get('webLink')}\nMessageId: {item['id']}\n\n---\n\n{body}\n")
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "from": item.get("from"), "subject": item.get("subject"), "received": item.get("received"),
            "snippet": item.get("preview"), "url": item.get("webLink"), "messageId": item["id"],
            "emailFile": email_file, "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
