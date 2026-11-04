"""Tests for the digest's waiting-on-you list: run-digest's waiting_sessions picks the running background
sessions blocked on Russell long enough to list, format_waiting renders them, and write_seed carries the
block or tells the session to skip step 1c. The job records are stubbed, so nothing reads the real
~/.claude state or runs claude.

Run directly:
    python plugins/drainer/tests/test_waiting_sessions.py
"""
import datetime
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

digest_spec = importlib.util.spec_from_file_location("run_digest", os.path.join(SCRIPTS, "run-digest.py"))
digest = importlib.util.module_from_spec(digest_spec)
digest_spec.loader.exec_module(digest)

failures = []
NOW = datetime.datetime(2026, 11, 4, 23, 0, tzinfo=datetime.timezone.utc)


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def hours_ago(h):
    return (NOW - datetime.timedelta(hours=h)).isoformat().replace("+00:00", "Z")


JOBS = {
    "69bb0c56": {"updatedAt": hours_ago(30), "name": "Trello: Context Risk Index",
                 "needs": "Pick A (staged Gmail reply) or B (doc comments)"},
    "aaaa0001": {"updatedAt": hours_ago(2), "needs": "too fresh to list"},
    "aaaa0002": {"updatedAt": hours_ago(50), "detail": "only a detail recorded"},
    "aaaa0003": {"updatedAt": hours_ago(40), "needs": "busy now"},
    "aaaa0004": {"updatedAt": hours_ago(40), "needs": "retired, no process"},
    "aaaa0005": {"updatedAt": hours_ago(40), "needs": "done, not blocked"},
}
AGENTS = [
    {"id": "69bb0c56", "kind": "background", "pid": 13472, "status": "idle", "state": "blocked",
     "name": "Trello: Context Risk Index - send review feedback"},
    {"id": "aaaa0001", "kind": "background", "pid": 1, "status": "idle", "state": "blocked", "name": "fresh"},
    {"id": "aaaa0002", "kind": "background", "pid": 2, "status": "idle", "state": "blocked", "name": "oldest"},
    {"id": "aaaa0003", "kind": "background", "pid": 3, "status": "busy", "state": "blocked", "name": "busy"},
    {"id": "aaaa0004", "kind": "background", "state": "blocked", "name": "retired"},
    {"id": "aaaa0005", "kind": "background", "pid": 5, "status": "idle", "state": "done", "name": "done"},
    {"id": None, "kind": "interactive", "pid": 6, "status": "idle", "name": "interactive"},
]

orig_find_job = digest.bg_session.find_job
digest.bg_session.find_job = lambda short: (None, JOBS.get(short))
try:
    print("waiting_sessions")
    got = digest.waiting_sessions(AGENTS, now=NOW)
    check("only running blocked idle sessions past the threshold, oldest first",
          [s["id"] for s in got], ["aaaa0002", "69bb0c56"])
    check("the ask comes from the job record", got[1]["needs"], "Pick A (staged Gmail reply) or B (doc comments)")
    check("detail stands in when no ask is recorded", got[0]["needs"], "only a detail recorded")
    check("an unreadable list is None, not empty", digest.waiting_sessions(None, now=NOW), None)

    print("\nformat_waiting")
    block = digest.format_waiting(got)
    check("each session carries its attach command", "`claude attach 69bb0c56`" in block, True)
    check("each session carries its ask", "asks: Pick A" in block, True)
    check("the header counts the sessions", block.startswith("Waiting on you (2 running session(s)"), True)
    check("nothing waiting renders as None", digest.format_waiting([]), None)
    check("an unreadable list renders as unavailable", "unavailable" in digest.format_waiting(None), True)

    print("\nwrite_seed")
    with tempfile.TemporaryDirectory() as rt:
        with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, block), encoding="utf-8") as f:
            listed = f.read()
        with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, None), encoding="utf-8") as f:
            empty = f.read()
    check("the seed carries the block for step 1c", "digest-core step 1c" in listed and "claude attach" in listed,
          True)
    check("with nothing waiting the seed says to skip step 1c", "skip digest-core step 1c" in empty, True)
finally:
    digest.bg_session.find_job = orig_find_job

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
