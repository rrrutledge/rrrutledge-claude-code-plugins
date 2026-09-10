"""Test for scripts/find-orphans.py.

Runs against the real ~/.claude/session-mgr/live-sessions.json using unique throwaway
session ids (seeded and removed within each test, cleaned up on failure).
Run directly: python plugins/session-mgr/tests/test_find_orphans.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
FIND_ORPHANS = os.path.join(PLUGIN_ROOT, "skills", "resume-sessions", "scripts", "find-orphans.py")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

spec = importlib.util.spec_from_file_location("find_orphans", FIND_ORPHANS)
find_orphans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find_orphans)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def seed_registry_entry(session_id, cwd="C:/fake/repo"):
    registry = find_orphans.load_registry()
    registry[session_id] = {"cwd": cwd, "started_at": "2026-07-20T00:00:00"}
    find_orphans.save_registry(registry)


def remove_registry_entry(session_id):
    registry = find_orphans.load_registry()
    registry.pop(session_id, None)
    find_orphans.save_registry(registry)


def test_confirmed_orphan_returned():
    print("test: a registry entry with no matching live process is returned as an orphan")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    seed_registry_entry(session_id)
    orig_active = find_orphans.active_session_ids
    find_orphans.active_session_ids = lambda: set()  # nothing running
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("orphan present", session_id in ids, f"got {ids}")
        match = next((o for o in orphans if o["session_id"] == session_id), None)
        check("cwd carried through", bool(match) and match["cwd"] == "C:/fake/repo")
    finally:
        find_orphans.active_session_ids = orig_active
        remove_registry_entry(session_id)


def test_live_process_excluded():
    print("test: a registry entry whose session IS currently running is excluded")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    seed_registry_entry(session_id)
    orig_active = find_orphans.active_session_ids
    find_orphans.active_session_ids = lambda: {session_id}  # pretend it's running
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("not returned", session_id not in ids, f"got {ids}")
    finally:
        find_orphans.active_session_ids = orig_active
        remove_registry_entry(session_id)


def test_self_closed_tail_excluded_and_pruned():
    print("test: a session whose transcript shows an EXECUTED self-close is excluded and pruned")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    seed_registry_entry(session_id)
    project_dir = os.path.join(PROJECTS_DIR, f"test-find-orphans-{uuid.uuid4()}")
    os.makedirs(project_dir, exist_ok=True)
    transcript = os.path.join(project_dir, f"{session_id}.jsonl")
    with open(transcript, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": 'python "C:/x/close-session.py"'}}]}}) + "\n")
    orig_active = find_orphans.active_session_ids
    find_orphans.active_session_ids = lambda: set()
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("excluded from results", session_id not in ids, f"got {ids}")
        registry = find_orphans.load_registry()
        check("pruned from registry", session_id not in registry)
    finally:
        find_orphans.active_session_ids = orig_active
        remove_registry_entry(session_id)
        os.remove(transcript)
        os.rmdir(project_dir)


def test_mention_only_not_pruned():
    print("test: a session that only MENTIONS close-session.py (never ran it) is still an orphan")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    seed_registry_entry(session_id)
    project_dir = os.path.join(PROJECTS_DIR, f"test-find-orphans-{uuid.uuid4()}")
    os.makedirs(project_dir, exist_ok=True)
    transcript = os.path.join(project_dir, f"{session_id}.jsonl")
    with open(transcript, "w", encoding="utf-8") as f:
        # A real tab editing the drainer: its recent context references close-session.py as text,
        # but no command was executed. It must NOT be pruned, so an abrupt close still resumes it.
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The worker's last step runs close-session.py / taskkill /T /F."}]}}) + "\n")
    orig_active = find_orphans.active_session_ids
    find_orphans.active_session_ids = lambda: set()
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("returned as orphan (not pruned)", session_id in ids, f"got {ids}")
        registry = find_orphans.load_registry()
        check("still in registry", session_id in registry)
    finally:
        find_orphans.active_session_ids = orig_active
        remove_registry_entry(session_id)
        os.remove(transcript)
        os.rmdir(project_dir)


def test_bare_launch_excluded_via_live_pid():
    print("test: a registry entry unmatched by session id, but whose recorded pid is still "
          "a live claude.exe, is excluded (the bare `claude` launch case)")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    registry = find_orphans.load_registry()
    registry[session_id] = {"cwd": "C:/fake/repo", "started_at": "2026-07-20T00:00:00",
                             "pid": os.getpid()}
    find_orphans.save_registry(registry)
    orig_active = find_orphans.active_session_ids
    orig_pid_check = find_orphans.pid_still_claude
    find_orphans.active_session_ids = lambda: set()  # session id matches nothing
    find_orphans.pid_still_claude = lambda pid: pid == os.getpid()  # but its pid is alive
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("not returned", session_id not in ids, f"got {ids}")
    finally:
        find_orphans.active_session_ids = orig_active
        find_orphans.pid_still_claude = orig_pid_check
        remove_registry_entry(session_id)


def test_dead_pid_still_flagged_as_orphan():
    print("test: a registry entry with a recorded pid that's no longer alive is still an orphan")
    session_id = f"test-find-orphans-{uuid.uuid4()}"
    registry = find_orphans.load_registry()
    registry[session_id] = {"cwd": "C:/fake/repo", "started_at": "2026-07-20T00:00:00",
                             "pid": 999999}  # not a real running pid
    find_orphans.save_registry(registry)
    orig_active = find_orphans.active_session_ids
    find_orphans.active_session_ids = lambda: set()
    try:
        orphans = find_orphans.find_confirmed_orphans()
        ids = {o["session_id"] for o in orphans}
        check("orphan present", session_id in ids, f"got {ids}")
    finally:
        find_orphans.active_session_ids = orig_active
        remove_registry_entry(session_id)


def test_pid_still_claude_false_for_missing_pid():
    print("test: pid_still_claude is False for a None/missing pid")
    check("None pid", find_orphans.pid_still_claude(None) is False)
    check("zero pid", find_orphans.pid_still_claude(0) is False)


def test_cli_prints_json():
    print("test: run as a script prints a JSON array to stdout")
    result = subprocess.run([sys.executable, FIND_ORPHANS], capture_output=True, text=True)
    check("exit code 0", result.returncode == 0, f"got {result.returncode}: {result.stderr}")
    try:
        parsed = json.loads(result.stdout)
        check("stdout is a JSON list", isinstance(parsed, list), result.stdout[:200])
    except json.JSONDecodeError:
        check("stdout is valid JSON", False, result.stdout[:200])


if __name__ == "__main__":
    test_confirmed_orphan_returned()
    test_live_process_excluded()
    test_self_closed_tail_excluded_and_pruned()
    test_mention_only_not_pruned()
    test_bare_launch_excluded_via_live_pid()
    test_dead_pid_still_flagged_as_orphan()
    test_pid_still_claude_false_for_missing_pid()
    test_cli_prints_json()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
