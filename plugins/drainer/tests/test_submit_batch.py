"""Test for submit_batch.py: a re-staged submission matches its saved batch only when every page value
and every uploaded file is identical, and a capture that read nothing is refused.

Run directly:
    python plugins/drainer/tests/test_submit_batch.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "drainer", "scripts"))
import submit_batch  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(command, manifest, pages, files=()):
    argv = [command, "--manifest", manifest]
    for page in pages:
        argv += ["--page", page]
    for path in files:
        argv += ["--file", path]
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")
    with contextlib.redirect_stdout(out):
        rc = submit_batch.main(argv)
    out.flush()
    return rc, out.buffer.getvalue().decode("utf-8").strip()


with tempfile.TemporaryDirectory() as tmp:
    manifest = os.path.join(tmp, "batch.json")
    form = write(os.path.join(tmp, "page1.json"), json.dumps({"name": "Zoë Example", "year": "2020"}))
    pay = write(os.path.join(tmp, "page2.json"), json.dumps({"amount": "$25.00"}))
    upload = write(os.path.join(tmp, "return.pdf"), "original")

    rc, _ = run("save", manifest, [form, pay], [upload])
    check("save succeeds", rc, 0)

    reordered = write(os.path.join(tmp, "page1b.json"), json.dumps({"year": "2020", "name": "Zoë Example"}))
    check("same values and files match, key order aside",
          run("check", manifest, [reordered, pay], [upload]), (0, "MATCH"))

    changed = write(os.path.join(tmp, "page2b.json"), json.dumps({"amount": "$50.00", "fee": "$1.00"}))
    check("changed and added values are named",
          run("check", manifest, [form, changed], [upload]),
          (1, 'page 2, amount: "$25.00" -> "$50.00"\npage 2, fee: (absent) -> "$1.00"'))

    rc, out = run("check", manifest, [form], [upload])
    check("a missing page differs", (rc, out), (1, "page count: 2 -> 1"))
    rc, _ = run("check", manifest, [pay, form], [upload])
    check("page order matters", rc, 1)

    write(upload, "edited")
    check("edited file differs",
          run("check", manifest, [form, pay], [upload]), (1, "file return.pdf: differs from the batch"))
    write(upload, "original")
    check("a dropped upload differs",
          run("check", manifest, [form, pay]), (1, "file return.pdf: differs from the batch"))
    check("an extra copy of an upload differs",
          run("check", manifest, [form, pay], [upload, upload]),
          (1, "file return.pdf: differs from the batch"))

    empty = write(os.path.join(tmp, "empty.json"), "{}")
    rc, _ = run("save", os.path.join(tmp, "none.json"), [empty])
    check("a page with no values is refused", (rc, os.path.exists(os.path.join(tmp, "none.json"))), (1, False))
    rc, _ = run("check", os.path.join(tmp, "unsaved.json"), [form])
    check("checking without a saved batch fails", rc, 1)

print()
if failures:
    print(f"Verification FAILED - {len(failures)} errors")
    sys.exit(1)
print("Verification passed ✅")
