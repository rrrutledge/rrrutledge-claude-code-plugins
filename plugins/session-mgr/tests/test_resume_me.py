"""Tests for spawn-session.py --resume-me: the launcher appends the resume-the-launcher instruction to
the brief, once, and refuses the launches it does not fit.

Runs in-process with the `claude --bg` launch stubbed, so nothing is launched.
Run directly: python plugins/session-mgr/tests/test_resume_me.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("spawn_session", os.path.join(SCRIPTS, "spawn-session.py"))
spawn_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spawn_session)

GUID = "11111111-2222-3333-4444-555555555555"
failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def run(argv, session_id=GUID):
    """spawn_session.main with the launch stubbed and $CLAUDE_CODE_SESSION_ID set to session_id
    (unset when None). Returns (rc, launched briefs)."""
    launched = []
    real_launch, real_env = spawn_session.launch, os.environ.get("CLAUDE_CODE_SESSION_ID")
    spawn_session.launch = lambda **kw: launched.append(kw.get("brief")) or ("deadbeef", "receipt")
    if session_id:
        os.environ["CLAUDE_CODE_SESSION_ID"] = session_id
    else:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    try:
        rc = spawn_session.main(argv)
    finally:
        spawn_session.launch = real_launch
        if real_env is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = real_env
    return rc, launched


def test_appends_resume_command_once():
    print("test: --resume-me appends the launcher's resume command to the brief, once")
    with tempfile.TemporaryDirectory() as tmp:
        brief = os.path.join(tmp, "handoff-form.md")
        with open(brief, "w", encoding="utf-8") as f:
            f.write("# Fill the form\n")
        argv = ["--title", "Fill form", "--cwd", tmp, "--brief", brief, "--model", "m", "--resume-me"]
        rc, launched = run(argv)
        rc2, _ = run(argv)
        text = open(brief, encoding="utf-8").read()
        check("exit 0", rc == 0 and rc2 == 0, f"{rc} {rc2}")
        check("launched from the brief itself", launched == [os.path.abspath(brief)], launched)
        check("brief keeps its body", text.startswith("# Fill the form"))
        check("brief carries the resume command", f"--resume {GUID}" in text and "spawn-session.py" in text, text)
        check("resume runs in the launcher's own directory",
              f'--cwd "{os.getcwd().replace(chr(92), "/")}"' in text, text)
        check("appended only once", text.count(spawn_session.RESUME_ME_HEADING) == 1)


def test_refusals():
    print("test: --resume-me refuses a resume launch, a prompt-file launch, and a missing session id")
    with tempfile.TemporaryDirectory() as tmp:
        brief = os.path.join(tmp, "b.md")
        with open(brief, "w", encoding="utf-8") as f:
            f.write("# b\n")
        rc, launched = run(["--cwd", tmp, "--resume", GUID, "--resume-me"])
        check("resume launch -> 1, nothing launched", rc == 1 and not launched, rc)
        rc, launched = run(["--title", "t", "--cwd", tmp, "--prompt-file", brief, "--model", "m", "--resume-me"])
        check("prompt-file launch -> 1, nothing launched", rc == 1 and not launched, rc)
        rc, launched = run(["--title", "t", "--cwd", tmp, "--brief", brief, "--model", "m", "--resume-me"],
                           session_id=None)
        check("no session id -> 1, nothing launched", rc == 1 and not launched, rc)
        check("brief untouched by the refusals", open(brief, encoding="utf-8").read() == "# b\n")


if __name__ == "__main__":
    test_appends_resume_command_once()
    test_refusals()
    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED: {failures}'}")
    sys.exit(1 if failures else 0)
