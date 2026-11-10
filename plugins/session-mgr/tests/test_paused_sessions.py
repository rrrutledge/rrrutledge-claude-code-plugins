"""Tests for session-mgr's paused-session record: a paused close is recorded, a resume clears it and
follows the continuation, the record counts only within its bound and while the session isn't running,
the duplicate check lists paused sessions with their resume id, and a resume with no --cwd finds the
paused session's own directory.

Runs in-process against a temp record, with every launch stubbed, so nothing is scanned or launched.
Run directly: python plugins/session-mgr/tests/test_paused_sessions.py
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)
import bg_session  # noqa: E402

TMP = tempfile.mkdtemp()
bg_session.PAUSED_PATH = os.path.join(TMP, "paused.json")
bg_session.REGISTRY_PATH = os.path.join(TMP, "live-sessions.json")
bg_session.WAKES_PATH = os.path.join(TMP, "wakes.json")

A, B, C = ("aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002",
           "cccccccc-0000-0000-0000-000000000003")
failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def reset():
    if os.path.exists(bg_session.PAUSED_PATH):
        os.remove(bg_session.PAUSED_PATH)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_and_bound():
    print("test: a paused close counts while stopped and within the bound")
    reset()
    bg_session.record_pause(A, "Waiting on PR", "C:/repo")
    check("listed", A in bg_session.paused_sessions([]), bg_session.paused_sessions([]))
    check("name and cwd kept", bg_session.paused_sessions([])[A]["cwd"] == "C:/repo")
    check("a running session is not paused", A not in bg_session.paused_sessions([{"sessionId": A, "pid": 7}]))
    check("a stopped listing still counts", A in bg_session.paused_sessions([{"sessionId": A}]))
    later = datetime.datetime.now(datetime.timezone.utc) + bg_session.PAUSE_HOLD + datetime.timedelta(minutes=1)
    check("past the bound it no longer holds", A not in bg_session.paused_sessions([], now=later))


def test_resume_clears_and_follows():
    print("test: a resume clears the pause and records the continuation, followed by later resumes")
    reset()
    bg_session.record_pause(A, "x", "C:/repo")
    bg_session.record_resume(A, continued_as=B)
    check("no longer paused", A not in bg_session.paused_sessions([]))
    check("A continues as B", bg_session.latest_session(A) == B)
    bg_session.record_pause(B, "x", "C:/repo")
    bg_session.record_resume(B, continued_as=C)
    check("the chain is followed", bg_session.latest_session(A) == C)
    check("an unknown guid is itself", bg_session.latest_session("zzz") == "zzz")
    bg_session.record_resume(C, continued_as=C)
    check("a wake in place records no successor", bg_session.latest_session(C) == C)


def test_launch_resumes_the_latest():
    print("test: launch resumes the newest continuation and records the new one")
    reset()
    bg_session.record_pause(A, "x", "C:/repo")
    bg_session.record_resume(A, continued_as=B)
    bg_session.record_pause(B, "x", "C:/repo")
    calls = []
    real = (bg_session.run_bounded, bg_session._wake_account, bg_session.session_guid, bg_session.transcript_account)
    bg_session.run_bounded = lambda args, **kw: calls.append(args) or type(
        "R", (), {"returncode": 0, "stdout": "backgrounded · cccccccc · x", "stderr": ""})()
    bg_session._wake_account = lambda guid: False
    bg_session.session_guid = lambda short, agents=None: C
    bg_session.transcript_account = lambda guid: None
    try:
        note = os.path.join(TMP, "note.md")
        open(note, "w").close()
        short, receipt = bg_session.launch(cwd=TMP, prompt_file=note, resume=A)
    finally:
        bg_session.run_bounded, bg_session._wake_account, bg_session.session_guid, bg_session.transcript_account = real
    resumed = calls[0][calls[0].index("--resume") + 1] if calls else None
    check("resumed the newest guid, not the old one", resumed == B, resumed)
    check("B is no longer paused", B not in bg_session.paused_sessions([]))
    check("the new continuation is followed", bg_session.latest_session(A) == C)


def test_duplicate_check_lists_paused():
    print("test: the duplicate check lists a paused session with its resume id")
    reset()
    bg_session.record_pause(A, "Slack: thread in #ops", "C:/repo")
    text = bg_session.duplicate_check_section([{"sessionId": "l", "name": "Live one", "pid": 1, "state": "working"}])
    check("live peer listed", "- Live one (working)" in text, text)
    check("paused peer listed with resume id", f"- Slack: thread in #ops (paused, resume id {A})" in text, text)
    reset()
    check("nothing live or paused -> no section", bg_session.duplicate_check_section([]) == "")


def test_resume_cwd_defaults_to_paused_record():
    print("test: spawn-session --resume with no --cwd uses the paused session's own directory")
    reset()
    repo = tempfile.mkdtemp()
    bg_session.record_pause(A, "x", repo)
    spawn_session = load("spawn-session")
    seen = []
    spawn_session.launch = lambda **kw: seen.append(kw) or ("deadbeef", "receipt")
    note = os.path.join(TMP, "note.md")
    open(note, "w").close()
    saved = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    try:
        rc = spawn_session.main(["--resume", A, "--prompt-file", note])
    finally:
        if saved:
            os.environ["CLAUDE_CODE_SESSION_ID"] = saved
    check("launched", rc == 0, rc)
    check("in the paused session's cwd", seen and seen[0]["cwd"] == repo, seen)


def test_end_session_records_pause():
    print("test: end-session --pause records the pause from the session's job record")
    reset()
    end_session = load("end-session")
    job = tempfile.mkdtemp()
    with open(os.path.join(job, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "Waiting worker", "cwd": "C:/work"}, f)
    saved = os.environ.get("CLAUDE_JOB_DIR")
    os.environ["CLAUDE_JOB_DIR"] = job
    stops = []
    end_session.fire_session_end = lambda sid: None
    end_session.stop_own_bg_session = lambda sid, pause=False: stops.append(pause) or 0
    try:
        end_session.close_headless(A, None, pause=True)
        end_session.close_headless(B, None, pause=False)
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_JOB_DIR", None)
        else:
            os.environ["CLAUDE_JOB_DIR"] = saved
    paused = bg_session.paused_sessions([])
    check("paused close recorded with name and cwd",
          paused.get(A, {}).get("name") == "Waiting worker" and paused[A]["cwd"] == "C:/work", paused)
    check("a plain close records nothing", B not in paused, paused)
    check("both still stop", stops == [True, False], stops)


if __name__ == "__main__":
    test_record_and_bound()
    test_resume_clears_and_follows()
    test_launch_resumes_the_latest()
    test_duplicate_check_lists_paused()
    test_resume_cwd_defaults_to_paused_record()
    test_end_session_records_pause()
    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED: {failures}'}")
    sys.exit(1 if failures else 0)
