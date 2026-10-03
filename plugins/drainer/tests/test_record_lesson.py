"""Test for record-lesson.py: one JSON line per call in the month's ledger file, the exact schema,
the item id and source read from the captured item json, and `pr` null when absent.

Run directly:
    python plugins/drainer/tests/test_record_lesson.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "skills", "drainer", "scripts", "record-lesson.py")

spec = importlib.util.spec_from_file_location("record_lesson", SCRIPT)
record_lesson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_lesson)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def run(argv, env_dir):
    os.environ["WORKER_LESSONS_DIR"] = env_dir
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-123"
    record_lesson.main(argv)
    files = sorted(os.listdir(env_dir))
    with open(os.path.join(env_dir, files[0]), encoding="utf-8") as f:
        return files, [json.loads(line) for line in f]


with tempfile.TemporaryDirectory() as tmp:
    item = os.path.join(tmp, "abc123.json")
    with open(item, "w", encoding="utf-8") as f:
        json.dump({"source": "email"}, f)
    ledger = os.path.join(tmp, "nested", "lessons")

    files, rows = run(["--trigger", "dead-end", "--what", "w", "--target", "t", "--change", "c",
                       "--item", item], ledger)
    row = rows[0]
    check("monthly file name", files[0].startswith("log-") and files[0].endswith(".jsonl"), True)
    check("schema keys", list(row), ["ts", "session_id", "iid", "source", "trigger", "what",
                                     "target", "change", "pr"])
    check("iid from item file", row["iid"], "abc123")
    check("source from item json", row["source"], "email")
    check("session id from env", row["session_id"], "sess-123")
    check("pr null when absent", row["pr"], None)
    check("ts carries an offset", row["ts"][-6] in "+-", True)

    files, rows = run(["--trigger", "correction", "--what", "w2", "--target", "t", "--change", "c",
                       "--pr", "https://example.test/pr/1"], ledger)
    check("second call appends", len(rows), 2)
    check("pr recorded", rows[1]["pr"], "https://example.test/pr/1")
    check("no item gives null iid and source", (rows[1]["iid"], rows[1]["source"]), (None, None))

    try:
        record_lesson.main(["--trigger", "bogus", "--what", "w", "--target", "t", "--change", "c"])
        check("bad trigger rejected", False, True)
    except SystemExit:
        check("bad trigger rejected", True, True)

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nall passed")
