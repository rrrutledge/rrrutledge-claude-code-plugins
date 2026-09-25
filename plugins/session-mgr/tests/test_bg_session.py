"""Test for scripts/bg_session.py (the one session launcher) and spawn-session.py (its CLI).

Every subprocess call is stubbed (bg_session.run_bounded, shutil.which), the registry read is
stubbed (bg_session._user_env), and the Claude accounts are fake dirs in a temp dir, so no real
claude ever launches and no real account is read.
Run directly: python plugins/session-mgr/tests/test_bg_session.py
"""
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "resume-sessions", "scripts"))
sys.path.insert(0, SCRIPTS)
import bg_session  # noqa: E402

_spec = importlib.util.spec_from_file_location("spawn_session", os.path.join(SCRIPTS, "spawn-session.py"))
spawn_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spawn_session)

GUID = "1a2b3c4d-1111-2222-3333-444455556666"
SHORT = "1a2b3c4d"

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


@contextlib.contextmanager
def sandbox(registry=None, stdout=f"backgrounded · {SHORT} · name\n", returncode=0, agents=None):
    """Fake accounts (main + backup under a temp dir), a fake registry, a recording run_bounded, and
    a stubbed claude_agents. Yields a namespace with the temp paths and the recorded calls."""
    tmp = tempfile.mkdtemp(prefix="test-bg-session-")
    main_dir, backup_dir = os.path.join(tmp, "main"), os.path.join(tmp, "backup")
    os.makedirs(main_dir)
    os.makedirs(backup_dir)
    registry = dict(registry or {})
    calls = []

    def fake_run(args, timeout, **kw):
        calls.append((list(args), kw))
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    saved_attrs = {name: getattr(bg_session, name) for name in (
        "run_bounded", "_user_env", "DEFAULT_CONFIG_DIR", "EXTRA_ACCOUNT_DIRS", "claude_agents")}
    saved_env = {k: os.environ.get(k) for k in (*bg_session._SESSION_ENV, "CLAUDE_CONFIG_DIR")}
    bg_session.run_bounded = fake_run
    bg_session._user_env = lambda name: (True, registry.get(name))
    bg_session.DEFAULT_CONFIG_DIR = main_dir
    bg_session.EXTRA_ACCOUNT_DIRS = (backup_dir,)
    bg_session.claude_agents = lambda: agents
    # A launch from inside a session inherits these; the launcher must clear every one.
    for k in bg_session._SESSION_ENV:
        os.environ[k] = "inherited-" + k
    os.environ["CLAUDE_HOST_PID"] = "31337"
    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    ns = type("NS", (), {})()
    ns.tmp, ns.main, ns.backup, ns.calls, ns.registry = tmp, main_dir, backup_dir, calls, registry
    try:
        yield ns
    finally:
        for name, value in saved_attrs.items():
            setattr(bg_session, name, value)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def flag_value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def write_transcript(account_dir, guid=GUID):
    project = os.path.join(account_dir, "projects", "C--repo")
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, guid + ".jsonl"), "w", encoding="utf-8") as f:
        f.write("{}\n")


def test_fresh_launch_flags():
    print("test: a fresh launch carries every load-bearing flag, and `--` before the seed")
    with sandbox(registry={"CLAUDE_NOTIFY_SOUND": "chime"}) as s:
        short = bg_session.spawn_bg("do the thing", "claude-sonnet-5", s.tmp, "My session")
        check("returns the short id", short == SHORT, f"got {short}")
        check("one launch", len(s.calls) == 1, s.calls)
        args, kw = s.calls[0]
        check("--bg", "--bg" in args, args)
        check("--remote-control", "--remote-control" in args, args)
        check("--permission-mode manual", flag_value(args, "--permission-mode") == "manual", args)
        check("--name", flag_value(args, "--name") == "My session", args)
        check("--model", flag_value(args, "--model") == "claude-sonnet-5", args)
        check("--disallowedTools", flag_value(args, "--disallowedTools") == bg_session.BG_DISALLOWED_TOOLS, args)
        settings = json.loads(flag_value(args, "--settings") or "{}")
        check("--settings clears CLAUDE_HOST_PID", settings.get("env", {}).get("CLAUDE_HOST_PID") == "",
              settings)
        check("--settings carries CLAUDE_NOTIFY_SOUND from the registry",
              settings.get("env", {}).get("CLAUDE_NOTIFY_SOUND") == "chime", settings)
        check("`--` then the seed, last", args[-2:] == ["--", "do the thing"], args)
        check("`--` after --disallowedTools", args.index("--") > args.index("--disallowedTools"), args)
        check("runs in the given cwd", kw.get("cwd") == s.tmp, kw)
        env = kw.get("env") or {}
        leaked = [k for k in bg_session._SESSION_ENV if k in env]
        check("session env vars cleared from the launch env", not leaked, leaked)
        check("registry unset -> CLAUDE_CONFIG_DIR removed", "CLAUDE_CONFIG_DIR" not in env)


def test_notify_sound_absent_when_unset():
    print("test: no CLAUDE_NOTIFY_SOUND in the registry -> not in --settings, CLAUDE_HOST_PID still cleared")
    with sandbox() as s:
        bg_session.spawn_bg("x", "m", s.tmp, "n")
        settings = json.loads(flag_value(s.calls[0][0], "--settings"))
        check("env is just the cleared host pid", settings == {"env": {"CLAUDE_HOST_PID": ""}}, settings)


def test_config_dir_follows_registry():
    print("test: CLAUDE_CONFIG_DIR in the launch env follows the registry")
    with sandbox() as s:
        s.registry["CLAUDE_CONFIG_DIR"] = s.backup
        os.environ["CLAUDE_CONFIG_DIR"] = s.main
        bg_session.spawn_bg("x", "m", s.tmp, "n")
        env = s.calls[0][1]["env"]
        check("registry set -> that dir", env.get("CLAUDE_CONFIG_DIR") == s.backup, env.get("CLAUDE_CONFIG_DIR"))
    with sandbox() as s:
        os.environ["CLAUDE_CONFIG_DIR"] = s.backup  # stale inherited value; the registry says main
        bg_session.spawn_bg("x", "m", s.tmp, "n")
        env = s.calls[0][1]["env"]
        check("registry unset -> removed even when inherited", "CLAUDE_CONFIG_DIR" not in env,
              env.get("CLAUDE_CONFIG_DIR"))


def test_resume_uses_transcript_account():
    print("test: a resume adds --resume <guid>, runs in the given cwd, on the account holding the transcript")
    with sandbox() as s:
        write_transcript(s.backup)
        short = bg_session.spawn_bg(None, None, s.tmp, None, resume=GUID)
        args, kw = s.calls[0]
        check("returns the short id", short == SHORT, f"got {short}")
        check("--resume guid", flag_value(args, "--resume") == GUID, args)
        check("no seed, no `--`", "--" not in args, args)
        check("keeps its own name and model", "--name" not in args and "--model" not in args, args)
        check("--bg still present", "--bg" in args and "--remote-control" in args, args)
        check("runs in the given cwd", kw.get("cwd") == s.tmp, kw)
        check("backup account's transcript -> backup CLAUDE_CONFIG_DIR",
              kw["env"].get("CLAUDE_CONFIG_DIR") == s.backup, kw["env"].get("CLAUDE_CONFIG_DIR"))
    with sandbox() as s:
        s.registry["CLAUDE_CONFIG_DIR"] = s.backup
        write_transcript(s.main)
        bg_session.spawn_bg("follow up", None, s.tmp, None, resume=GUID)
        args, kw = s.calls[0]
        check("main account's transcript -> CLAUDE_CONFIG_DIR removed despite the registry",
              "CLAUDE_CONFIG_DIR" not in kw["env"], kw["env"].get("CLAUDE_CONFIG_DIR"))
        check("follow-up seed after `--`", args[-2:] == ["--", "follow up"], args)


def test_short_id_parsing():
    print("test: the short id parses with and without ANSI color codes; failures give None")
    cases = {
        "plain": (f"backgrounded · {SHORT} · name", 0, SHORT),
        "ansi": (f"\x1b[32mbackgrounded\x1b[0m · \x1b[36m{SHORT}\x1b[39m · name", 0, SHORT),
        "no id": ("something else entirely", 0, None),
        "nonzero exit": (f"backgrounded · {SHORT}", 1, None),
    }
    for label, (stdout, rc, want) in cases.items():
        with sandbox(stdout=stdout, returncode=rc) as s:
            got = bg_session.spawn_bg("x", "m", s.tmp, "n")
            check(label, got == want, f"got {got!r}")


def test_write_receipt():
    print("test: write_receipt writes the full guid when claude_agents lists it, else the short id")
    for label, agents, want in (
            ("listed -> full guid", [{"id": SHORT, "sessionId": GUID}], GUID),
            ("not listed -> short id", [{"id": "ffffffff", "sessionId": "ffffffff-0"}], SHORT),
            ("unreadable -> short id", None, SHORT)):
        with sandbox(agents=agents) as s:
            anchor = os.path.join(s.tmp, "brief.md")
            wrote = bg_session.write_receipt(anchor, SHORT)
            with open(anchor + ".session", encoding="utf-8") as f:
                on_disk = f.read()
            check(label, wrote == want and on_disk == want, f"returned {wrote}, file {on_disk}")


def test_claude_agents_merges_accounts():
    print("test: claude_agents merges every account, dedupes, tags configDir, None when all fail")
    real_agents = bg_session.claude_agents
    with sandbox() as s:
        bg_session.claude_agents = real_agents
        outputs = {None: (0, json.dumps([{"sessionId": "a", "id": "a"}, {"sessionId": "shared"}])),
                   s.backup: (0, json.dumps([{"sessionId": "shared"}, {"sessionId": "b"}]))}

        def per_account(args, timeout, **kw):
            rc, out = outputs[kw["env"].get("CLAUDE_CONFIG_DIR")]
            return subprocess.CompletedProcess(args, rc, out, "")
        bg_session.run_bounded = per_account
        saved_which = shutil.which
        shutil.which = lambda name: "claude"
        try:
            merged = bg_session.claude_agents()
            ids = [a["sessionId"] for a in merged or []]
            check("merged and deduped", sorted(ids) == ["a", "b", "shared"], ids)
            tags = {a["sessionId"]: a["configDir"] for a in merged or []}
            check("tagged configDir", tags.get("a") is None and tags.get("b") == s.backup, tags)
            outputs[s.backup] = (1, "")
            check("one account failing still returns the other",
                  sorted(a["sessionId"] for a in bg_session.claude_agents() or []) == ["a", "shared"])
            outputs[None] = (0, "not json")
            check("all failing -> None", bg_session.claude_agents() is None)
        finally:
            shutil.which = saved_which


def test_is_background_session():
    print("test: is_background_session reads the job dir's state.json")
    with tempfile.TemporaryDirectory() as job:
        state = os.path.join(job, "state.json")
        check("no state.json -> False", bg_session.is_background_session({"CLAUDE_JOB_DIR": job}) is False)
        with open(state, "w", encoding="utf-8") as f:
            json.dump({"backend": "daemon", "state": "working"}, f)
        check("backend daemon -> True", bg_session.is_background_session({"CLAUDE_JOB_DIR": job}) is True)
        with open(state, "w", encoding="utf-8") as f:
            json.dump({"backend": "local"}, f)
        check("other backend -> False", bg_session.is_background_session({"CLAUDE_JOB_DIR": job}) is False)
    check("no job dir -> False", bg_session.is_background_session({"CLAUDE_HOST_PID": "4242"}) is False)


def test_spawn_session_cli():
    print("test: spawn-session.py main()")
    with sandbox(agents=None) as s:
        rc = spawn_session.main(["--brief", os.path.join(s.tmp, "missing.md"), "--title", "t",
                                 "--model", "m", "--cwd", s.tmp])
        check("missing file -> 1, nothing launched", rc == 1 and not s.calls, f"rc {rc}, calls {s.calls}")

        brief = os.path.join(s.tmp, "handoff.md")
        with open(brief, "w", encoding="utf-8") as f:
            f.write("brief")
        rc = spawn_session.main(["--brief", brief, "--title", "t", "--cwd", s.tmp])
        check("fresh launch without --model -> 1, nothing launched", rc == 1 and not s.calls,
              f"rc {rc}, calls {s.calls}")

        prompt = os.path.join(s.tmp, "prompt.md")
        summary = os.path.join(s.tmp, "summary.txt")
        with open(prompt, "w", encoding="utf-8") as f:
            f.write("instructions")
        with open(summary, "w", encoding="utf-8") as f:
            f.write("Daily digest\n  for today\n")
        rc = spawn_session.main(["--prompt-file", prompt, "--summary-file", summary,
                                 "--model", "claude-sonnet-5", "--cwd", s.tmp])
        check("prompt-file launch -> 0", rc == 0, f"rc {rc}")
        args = s.calls[-1][0] if s.calls else []
        seed = args[-1] if args else ""
        check("summary leads the seed", seed.startswith("Daily digest for today Your task instructions are in"),
              seed)
        check("seed points at the prompt file", prompt in seed, seed)
        check("summary names the session", flag_value(args, "--name") == "Daily digest for today", args)
        with open(prompt + ".session", encoding="utf-8") as f:
            check("receipt written beside the prompt file", f.read() == SHORT)


if __name__ == "__main__":
    test_fresh_launch_flags()
    test_notify_sound_absent_when_unset()
    test_config_dir_follows_registry()
    test_resume_uses_transcript_account()
    test_short_id_parsing()
    test_write_receipt()
    test_claude_agents_merges_accounts()
    test_is_background_session()
    test_spawn_session_cli()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
