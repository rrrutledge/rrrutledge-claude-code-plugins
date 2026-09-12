"""Tests for the gmail adapter's per-item reads running over IMAP by INBOX UID (off the REST quota):
triage_text / _fetch_body / screen_signal / capture all shell out to gmail.js's --show-imap / --auth-imap
keyed on the item's `uid`, and every failure path (nonzero exit, bad JSON, missing uid) keeps its prior
fallback. clear() staying on REST by Message-ID is covered by test_poll_clear.py.

Run directly:
    python plugins/drainer/tests/test_gmail_imap_reads.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")

sys.path.insert(0, SCRIPTS)  # the adapter imports its siblings (provider_base) by bare name


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gmail = _load("gmail_adapter", os.path.join(PROVIDERS, "gmail-adapter.py"))

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class _Res:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def stub_run_node(result):
    """Replace gmail.run_node with one that records its args and returns `result`. Returns the calls list."""
    calls = []
    gmail.run_node = lambda args, **kw: (calls.append(args) or result)
    return calls


def new_provider():
    p = gmail.Provider.__new__(gmail.Provider)  # skip __init__ (which locates gmail.js)
    p.gmailjs = "GMAILJS"
    return p


# A --show-imap dump: header lines, a blank line, the new text, then a quoted reply chain triage strips.
SHOW_DUMP = (
    "Subject: Contract question\n"
    "From: Jane Q <jane@example.com>\n"
    "To: russ@innersourcecommons.org\n"
    "Date: 2026-09-12T14:00:00.000Z\n"
    "\n"
    "Can you confirm the terms by Friday?\n"
    "On Thu, someone wrote:\n"
    "> the earlier message\n"
)

ITEM = {"id": "<abc@mail.gmail.com>", "uid": 4242, "preview": "light preview"}


print("triage_text: fetches by --show-imap=<uid> and returns the quote-stripped new content")
p = new_provider()
calls = stub_run_node(_Res(0, SHOW_DUMP))
text = p.triage_text(ITEM)
check("shelled out to gmail.js --show-imap=<uid>", calls and calls[0], ["GMAILJS", "--show-imap=4242"])
check("quoted reply chain stripped, header + new text kept", "the earlier message" not in text and
      "Can you confirm the terms by Friday?" in text, True)

print("\ntriage_text: falls back to the preview on a fetch failure and when the uid is missing")
p = new_provider()
stub_run_node(_Res(1))
check("nonzero exit -> preview fallback", p.triage_text(ITEM), "light preview")
nocall = stub_run_node(_Res(0, SHOW_DUMP))
check("no uid -> preview fallback, no fetch attempted", p.triage_text({"id": "<x>", "preview": "light preview"}),
      "light preview")
check("  ... and run_node was never called", nocall, [])


print("\nscreen_signal: fetches by --auth-imap=<uid> and parses the auth JSON into the screen summary")
p = new_provider()
auth_json = json.dumps({
    "from": "Jane Q <jane@example.com>", "fromAddress": "jane@example.com",
    "authenticationResults": ["mx.google.com; dkim=pass header.d=example.com; spf=pass "
                              "smtp.mailfrom=jane@example.com; dmarc=pass"],
    "receivedSpf": [],
})
calls = stub_run_node(_Res(0, auth_json))
sig = p.screen_signal(ITEM)
check("shelled out to gmail.js --auth-imap=<uid>", calls and calls[0], ["GMAILJS", "--auth-imap=4242"])
check("auth JSON parsed into a screen summary dict", isinstance(sig, dict) and sig.get("dmarc"), "pass")

print("\nscreen_signal: None on fetch failure, malformed JSON, and a missing uid")
p = new_provider()
stub_run_node(_Res(1))
check("nonzero exit -> None", p.screen_signal(ITEM), None)
stub_run_node(_Res(0, "not json"))
check("malformed JSON -> None", p.screen_signal(ITEM), None)
nocall = stub_run_node(_Res(0, auth_json))
check("no uid -> None, no fetch attempted", p.screen_signal({"id": "<x>"}), None)
check("  ... and run_node was never called", nocall, [])


print("\n_fetch_body: fetches by --show-imap=<uid>; empty string on failure or missing uid")
p = new_provider()
calls = stub_run_node(_Res(0, SHOW_DUMP))
body = p._fetch_body(ITEM)
check("shelled out to gmail.js --show-imap=<uid>", calls and calls[0], ["GMAILJS", "--show-imap=4242"])
check("returns the quote-stripped excerpt", "Can you confirm the terms by Friday?" in body, True)
stub_run_node(_Res(1))
check("nonzero exit -> empty string", p._fetch_body(ITEM), "")
check("no uid -> empty string", p._fetch_body({"id": "<x>"}), "")


print("\ncapture: body fetched via --show-imap=<uid>; link + messageId still come from the Message-ID")
p = new_provider()
calls = stub_run_node(_Res(0, SHOW_DUMP))
rt = tempfile.mkdtemp(prefix="gmail-capture-")
item = {**ITEM, "_bucket": "fyi", "_kind": "read", "from": "Jane Q <jane@example.com>",
        "subject": "Contract question", "received": "2026-09-12T14:00:00.000Z", "_correspondent": "jane@example.com"}
json_file = p.capture(item, "item-1", rt)
check("shelled out to gmail.js --show-imap=<uid>", calls and calls[0], ["GMAILJS", "--show-imap=4242"])
with open(json_file, encoding="utf-8") as f:
    rec = json.load(f)
check("record keeps the RFC822 Message-ID", rec["messageId"], "<abc@mail.gmail.com>")
check("web link is built from the Message-ID (rfc822msgid search)", "rfc822msgid" in rec["url"], True)
with open(rec["emailFile"], encoding="utf-8") as f:
    email_md = f.read()
check("captured email file carries the fetched body", "Can you confirm the terms by Friday?" in email_md, True)

print("\ncapture: a missing uid or fetch failure writes the (could not load body) sentinel, still captures")
p = new_provider()
stub_run_node(_Res(1))
json_file = p.capture(item, "item-2", rt)
with open(json.load(open(json_file, encoding="utf-8"))["emailFile"], encoding="utf-8") as f:
    check("fetch failure -> (could not load body) sentinel in the email file", "(could not load body)" in f.read(), True)
nocall = stub_run_node(_Res(0, SHOW_DUMP))
json_file = p.capture({**item, "uid": None}, "item-3", rt)
with open(json.load(open(json_file, encoding="utf-8"))["emailFile"], encoding="utf-8") as f:
    check("no uid -> (could not load body), no fetch attempted", "(could not load body)" in f.read(), True)
check("  ... and run_node was never called for the no-uid capture", nocall, [])


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
