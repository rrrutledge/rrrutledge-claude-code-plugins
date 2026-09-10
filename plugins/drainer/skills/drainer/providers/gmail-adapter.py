"""gmail poller adapter — Gmail/Workspace mail via the gmail skill's gmail.js (Gmail REST API over OAuth).

All gmail mechanics live HERE, alongside the prose contract in `gmail-provider.md`: locating gmail.js,
the `--list-inbox --json` enumerate, the Message-ID id scheme, and the captured item shape. The poller
(`scripts/run-poller.py`) loads this adapter dynamically and drives it through the `ProviderBase`
interface — it contains no Gmail specifics.

This is the Gmail-REST sibling of `outlook-graph-adapter.py` (Graph). Same operations, both REST: gmail.js
authorizes through the gmail skill's cached OAuth token (GMAIL_OAUTH_CLIENT_ID / GMAIL_OAUTH_CLIENT_SECRET),
so no per-item credential lives in this adapter's environment.
"""
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone

# scripts/ is on sys.path (the poller inserts it); fall back to a relative add when run standalone.
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import (ProviderBase, ProviderError, run_node, slug, find_skill_script,  # noqa: E402
                           node_failure_kind, parse_email_auth)


class Provider(ProviderBase):
    name = "gmail"

    def __init__(self):
        self.gmailjs = self._find_gmail_js()

    @staticmethod
    def _find_gmail_js():
        path = find_skill_script(__file__, "gmail", os.path.join("scripts", "gmail.js"))
        if not path:
            raise ProviderError("Could not locate the gmail skill's gmail.js for the gmail provider.",
                                kind="config")
        return path

    @staticmethod
    def _web_link(message_id):
        """A Gmail web link that opens the message by its RFC822 Message-ID (account-index agnostic)."""
        mid = (message_id or "").strip("<>")
        return "https://mail.google.com/mail/u/0/#search/" + urllib.parse.quote(f"rfc822msgid:{mid}")

    def enumerate(self, limit):
        res = run_node([self.gmailjs, "--list-inbox", "--json", f"--top={limit}"])
        if res.returncode != 0:
            raise ProviderError(f"gmail enumerate failed: {res.stderr.strip()[:300]}",
                                kind=node_failure_kind(res.stderr))
        msgs = json.loads(res.stdout or "[]")
        return [m for m in msgs if not self._own_outbound_reply(m)]

    @staticmethod
    def _own_outbound_reply(m):
        """True for a message the account owner sent to other people that the Workspace account keeps in
        the inbox (russ@ISC files its own sent replies under INBOX, so they come back through the enumerate).
        That is Russell's own outbound side of a conversation, not inbound mail to triage — dropping it here
        keeps a long thread from re-queuing his every reply as its own item. A genuine self-note (he is the
        only recipient, so `toMe` is set) is preserved: it's a task he captured for himself, not a reply to
        someone else. `fromMe`/`toMe` come from gmail.js, which knows the account owner's own address."""
        return bool(m.get("fromMe")) and not bool(m.get("toMe"))

    def triage_text(self, item):
        """Fetch the message body and return just the NEW content (quoted reply chain stripped), so the
        triage step sees what the message actually says instead of only its subject line. The enumerate
        listing carries no preview, so without this triage would classify a Gmail thread purely
        on sender + subject. Falls back to the (usually empty) preview if the body can't be fetched."""
        message_id = item.get("id")
        if not message_id:
            return item.get("preview") or ""
        show = run_node([self.gmailjs, f"--show={message_id}"])
        if show.returncode != 0:
            return item.get("preview") or ""
        return self._new_message_excerpt(show.stdout)

    def screen_signal(self, item):
        """Surface this message's envelope-authentication verdict into the security screen payload. Runs
        `gmail.js --auth` for the message (the SPF/DKIM/DMARC headers Gmail stamped on arrival), then
        parse_email_auth turns the raw Authentication-Results / Received-SPF values into the compact
        summary the screen weighs. Returns None on any fetch/parse miss so a missing signal never blocks
        screening (the screen still judges the content)."""
        message_id = item.get("id")
        if not message_id:
            return None
        res = run_node([self.gmailjs, f"--auth={message_id}"])
        if res.returncode != 0:
            return None
        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            return None
        return parse_email_auth(data.get("fromAddress") or item.get("fromAddress"),
                                data.get("authenticationResults"), data.get("receivedSpf"))

    def _fetch_body(self, item):
        """The new-message text, for relay-correspondent extraction when a relay's name isn't already in
        the subject/preview. Only reached for a recognized relay sender whose cheap fields missed, so this
        per-item body fetch stays rare; reuses the same quote-stripped excerpt triage sees."""
        message_id = item.get("id")
        if not message_id:
            return ""
        show = run_node([self.gmailjs, f"--show={message_id}"])
        return self._new_message_excerpt(show.stdout) if show.returncode == 0 else ""

    @staticmethod
    def _new_message_excerpt(raw, limit=1500):
        """From a `gmail.js --show` dump (Subject/From/To/Cc/Date header lines, then the body), keep the
        header lines and the new message text, dropping the quoted reply chain. The To/Cc lines are kept
        on purpose — they're how triage tells a message aimed at Russell from one where he's only a CC.
        The quote chain begins at the first `On <date> … wrote:` attribution or the first `>`-quoted line."""
        kept = []
        for line in (raw or "").splitlines():
            s = line.strip()
            if s.startswith(">") or re.match(r"^On .*wrote:\s*$", s):
                break
            kept.append(line)
        return "\n".join(kept).strip()[:limit]

    def still_in_inbox_ids(self):
        res = run_node([self.gmailjs, "--list-inbox", "--json", "--top=500"])
        if res.returncode != 0:
            return None
        try:
            msgs = json.loads(res.stdout or "[]")
        except ValueError:
            return None
        return {m["id"] for m in msgs if m.get("id")}

    def clear(self, item):
        """Archive an fyi/junk message at triage time (the provider CLEAR: `gmail.js --archive` removes it
        from the inbox, keeping it in [Gmail]/All Mail - reversible and still searchable). Returns True on
        success, False on failure - see ProviderBase.clear for why this is safe for the poller to call."""
        res = run_node([self.gmailjs, f"--archive={item['id']}"])
        return res.returncode == 0

    def stable_id(self, item):
        # Timestamp to the second so two messages from the same sender with the same opening subject in
        # the same minute don't collide and silently drop one. Mirrors the outlook-graph scheme.
        digits = "".join(c for c in (item.get("received") or "") if c.isdigit())
        recv = f"{digits[:8]}-{digits[8:14]}"  # YYYYMMDD-HHMMSS
        sender = slug((item.get("fromAddress") or item.get("from") or "").split("@")[0])
        subj3 = slug("-".join((item.get("subject") or "").split()[:3]))
        return f"{self.name}-{recv}-{sender}-{subj3}".strip("-")[:72]

    def capture(self, item, iid, runtime_dir):
        items_dir = os.path.join(runtime_dir, "items")
        os.makedirs(items_dir, exist_ok=True)
        email_file = os.path.join(items_dir, f"{iid}.email.md")
        message_id = item["id"]
        show = run_node([self.gmailjs, f"--show={message_id}"])
        body = show.stdout if show.returncode == 0 else "(could not load body)"
        web_link = self._web_link(message_id)
        with open(email_file, "w", encoding="utf-8") as f:
            f.write(f"# {item.get('subject')}\n\nFrom: {item.get('from')}\nReceived: {item.get('received')}\n"
                    f"Link: {web_link}\nMessageId: {message_id}\n\n---\n\n{body}\n")
        record = {
            "id": iid, "source": self.name, "triage": item["_bucket"], "kind": item.get("_kind"),
            "from": item.get("from"), "subject": item.get("subject"), "received": item.get("received"),
            "snippet": item.get("preview"), "url": web_link, "messageId": message_id,
            "correspondent": item.get("_correspondent"), "emailFile": email_file,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        json_file = os.path.join(items_dir, f"{iid}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        return json_file
