"""Tests for the launch record: spawn-session.py records what the launching session does next
(handoff / resume / keep-open) on its live-session registry entry, and nothing is recorded for
launches that are not a handoff from a session.

Runs in-process with the `claude --bg` launch stubbed and the registry in a temp folder.
Run directly: python plugins/session-mgr/tests/test_launch_marker.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)

import launch_marker  # noqa: E402

spec = importlib.util.spec_from_file_location("spawn_session", os.path.join(SCRIPTS, "spawn-session.py"))
spawn_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spawn_session)

GUID = "11111111-2222-3333-4444-555555555555"
failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def spawn(tmp, extra, session_id=GUID):
    """Run spawn-session with the launch stubbed. Returns (rc, printed output)."""
    brief = os.path.join(tmp, "handoff.md")
    with open(brief, "w", encoding="utf-8") as f:
        f.write("# Brief\n")
    real_launch = spawn_session.launch
    spawn_session.launch = lambda **kw: ("deadbeef", "receipt")
    if session_id:
        os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
    else:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = spawn_session.main(["--title", "Side job", "--cwd", tmp, "--brief", brief, "--model", "m"] + extra)
    finally:
        spawn_session.launch = real_launch
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    return rc, out.getvalue()


def with_temp_dir(fn):
    """Run fn(tmp) with the live-session registry in a temp folder and GUID registered in it."""
    sr = launch_marker.session_registry
    real = sr.REGISTRY_DIR, sr.REGISTRY_PATH
    with tempfile.TemporaryDirectory() as tmp:
        sr.REGISTRY_DIR = os.path.join(tmp, "state")
        sr.REGISTRY_PATH = os.path.join(sr.REGISTRY_DIR, "live-sessions.json")
        sr.update_registry(lambda r: r.update({GUID: {"cwd": tmp, "pid": 1, "host_pid": None}}))
        try:
            fn(tmp)
        finally:
            sr.REGISTRY_DIR, sr.REGISTRY_PATH = real


def test_default_records_handoff_and_says_close():
    print("test: a plain launch records a handoff and tells the session to close")

    def body(tmp):
        rc, out = spawn(tmp, [])
        marker = launch_marker.read(GUID)
        check("exit 0", rc == 0)
        check("mode is handoff", marker and marker["mode"] == "handoff", str(marker))
        check("title and short id kept", marker and marker["title"] == "Side job" and marker["short_id"] == "deadbeef")
        check("output tells the session to close", "close this one in this same turn" in out, out)
        check("output names keep-open for a side quest", "launch_marker.py" in out and "keep-open" in out)
    with_temp_dir(body)


def test_unregistered_session_gets_a_warning():
    print("test: a launching session with no registry entry is told, loudly")

    def body(tmp):
        launch_marker.session_registry.update_registry(lambda r: r.pop(GUID, None))
        rc, out = spawn(tmp, [])
        check("launch itself still succeeds", rc == 0)
        check("output warns of the missing entry", "WARNING" in out and "no entry in the live-session registry" in out, out)
        check("close instruction still printed", "close this one in this same turn" in out)
    with_temp_dir(body)


def test_flags_choose_mode():
    print("test: --resume-me records resume, --keep-open records keep-open")

    def body(tmp):
        spawn(tmp, ["--resume-me"])
        check("resume-me -> resume", launch_marker.read(GUID)["mode"] == "resume")
        spawn(tmp, ["--keep-open"])
        check("keep-open -> keep-open", launch_marker.read(GUID)["mode"] == "keep-open")
        rc, _ = spawn(tmp, ["--keep-open", "--resume-me"])
        check("the two flags together are refused", rc == 1)
    with_temp_dir(body)


def test_nothing_recorded_outside_a_session_handoff():
    print("test: no marker without a launching session")

    def body(tmp):
        spawn(tmp, [], session_id=None)
        check("no session id -> no marker", launch_marker.read(GUID) is None)
    with_temp_dir(body)


def test_declare_and_registry_removal():
    print("test: keep-open declaration flips the field; the SessionEnd removal drops it; no entry is ever created")

    def body(tmp):
        sr = launch_marker.session_registry
        check("nothing to declare yet", launch_marker.declare_keep_open(GUID) is False)
        launch_marker.write(GUID, "handoff", "T", "abc")
        check("declare succeeds", launch_marker.declare_keep_open(GUID) is True)
        check("mode flipped, title kept", launch_marker.read(GUID) == {"mode": "keep-open", "title": "T", "short_id": "abc"})
        check("entry's other fields untouched", sr.load_registry()[GUID]["cwd"] == tmp)
        sr.update_registry(lambda r: r.pop(GUID, None))
        check("entry removal drops the launch", launch_marker.read(GUID) is None)
        check("write for an unregistered session records nothing", launch_marker.write("other", "handoff", "T", "abc") is False)
        check("and creates no entry", "other" not in sr.load_registry())
    with_temp_dir(body)


if __name__ == "__main__":
    test_default_records_handoff_and_says_close()
    test_unregistered_session_gets_a_warning()
    test_flags_choose_mode()
    test_nothing_recorded_outside_a_session_handoff()
    test_declare_and_registry_removal()
    print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
