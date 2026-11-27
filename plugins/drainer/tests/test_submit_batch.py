"""Test for submit_batch.py: a re-staged submission matches its saved batch only when every page value
and every uploaded file is identical, and a capture that proves nothing is refused.

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

    def page(name, values):
        return write(os.path.join(tmp, name), json.dumps(values))

    def restaged(name, values):
        """A page captured after the batch was saved."""
        path = page(name, values)
        later = os.path.getmtime(manifest) + 10
        os.utime(path, (later, later))
        return path

    form = page("form.json", {"name": "Zoë Example", "year": "2020"})
    pay = page("pay.json", {"amount": "$25.00"})
    upload = write(os.path.join(tmp, "return.pdf"), "original")
    schedule = write(os.path.join(tmp, "schedule.pdf"), "schedule")

    rc, _ = run("save", manifest, [form, pay], [upload, schedule])
    check("save succeeds", rc, 0)

    os.utime(form, (0, 0))
    os.utime(pay, (0, 0))
    rc, out = run("check", manifest, [form, pay], [upload, schedule])
    check("pages captured before the batch are refused", (rc, "captured before the batch" in out), (1, True))

    form = restaged("form2.json", {"year": "2020", "name": "Zoë Example"})
    pay = restaged("pay2.json", {"amount": "$25.00"})
    both = [upload, schedule]
    check("same values and files match, key order aside",
          run("check", manifest, [form, pay], both), (0, "MATCH"))

    changed = restaged("pay3.json", {"amount": "$50.00", "fee": "$1.00"})
    check("changed and added values are named",
          run("check", manifest, [form, changed], both),
          (1, 'page 2, amount: "$25.00" -> "$50.00"\npage 2, fee: (absent) -> "$1.00"'))

    check("a missing page differs", run("check", manifest, [form], both), (1, "page count: 2 -> 1"))
    rc, _ = run("check", manifest, [pay, form], both)
    check("page order matters", rc, 1)

    rc, out = run("check", manifest, [form, pay], [schedule, upload])
    check("uploads in a different order differ", (rc, out.startswith("files: return.pdf (")), (1, True))
    rc, _ = run("check", manifest, [form, pay], [upload])
    check("a dropped upload differs", rc, 1)
    write(upload, "edited")
    rc, out = run("check", manifest, [form, pay], both)
    check("an edited file differs", (rc, out.startswith("files: ")), (1, True))

    for label, values in (("empty", {}), ("blank", {"amount": ""}), ("nested", {"url": "u", "fields": {}})):
        target = os.path.join(tmp, f"{label}-batch.json")
        rc, _ = run("save", target, [page(f"{label}.json", values)])
        check(f"{label} capture is refused", (rc, os.path.exists(target)), (1, False))
    rc, _ = run("check", os.path.join(tmp, "unsaved.json"), [form])
    check("checking without a saved batch fails", rc, 1)

print()
if failures:
    print(f"Verification FAILED - {len(failures)} errors")
    sys.exit(1)
print("Verification passed ✅")
