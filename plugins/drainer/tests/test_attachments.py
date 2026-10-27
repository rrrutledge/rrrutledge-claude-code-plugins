"""Tests for attachments as part of the captured email: provider_base.parse_attachments reading the helpers'
`Attachments:` header line, attachment_text / attachments_section turning saved files into readable text,
both email adapters' capture saving the files and appending that section to `<id>.email.md`, and the
outlook-graph triage text carrying the attachment line. run_node is stubbed (the stub "downloads" by
writing fixture files into --out-dir), so no network or credentials are needed.

Run directly:
    python plugins/drainer/tests/test_attachments.py
"""
import importlib.util
import os
import sys
import tempfile
import zipfile
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
SHOW = (f"Subject: September Invoice\nFrom: Olive <olive@example.com>\nTo: Russ <russ@example.com>\n"
        f"Attachments: {DOCX} | logo.png (image/png)\n\n"
        "I just realised that I forgot to send you my invoice for September.")
PLAIN_SHOW = "Subject: Hi\nFrom: Bob <bob@x.com>\nTo: Russ <russ@example.com>\n\nAttachments: quoted.pdf (application/pdf)"


def write_docx(path, paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document><w:body>{body}</w:body></w:document>')


def fake_download(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    write_docx(os.path.join(out_dir, "ISC September 26 Invoice.docx"), ["Days worked for September", "Total &amp; due: 6"])
    with open(os.path.join(out_dir, "logo.png"), "wb") as f:
        f.write(b"\x89PNG\r\n")


# --- parse_attachments --------------------------------------------------------------------------
print("parse_attachments")
check("names every file on the header line", provider_base.parse_attachments(SHOW), [DOCX, "logo.png (image/png)"])
check("an Attachments: line in the body is ignored", provider_base.parse_attachments(PLAIN_SHOW), [])
check("empty input -> empty", provider_base.parse_attachments(""), [])

# --- attachment_text / attachments_section -----------------------------------------------------
print("\nattachment_text / attachments_section")
with tempfile.TemporaryDirectory() as d:
    fake_download(d)
    docx_text = provider_base.attachment_text(os.path.join(d, "ISC September 26 Invoice.docx"))
    check("docx text is extracted paragraph by paragraph", docx_text.splitlines(),
          ["Days worked for September", "Total & due: 6"])
    check("an image has no extractable text", provider_base.attachment_text(os.path.join(d, "logo.png")), None)
    section = provider_base.attachments_section(d)
    check("section heads with ## Attachments", section.lstrip().startswith("## Attachments"), True)
    check("section carries the docx text", "Days worked for September" in section, True)
    check("section points at the saved image", f"Saved: {os.path.join(d, 'logo.png')}" in section, True)
check("no directory -> no section", provider_base.attachments_section(os.path.join(HERE, "no-such-dir")), "")


# --- capture ------------------------------------------------------------------------------------
def _load_adapter(name):
    s = importlib.util.spec_from_file_location(f"{name}_adapter_att", os.path.join(PROVIDERS, f"{name}-adapter.py"))
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def _stub_node(show_text, calls):
    def run(args, **kw):
        calls.append(args)
        out_dir = next((a.split("=", 1)[1] for a in args if a.startswith("--out-dir=")), None)
        if out_dir:
            fake_download(out_dir)
            return SimpleNamespace(returncode=0, stdout="Saved", stderr="")
        return SimpleNamespace(returncode=0, stdout=show_text, stderr="")
    return run


def _provider(name, script_attr, show_text, calls):
    mod = _load_adapter(name)
    mod.run_node = _stub_node(show_text, calls)
    p = mod.Provider.__new__(mod.Provider)
    setattr(p, script_attr, "helper.js")
    return p


def _capture(name, script_attr, show_text, item):
    calls = []
    p = _provider(name, script_attr, show_text, calls)
    with tempfile.TemporaryDirectory() as d:
        p.capture(item, "item-1", d)
        with open(os.path.join(d, "items", "item-1.email.md"), encoding="utf-8") as f:
            email_md = f.read()
    return email_md, calls


base_item = {"id": "<msg-1@x>", "uid": 7, "_bucket": "needs-you", "from": "Olive <olive@example.com>",
             "subject": "September Invoice", "received": "2026-10-27T15:00:00Z"}

for adapter, attr in (("outlook-graph", "mailjs"), ("gmail", "gmailjs")):
    print(f"\n{adapter} capture")
    email_md, calls = _capture(adapter, attr, SHOW, dict(base_item))
    check("downloads the attachments once", sum(any(a.startswith("--out-dir=") for a in c) for c in calls), 1)
    check("email.md carries the attachment section", "## Attachments" in email_md, True)
    check("email.md carries the invoice text", "Days worked for September" in email_md, True)
    check("the section follows the body", email_md.index("forgot to send") < email_md.index("## Attachments"), True)

    email_md, calls = _capture(adapter, attr, PLAIN_SHOW, dict(base_item))
    check("no attachments -> no download", any(any(a.startswith("--out-dir=") for a in c) for c in calls), False)
    check("no attachments -> no section", "## Attachments" in email_md, False)

# --- outlook-graph triage text ------------------------------------------------------------------
print("\noutlook-graph triage_text")
calls = []
graph = _provider("outlook-graph", "mailjs", SHOW, calls)
preview = "Hi Russ, I just realised that I forgot to send you my invoice for September."
check("a flagged message heads its preview with the attachment line",
      graph.triage_text({"id": "m1", "preview": preview, "hasAttachments": True}),
      f"Attachments: {DOCX} | logo.png (image/png)\n\n{preview}")
calls.clear()
check("an unflagged message is its preview alone",
      graph.triage_text({"id": "m2", "preview": preview, "hasAttachments": False}), preview)
check("an unflagged message makes no extra fetch", calls, [])

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
