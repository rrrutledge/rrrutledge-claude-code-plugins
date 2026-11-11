"""Tests for spawn-session.py's duplicate-session check: a fresh --brief launch gets the live sessions
appended to its brief (the launcher left out and named as the parent), and the launches it does not fit
are left alone.

Runs in-process with `claude agents` and the `claude --bg` launch stubbed, so nothing is scanned or launched.
Run directly: python plugins/session-mgr/tests/test_duplicate_check.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)
import bg_session  # noqa: E402
import launch_marker  # noqa: E402

bg_session.PAUSED_PATH = os.path.join(tempfile.mkdtemp(), "paused.json")  # no machine paused record in the lists
launch_marker.session_registry.REGISTRY_DIR = tempfile.mkdtemp()
launch_marker.session_registry.REGISTRY_PATH = os.path.join(launch_marker.session_registry.REGISTRY_DIR, "live-sessions.json")

spec = importlib.util.spec_from_file_location("spawn_session", os.path.join(SCRIPTS, "spawn-session.py"))
spawn_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spawn_session)

GUID = "11111111-2222-3333-4444-555555555555"
AGENTS = [
    {"sessionId": GUID, "name": "Launcher session", "pid": 1, "status": "busy"},
    {"sessionId": "a", "name": "Send the\nnewsletter", "pid": 2, "state": "working"},
    {"sessionId": "b", "name": "Russell's own work", "pid": 3, "status": "idle"},
    {"sessionId": "c", "name": "Dead entry", "state": "stopped"},
    {"sessionId": "d", "name": "", "pid": 4},
]
failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def run(argv, agents):
    """spawn_session.main with the scan returning `agents` (a callable may raise) and the launch stubbed."""
    launched = []
    real = (spawn_session.launch, spawn_session.claude_agents, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    spawn_session.launch = lambda **kw: launched.append(kw) or ("deadbeef", "receipt")
    spawn_session.claude_agents = agents if callable(agents) else (lambda: agents)
    os.environ["CLAUDE_CODE_SESSION_ID"] = GUID
    try:
        rc = spawn_session.main(argv)
    finally:
        spawn_session.launch, spawn_session.claude_agents = real[0], real[1]
        if real[2] is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = real[2]
    return rc, launched


def make_brief(tmp):
    brief = os.path.join(tmp, "handoff.md")
    with open(brief, "w", encoding="utf-8") as f:
        f.write("# Do the thing\n")
    return brief


def fresh(tmp, brief, *extra):
    return ["--title", "Do thing", "--cwd", tmp, "--brief", brief, "--model", "m", *extra]


def test_fresh_launch_lists_peers():
    print("test: a fresh --brief launch lists live peers, names the parent, once")
    with tempfile.TemporaryDirectory() as tmp:
        brief = make_brief(tmp)
        rc, _ = run(fresh(tmp, brief), AGENTS)
        run(fresh(tmp, brief), AGENTS)
        text = open(brief, encoding="utf-8").read()
        check("exit 0", rc == 0, rc)
        check("body kept", text.startswith("# Do the thing"))
        check("peers listed one line each with state",
              "- Send the newsletter (working)" in text and "- Russell's own work (idle)" in text, text)
        check("dead and nameless entries left out", "Dead entry" not in text and text.count("\n- ") == 2, text)
        check("launcher not in the list", "- Launcher session" not in text, text)
        check("launcher named as parent", "handed off from `Launcher session`" in text, text)
        check("points at the settle doc", "duplicate-sessions.md" in text, text)
        check("appended once", text.count(spawn_session.DUPLICATE_CHECK_HEADING) == 1)


def test_resume_me_tail_stays_last():
    print("test: with --resume-me the duplicate section comes before the resume instruction; kind is shown")
    agents = AGENTS + [{"sessionId": "e", "name": "Mine", "pid": 9, "kind": "interactive", "status": "idle"}]
    with tempfile.TemporaryDirectory() as tmp:
        brief = make_brief(tmp)
        run(fresh(tmp, brief, "--resume-me"), agents)
        text = open(brief, encoding="utf-8").read()
        dup, res = text.find(spawn_session.DUPLICATE_CHECK_HEADING), text.find(spawn_session.RESUME_ME_HEADING)
        check("duplicate section precedes resume tail", 0 <= dup < res, f"{dup} {res}")
        check("kind shown", "- Mine (interactive, idle)" in text, text)


def test_cap():
    print("test: the list is capped at 40")
    many = [{"sessionId": str(i), "name": f"s{i}", "pid": i + 10, "state": "working"} for i in range(60)]
    with tempfile.TemporaryDirectory() as tmp:
        brief = make_brief(tmp)
        run(fresh(tmp, brief), many)
        text = open(brief, encoding="utf-8").read()
        check("40 lines", text.count("\n- s") == 40, text.count("\n- s"))


def test_skipped_launches():
    print("test: resume, worker, prompt-file launches and scan failures add nothing")
    with tempfile.TemporaryDirectory() as tmp:
        brief = make_brief(tmp)
        rc, _ = run(["--cwd", tmp, "--resume", GUID, "--brief", brief], AGENTS)
        check("resume with follow-up brief untouched", open(brief, encoding="utf-8").read() == "# Do the thing\n", rc)
        rc, _ = run(fresh(tmp, brief), None)
        check("failed scan -> launches, brief untouched",
              rc == 0 and open(brief, encoding="utf-8").read() == "# Do the thing\n", rc)

        def boom():
            raise OSError("no claude")
        rc, _ = run(fresh(tmp, brief), boom)
        check("scan that raises still launches", rc == 0 and open(brief, encoding="utf-8").read() == "# Do the thing\n", rc)

        prompt = os.path.join(tmp, "p.md")
        with open(prompt, "w", encoding="utf-8") as f:
            f.write("# p\n")
        run(["--title", "t", "--cwd", tmp, "--prompt-file", prompt, "--model", "m"], AGENTS)
        check("prompt-file launch untouched", open(prompt, encoding="utf-8").read() == "# p\n")

        job = os.path.join(tmp, "job")
        os.makedirs(job)
        with open(os.path.join(job, "job.json"), "w", encoding="utf-8") as f:
            f.write('{"job": "j", "workers": []}')
        rc, launched = run(fresh(tmp, brief, "--worker-of", job), AGENTS)
        check("worker launch: original brief untouched", open(brief, encoding="utf-8").read() == "# Do the thing\n", rc)
        seed = launched[0]["brief"] if launched else ""
        check("worker launch: seed has no duplicate section",
              seed and spawn_session.DUPLICATE_CHECK_HEADING not in open(seed, encoding="utf-8").read(), seed)


if __name__ == "__main__":
    test_fresh_launch_lists_peers()
    test_resume_me_tail_stays_last()
    test_cap()
    test_skipped_launches()
    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED: {failures}'}")
    sys.exit(1 if failures else 0)
