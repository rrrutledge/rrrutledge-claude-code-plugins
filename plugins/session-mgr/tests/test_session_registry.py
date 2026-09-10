"""Test for hooks/session_registry.py.

Runs against the real ~/.claude/session-mgr/live-sessions.json using unique throwaway
session ids (seeded and removed within each test, cleaned up on failure).
Run directly: python plugins/session-mgr/tests/test_session_registry.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
REGISTRY_HOOK = os.path.join(PLUGIN_ROOT, "hooks", "session_registry.py")
REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")

spec = importlib.util.spec_from_file_location("session_registry", REGISTRY_HOOK)
session_registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session_registry)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def registry_entry(session_id):
    if not os.path.exists(REGISTRY_PATH):
        return None
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f).get(session_id)


def fire_event(event, session_id, reason=None, host_pid=None):
    payload = {"hook_event_name": event, "session_id": session_id, "cwd": os.getcwd()}
    if reason is not None:
        payload["reason"] = reason
    # The hook reads CLAUDE_HOST_PID from its own environment at SessionStart. Control it per
    # call so the test is deterministic regardless of whether the test runner itself was launched
    # from an interactive tab (which would carry a real host pid).
    env = dict(os.environ)
    if host_pid is not None:
        env["CLAUDE_HOST_PID"] = host_pid
    else:
        env.pop("CLAUDE_HOST_PID", None)
    subprocess.run([sys.executable, REGISTRY_HOOK], input=json.dumps(payload), text=True,
                   check=True, env=env)


class _FakeProc:
    """Minimal stand-in for a psutil.Process, chained via parent() like the real ancestry walk."""

    def __init__(self, pid, name, parent=None):
        self.pid = pid
        self._name = name
        self._parent = parent

    def name(self):
        return self._name

    def parent(self):
        return self._parent


def test_ancestor_walk_finds_claude_exe():
    print("test: find_claude_ancestor_pid returns the nearest claude.exe ancestor's pid")
    claude = _FakeProc(4242, "claude.exe")
    shell = _FakeProc(999, "powershell.exe", parent=claude)
    hook = _FakeProc(os.getpid(), "python.exe", parent=shell)
    import psutil
    orig_process = psutil.Process
    psutil.Process = lambda pid: hook
    try:
        pid = session_registry.find_claude_ancestor_pid()
        check("found claude.exe pid", pid == 4242, f"got {pid}")
    finally:
        psutil.Process = orig_process


def test_ancestor_walk_returns_none_without_claude_exe():
    print("test: find_claude_ancestor_pid returns None when no ancestor is claude.exe")
    root = _FakeProc(1, "System Idle Process", parent=None)
    shell = _FakeProc(999, "powershell.exe", parent=root)
    hook = _FakeProc(os.getpid(), "python.exe", parent=shell)
    import psutil
    orig_process = psutil.Process
    psutil.Process = lambda pid: hook
    try:
        pid = session_registry.find_claude_ancestor_pid()
        check("no claude.exe ancestor -> None", pid is None, f"got {pid}")
    finally:
        psutil.Process = orig_process


def test_session_start_records_pid_key():
    print("test: SessionStart writes a 'pid' key alongside cwd/started_at")
    session_id = f"test-session-registry-{uuid.uuid4()}"
    fire_event("SessionStart", session_id)
    try:
        entry = registry_entry(session_id)
        check("entry written", entry is not None)
        check("pid key present", bool(entry) and "pid" in entry, f"got {entry}")
    finally:
        fire_event("SessionEnd", session_id)
        check("cleaned up on SessionEnd", registry_entry(session_id) is None)


def test_session_start_records_host_pid():
    print("test: SessionStart records CLAUDE_HOST_PID as host_pid (present for a launcher tab, "
          "None for a background run)")
    with_host = f"test-session-registry-{uuid.uuid4()}"
    without_host = f"test-session-registry-{uuid.uuid4()}"
    fire_event("SessionStart", with_host, host_pid="4242")
    fire_event("SessionStart", without_host)  # no CLAUDE_HOST_PID in env
    try:
        check("host_pid recorded when set", (registry_entry(with_host) or {}).get("host_pid") == "4242",
              f"got {registry_entry(with_host)}")
        check("host_pid None when unset", (registry_entry(without_host) or {}).get("host_pid") is None,
              f"got {registry_entry(without_host)}")
    finally:
        fire_event("SessionEnd", with_host, reason="self_close")
        fire_event("SessionEnd", without_host, reason="self_close")


def test_abrupt_close_of_real_tab_is_kept():
    print("test: SessionEnd reason 'other' on a tab WITH a host_pid keeps the entry (resumable)")
    session_id = f"test-session-registry-{uuid.uuid4()}"
    fire_event("SessionStart", session_id, host_pid="4242")
    try:
        fire_event("SessionEnd", session_id, reason="other")
        check("entry kept after abrupt close of a real tab", registry_entry(session_id) is not None,
              f"got {registry_entry(session_id)}")
    finally:
        fire_event("SessionEnd", session_id, reason="self_close")
        check("cleaned up", registry_entry(session_id) is None)


def test_abrupt_close_without_host_pid_is_removed():
    print("test: SessionEnd reason 'other' on a session WITHOUT a host_pid deregisters it "
          "(background/ephemeral run is never resurrected)")
    session_id = f"test-session-registry-{uuid.uuid4()}"
    fire_event("SessionStart", session_id)  # no host_pid
    fire_event("SessionEnd", session_id, reason="other")
    check("removed", registry_entry(session_id) is None, f"got {registry_entry(session_id)}")


def test_deliberate_close_of_real_tab_is_removed():
    print("test: a deliberate end (self_close, /exit, /clear) deregisters even a real tab")
    for reason in ("self_close", "prompt_input_exit", "clear", "logout"):
        session_id = f"test-session-registry-{uuid.uuid4()}"
        fire_event("SessionStart", session_id, host_pid="4242")
        fire_event("SessionEnd", session_id, reason=reason)
        removed = registry_entry(session_id) is None
        check(f"removed on reason={reason}", removed, f"still present: {registry_entry(session_id)}")
        if not removed:
            fire_event("SessionEnd", session_id, reason="self_close")


if __name__ == "__main__":
    test_ancestor_walk_finds_claude_exe()
    test_ancestor_walk_returns_none_without_claude_exe()
    test_session_start_records_pid_key()
    test_session_start_records_host_pid()
    test_abrupt_close_of_real_tab_is_kept()
    test_abrupt_close_without_host_pid_is_removed()
    test_deliberate_close_of_real_tab_is_removed()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
