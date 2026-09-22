"""End-to-end + unit test for scripts/end-session.py and the drainer's close-session.py resolver.

The tab path (CLAUDE_HOST_PID set) is exercised end-to-end against the real
~/.claude/session-mgr/live-sessions.json using a unique throwaway session id. The headless path
(CLAUDE_HOST_PID unset, CLAUDE_PID set) is exercised in-process with the kill and SessionEnd firing
stubbed, so the test never spawns or stops a real background session.
Run directly: python plugins/session-mgr/tests/test_end_session.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
END_SESSION = os.path.join(PLUGIN_ROOT, "skills", "resume-sessions", "scripts", "end-session.py")
REGISTRY_HOOK = os.path.join(PLUGIN_ROOT, "hooks", "session_registry.py")
CLOSE_SESSION = os.path.abspath(os.path.join(
    PLUGIN_ROOT, "..", "drainer", "skills", "drainer", "scripts", "close-session.py"))
REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")

# Load end-session.py as a module (hyphenated filename) for the in-process headless-branch tests.
_spec = importlib.util.spec_from_file_location("end_session", END_SESSION)
end_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(end_session)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def scrubbed_env(**overrides):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_HOST_PID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID")}
    env.update(overrides)
    return env


def registry_has(session_id):
    if not os.path.exists(REGISTRY_PATH):
        return False
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return session_id in json.load(f)


def fire_event(event, session_id):
    payload = json.dumps({"hook_event_name": event, "session_id": session_id, "cwd": os.getcwd()})
    subprocess.run([sys.executable, REGISTRY_HOOK], input=payload, text=True, check=True)


def test_nothing_to_close_refuses():
    print("test: neither a tab nor a headless worker -> exit 1, nothing killed")
    result = subprocess.run([sys.executable, END_SESSION], env=scrubbed_env(),
                            capture_output=True, text=True)
    check("exit code 1", result.returncode == 1, f"got {result.returncode}")
    check("explains nothing to close", "nothing to close" in result.stdout, result.stdout)


def test_non_ancestor_pid_refuses():
    print("test: non-ancestor CLAUDE_HOST_PID -> refuses to kill")
    victim = subprocess.Popen(["powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 30"])
    try:
        result = subprocess.run(
            [sys.executable, END_SESSION],
            env=scrubbed_env(CLAUDE_HOST_PID=str(victim.pid),
                             CLAUDE_CODE_SESSION_ID="test-non-ancestor"),
            capture_output=True, text=True)
        check("exit code 1", result.returncode == 1, f"got {result.returncode}")
        check("explains refusal", "not an ancestor" in result.stdout, result.stdout)
        check("victim still alive", victim.poll() is None)
    finally:
        victim.kill()


def test_self_close_fires_session_end_then_kills():
    print("test: tab self-close -> SessionEnd fired (registry entry removed), host tree dead")
    session_id = f"test-end-session-{uuid.uuid4()}"
    fire_event("SessionStart", session_id)
    check("registry seeded", registry_has(session_id))
    try:
        host = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f"$env:CLAUDE_HOST_PID = $PID; "
             f"$env:CLAUDE_CODE_SESSION_ID = '{session_id}'; "
             f"python '{END_SESSION}'; "
             "Start-Sleep -Seconds 30"],
            env=scrubbed_env())
        deadline = time.time() + 20
        while time.time() < deadline and host.poll() is None:
            time.sleep(0.5)
        check("host process tree killed (did not reach its 30s sleep)", host.poll() is not None)
        check("registry entry removed by SessionEnd hook", not registry_has(session_id))
    finally:
        if registry_has(session_id):
            fire_event("SessionEnd", session_id)


def _run_main_headless(ancestor, session_id="6997ef2f-1111-2222-3333-444455556666",
                       claude_pid="4242", host_pid=None):
    """Call end_session.main() on the headless branch in-process, with the SessionEnd firing and every
    subprocess call stubbed. Returns (rc, fired_session_ids, subprocess_argvs)."""
    fired, calls = [], []
    real_run = end_session.subprocess.run
    real_fire = end_session.fire_session_end
    real_anc = end_session.pid_is_ancestor
    end_session.subprocess.run = lambda argv, **kw: calls.append(argv) or types.SimpleNamespace(returncode=0)
    end_session.fire_session_end = lambda sid: fired.append(sid)
    end_session.pid_is_ancestor = lambda pid, label: ancestor
    saved = {k: os.environ.get(k) for k in ("CLAUDE_HOST_PID", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID")}
    os.environ.pop("CLAUDE_HOST_PID", None)
    if host_pid is not None:
        os.environ["CLAUDE_HOST_PID"] = host_pid
    os.environ["CLAUDE_PID"] = claude_pid
    os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
    try:
        rc = end_session.main()
    finally:
        end_session.subprocess.run = real_run
        end_session.fire_session_end = real_fire
        end_session.pid_is_ancestor = real_anc
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rc, fired, calls


def test_headless_branch_fires_then_stops():
    print("test: headless self-close -> SessionEnd fired, `claude stop <short id>`, no taskkill")
    rc, fired, calls = _run_main_headless(ancestor=True)
    check("exit code 0", rc == 0, f"got {rc}")
    check("fired SessionEnd with the full session id", fired == ["6997ef2f-1111-2222-3333-444455556666"], fired)
    check("stopped its own session by short id", ["claude", "stop", "6997ef2f"] in calls, calls)
    check("never taskkilled (headless closes via claude stop)",
          not any(argv and argv[0] == "taskkill" for argv in calls), calls)


def test_headless_non_ancestor_refuses():
    print("test: headless CLAUDE_PID not an ancestor -> refuses, no fire, no stop")
    rc, fired, calls = _run_main_headless(ancestor=False)
    check("exit code 1", rc == 1, f"got {rc}")
    check("did not fire SessionEnd", fired == [], fired)
    check("did not stop anything", calls == [], calls)


def test_resolver_forwards():
    print("test: drainer close-session.py resolves and forwards to end-session.py")
    # Forwards to the NEWEST INSTALLED end-session.py, which may predate this branch, so assert only the
    # forwarding contract (a refusal exit and end-session's own output), not a version-specific message.
    result = subprocess.run([sys.executable, CLOSE_SESSION], env=scrubbed_env(),
                            capture_output=True, text=True)
    check("exit code 1 (forwarded refusal)", result.returncode == 1, f"got {result.returncode}")
    check("end-session output came through", "end-session:" in result.stdout,
          result.stdout + result.stderr)


if __name__ == "__main__":
    test_nothing_to_close_refuses()
    test_non_ancestor_pid_refuses()
    test_self_close_fires_session_end_then_kills()
    test_headless_branch_fires_then_stops()
    test_headless_non_ancestor_refuses()
    test_resolver_forwards()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
