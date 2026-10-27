"""Tests for email attachment visibility: provider_base.parse_attachments reading the helpers' `Attachments:`
header line, the ProviderBase.triage_signal `hasAttachments` flag, and both email adapters' capture writing
the attachment names into `<id>.email.md` and `<id>.json`. run_node is stubbed, so no network or
credentials are needed.

Run directly:
    python plugins/drainer/tests/test_attachments.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import provider_base  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


DOCX = "ISC September 26 Invoice.docx (application/vnd.openxmlformats-officedocument.wordprocessingml.document)"
GRAPH_SHOW = (f"Subject: September Invoice\nFrom: Olive <olive@example.com>\nTo: Russ <russ@example.com>\n"
              f"Attachments: {DOCX} | timesheet.pdf (application/pdf)\n\n"
              "I just realised that I forgot to send you my invoice for September.")
PLAIN_SHOW = "Subject: Hi\nFrom: Bob <bob@x.com>\nTo: Russ <russ@example.com>\n\nAttachments: quoted.pdf (application/pdf)"

# --- parse_attachments --------------------------------------------------------------------------
print("parse_attachments")
check("names every file on the header line", provider_base.parse_attachments(GRAPH_SHOW),
      [DOCX, "timesheet.pdf (application/pdf)"])
check("an Attachments: line in the body is ignored", provider_base.parse_attachments(PLAIN_SHOW), [])
check("no line -> empty", provider_base.parse_attachments("Subject: x\n\nbody"), [])
check("empty input -> empty", provider_base.parse_attachments(""), [])

# --- triage_signal ------------------------------------------------------------------------------
print("\nProviderBase.triage_signal hasAttachments")
base = provider_base.ProviderBase()
check("an attachment is flagged", base.triage_signal({"hasAttachments": True}), {"hasAttachments": True})
check("a self-note with an attachment carries both flags",
      base.triage_signal({"fromMe": True, "toMe": True, "hasAttachments": True}),
      {"selfEmail": True, "hasAttachments": True})
check("no attachment carries no signal", base.triage_signal({"hasAttachments": False}), None)


# --- capture ------------------------------------------------------------------------------------
def _load_adapter(name):
    s = importlib.util.spec_from_file_location(f"{name}_adapter_att", os.path.join(PROVIDERS, f"{name}-adapter.py"))
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def _capture(name, script_attr, show_text, item):
    mod = _load_adapter(name)
    mod.run_node = lambda args, **kw: SimpleNamespace(returncode=0, stdout=show_text, stderr="")
    p = mod.Provider.__new__(mod.Provider)
    setattr(p, script_attr, "helper.js")
    with tempfile.TemporaryDirectory() as d:
        json_file = p.capture(item, "item-1", d)
        with open(json_file, encoding="utf-8") as f:
            record = json.load(f)
        with open(record["emailFile"], encoding="utf-8") as f:
            email_md = f.read()
    return record, email_md


base_item = {"id": "<msg-1@x>", "uid": 7, "_bucket": "needs-you", "from": "Olive <olive@example.com>",
             "subject": "September Invoice", "received": "2026-10-27T15:00:00Z", "hasAttachments": True}

for adapter, attr in (("outlook-graph", "mailjs"), ("gmail", "gmailjs")):
    print(f"\n{adapter} capture")
    record, email_md = _capture(adapter, attr, GRAPH_SHOW, dict(base_item))
    check("json records hasAttachments", record.get("hasAttachments"), True)
    check("json lists the attachment names", record.get("attachments"), [DOCX, "timesheet.pdf (application/pdf)"])
    header = email_md.split("---", 1)[0]
    check("email.md header names the attachments",
          f"Attachments: {DOCX} | timesheet.pdf (application/pdf)" in header, True)

    record, email_md = _capture(adapter, attr, PLAIN_SHOW, {**base_item, "hasAttachments": False})
    check("no attachments -> hasAttachments false", record.get("hasAttachments"), False)
    check("no attachments -> empty list", record.get("attachments"), [])
    check("no attachments -> no header line", "Attachments:" in email_md.split("---", 1)[0], False)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
