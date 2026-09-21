"""outlook-graph poller adapter — Microsoft Graph mail via the ms-graph skill's mail.js.

All outlook-graph mechanics live HERE, alongside the prose contract in
`outlook-graph-provider.md`: locating mail.js, the `--list-inbox --json` enumerate, the Graph id
scheme, and the captured item shape. The poller (`scripts/run-poller.py`) loads this adapter
dynamically and drives it through the `ProviderBase` interface — it contains no Outlook specifics.
"""
import json
import os
import sys
from datetime import datetime, timezone

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import (ProviderBase, ProviderError, run_node, slug, find_skill_script,  # noqa: E402
                           node_failure_kind, parse_email_auth)


class Provider(ProviderBase):
    name = "outlook-graph"

    def __init__(self):
        self.mailjs = self._find_mail_js()

    @staticmethod
    def _find_mail_js():
        path = find_skill_script(__file__, "ms-graph", os.path.join("scripts", "mail.js"))
        if not path:
            raise ProviderError("Could not locate ms-graph mail.js for outlook-graph.", kind="config")
        return path

    def enumerate(self, limit):
        res = run_node([self.mailjs, "--list-inbox", "--json", f"--top={limit}"])
        if res.returncode != 0:
            raise ProviderError(f"outlook-graph enumerate failed: {res.stderr.strip()[:300]}",
                                kind=node_failure_kind(res.stderr))
        msgs = json.loads(res.stdout or "[]")
        return [m for m in msgs if not self._own_outbound_reply(m)]

    @staticmethod
    def _own_outbound_reply(m):
        """True for a message Russell sent to other people that surfaced back in his inbox (a reply
        threaded into the conversation, e.g. his "RE: Baggage Fee" to his dad). That is his own outbound
        side, not inbound mail to triage — dropping it here keeps his replies out of the queue. A genuine
        self-note (he is a recipient, so `toMe` is set) is preserved: it's a task he captured for himself.
        `fromMe`/`toMe` come from mail.js, which resolves the mailbox owner's own address."""
        return bool(m.get("fromMe")) and not bool(m.get("toMe"))

    def still_in_inbox_ids(self):
        res = run_node([self.mailjs, "--list-inbox", "--json", "--top=500"])
        if res.returncode != 0:
            return None
        try:
            msgs = json.loads(res.stdout or "[]")
        except ValueError:
            return None
        return {m["id"] for m in msgs if m.get("id")}

    def screen_signal(self, item):
        """Surface this message's envelope-authentication verdict into the security screen payload. Runs
        `mail.js --auth` for the message (the SPF/DKIM/DMARC headers the receiving system stamped on
        arrival, from the same internetMessageHeaders --unsubscribe reads), then parse_email_auth turns
        the raw Authentication-Results / Received-SPF values into the compact summary the screen weighs.
        Returns None on any fetch/parse miss so a missing signal never blocks screening (the screen still
        judges the content)."""
        message_id = item.get("id")
        if not message_id:
            return None
        res = run_node([self.mailjs, f"--auth={message_id}"])
        if res.returncode != 0:
            return None
        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            return None
        return parse_email_auth(data.get("fromAddress") or item.get("fromAddress"),
                                data.get("authenticationResults"), data.get("receivedSpf"))

    def _fetch_body(self, item):
        """The message body, for relay-correspondent extraction when a relay's name isn't already in the
        subject/preview. Only reached for a recognized relay sender whose cheap fields missed, so this
        per-item Graph fetch stays rare."""
        show = run_node([self.mailjs, f"--show={item['id']}"])
        return show.stdout if show.returncode == 0 else ""

    def clear(self, item):
        """Archive an fyi/junk message at triage time (the provider CLEAR: `mail.js --delete` moves it to
        Archive, reversible and still searchable). Returns True on success, False on failure - see
        ProviderBase.clear for why the poller can call this without risk of losing the item."""
        res = run_node([self.mailjs, f"--delete={item['id']}"])
        return res.returncode == 0

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
            "correspondent": item.get("_correspondent"), "emailFile": email_file,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
