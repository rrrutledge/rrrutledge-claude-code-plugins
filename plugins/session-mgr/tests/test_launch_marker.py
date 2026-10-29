"""Tests for the launch marker: spawn-session.py records what the launching session does next
(handoff / resume / keep-open), end-session.py clears it, and nothing is recorded for launches that
are not a handoff from a session.

Runs in-process with the `claude --bg` launch stubbed and the marker directory in a temp folder.
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
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LAUNCH_MARKER_DIR"] = os.path.join(tmp, "state")
        try:
            fn(tmp)
        finally:
            os.environ.pop("LAUNCH_MARKER_DIR", None)


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


def test_declare_and_clear():
    print("test: keep-open declaration flips the marker, clear removes it")

    def body(tmp):
        check("nothing to declare yet", launch_marker.declare_keep_open(GUID) is False)
        launch_marker.write(GUID, "handoff", "T", "abc")
        check("declare succeeds", launch_marker.declare_keep_open(GUID) is True)
        check("mode flipped, title kept", launch_marker.read(GUID) == {"mode": "keep-open", "title": "T", "short_id": "abc"})
        launch_marker.clear(GUID)
        check("cleared", launch_marker.read(GUID) is None)
        launch_marker.clear(GUID)
        check("clearing twice is harmless", True)
    with_temp_dir(body)


if __name__ == "__main__":
    test_default_records_handoff_and_says_close()
    test_flags_choose_mode()
    test_nothing_recorded_outside_a_session_handoff()
    test_declare_and_clear()
    print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
