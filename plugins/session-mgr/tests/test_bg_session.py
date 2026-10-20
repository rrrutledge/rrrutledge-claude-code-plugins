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
        "run_bounded", "_user_env", "DEFAULT_CONFIG_DIR", "ACCOUNTS_PATH", "claude_agents",
        "REGISTRY_PATH", "WAKES_PATH", "wait_for_bridge", "stop_session")}
    bg_session.BRIDGE_WARNINGS.clear()
    bg_session.wait_for_bridge = lambda *a, **k: True
    bg_session.REGISTRY_PATH = os.path.join(tmp, "live-sessions.json")
    bg_session.WAKES_PATH = os.path.join(tmp, "wakes.json")
    bg_session.ACCOUNTS_PATH = os.path.join(tmp, "accounts.json")
    with open(bg_session.ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump([backup_dir], f)
    saved_env = {k: os.environ.get(k) for k in (*bg_session._SESSION_ENV, "CLAUDE_CONFIG_DIR")}
    bg_session.run_bounded = fake_run
    bg_session._user_env = lambda name: (True, registry.get(name))
    bg_session.DEFAULT_CONFIG_DIR = main_dir
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


def test_scratch_only_turns_off_worktree_guard():
    print("test: scratch_only=True -> --settings sets worktree.bgIsolation none; the default leaves the guard on")
    with sandbox() as s:
        bg_session.spawn_bg("x", "m", s.tmp, "n", scratch_only=True)
        bg_session.spawn_bg("x", "m", s.tmp, "n")
        scratch = json.loads(flag_value(s.calls[0][0], "--settings"))
        default = json.loads(flag_value(s.calls[1][0], "--settings"))
        check("scratch_only launch turns the guard off", scratch.get("worktree") == {"bgIsolation": "none"}, scratch)
        check("scratch_only launch still clears the host pid", scratch.get("env", {}).get("CLAUDE_HOST_PID") == "",
              scratch)
        check("default launch keeps the guard", "worktree" not in default, default)


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


def write_job(account_dir, guid=GUID, flags=("--remote-control", "--permission-mode", "manual", "--name", "n")):
    job = os.path.join(account_dir, "jobs", guid.split("-")[0])
    os.makedirs(job, exist_ok=True)
    with open(os.path.join(job, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"sessionId": guid, "state": "done", "respawnFlags": list(flags)}, f)


def test_resume_wakes_background_job_in_place():
    print("test: reopening a background job with nothing queued wakes it in place - the bare "
          "`claude --bg --resume <guid>` on the job's account - and logs the wake")
    with sandbox() as s:
        write_transcript(s.backup)
        write_job(s.backup)
        with open(bg_session.REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump({GUID: {"cwd": "C:/repo"}}, f)
        short = bg_session.spawn_bg(None, None, s.tmp, "Ignored title", resume=GUID)
        args, kw = s.calls[0]
        check("returns the short id", short == SHORT, f"got {short}")
        check("bare resume, no other flag", args == ["claude", "--bg", "--resume", GUID], args)
        check("on the job's account", kw["env"].get("CLAUDE_CONFIG_DIR") == s.backup,
              kw["env"].get("CLAUDE_CONFIG_DIR"))
        leaked = [k for k in bg_session._SESSION_ENV if k in kw["env"]]
        check("session env vars cleared", not leaked, leaked)
        check("wake logged", GUID in bg_session.load_wakes(), bg_session.load_wakes())
        with open(bg_session.REGISTRY_PATH, encoding="utf-8") as f:
            check("same id continues, so the registry entry stays", GUID in json.load(f))


def test_resume_copies_when_wake_does_not_fit():
    print("test: a follow-up seed, or saved flags without --remote-control / manual mode, resumes as a "
          "fresh launch with every load-bearing flag instead of a wake")
    cases = {
        "follow-up seed": (("--remote-control", "--permission-mode", "manual"), "follow up"),
        "no --remote-control": (("--permission-mode", "manual"), None),
        "not manual mode": (("--remote-control", "--permission-mode", "auto"), None),
    }
    for label, (flags, seed) in cases.items():
        with sandbox() as s:
            write_transcript(s.main)
            write_job(s.main, flags=flags)
            bg_session.spawn_bg(seed, None, s.tmp, None, resume=GUID)
            args = s.calls[0][0]
            check(f"{label}: --remote-control added", "--remote-control" in args, args)
            check(f"{label}: --resume guid", flag_value(args, "--resume") == GUID, args)
            check(f"{label}: no wake logged", not bg_session.load_wakes(), bg_session.load_wakes())


def test_wake_that_copies_the_job_is_replaced():
    print("test: a wake that comes back as a new job with no saved flags is stopped and the session "
          "continued with every flag")
    with sandbox(stdout="backgrounded · deadbeef · name\n") as s:
        write_transcript(s.main)
        write_job(s.main)
        copy = os.path.join(s.main, "jobs", "deadbeef")
        os.makedirs(copy)
        with open(os.path.join(copy, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"sessionId": "deadbeef-0", "respawnFlags": ["--model", "sonnet"]}, f)
        stopped = []
        bg_session.stop_session = lambda short, cfg: stopped.append(short)
        bg_session.spawn_bg(None, None, s.tmp, None, resume=GUID)
        check("the copy is stopped", stopped == ["deadbeef"], stopped)
        check("two launches: the wake, then the full-flag continuation", len(s.calls) == 2, s.calls)
        full = s.calls[1][0]
        check("continuation carries --remote-control and manual mode",
              "--remote-control" in full and flag_value(full, "--permission-mode") == "manual", full)
        check("continuation resumes the same guid", flag_value(full, "--resume") == GUID, full)


def test_failed_wake_returns_none():
    print("test: a wake that fails returns None and logs nothing")
    with sandbox(returncode=1) as s:
        write_transcript(s.main)
        write_job(s.main)
        check("None", bg_session.spawn_bg(None, None, s.tmp, None, resume=GUID) is None)
        check("no wake logged", not bg_session.load_wakes())


def test_accounts_discovered_not_named():
    print("test: accounts come from the known-accounts list, which launches and remember_account fill in")
    with sandbox() as s:
        os.remove(bg_session.ACCOUNTS_PATH)
        check("no list -> main account only", bg_session.account_config_dirs() == [None],
              bg_session.account_config_dirs())
        bg_session.remember_account(None)
        bg_session.remember_account(s.main)
        check("main account is never recorded", not os.path.exists(bg_session.ACCOUNTS_PATH))
        s.registry["CLAUDE_CONFIG_DIR"] = s.backup
        bg_session.spawn_bg("x", "m", s.tmp, "n")
        s.registry.pop("CLAUDE_CONFIG_DIR")
        check("a launch on an account records it",
              bg_session.account_config_dirs() == [None, os.path.abspath(s.backup)],
              bg_session.account_config_dirs())
        bg_session.remember_account(s.backup)
        with open(bg_session.ACCOUNTS_PATH, encoding="utf-8") as f:
            check("recorded once", len(json.load(f)) == 1)
        shutil.rmtree(s.backup)
        check("a recorded dir that no longer exists is skipped", bg_session.account_config_dirs() == [None],
              bg_session.account_config_dirs())


def test_resume_forgets_old_registry_entry():
    print("test: a successful resume drops the resumed guid from the live-session registry; a failed one keeps it")
    other = "11111111-2222-3333-4444-555555555555"
    for returncode, kept in ((0, False), (1, True)):
        with sandbox(returncode=returncode) as s:
            with open(bg_session.REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump({GUID: {"cwd": "C:/repo"}, other: {"cwd": "C:/other"}}, f)
            bg_session.spawn_bg(None, None, s.tmp, None, resume=GUID)
            with open(bg_session.REGISTRY_PATH, encoding="utf-8") as f:
                registry = json.load(f)
            label = "failed resume keeps it" if kept else "resumed guid removed"
            check(label, (GUID in registry) == kept, registry)
            check("other entries untouched", other in registry, registry)


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


def test_launch_cwd_uses_on_disk_case():
    print("test: a launch cwd typed in the wrong case runs under the on-disk spelling")
    if os.name != "nt":
        print("  SKIP: case-insensitive paths are a Windows concern")
        return
    with sandbox() as s:
        real = os.path.join(s.tmp, "MixedCaseDir")
        os.makedirs(real)
        swapped = os.path.join(s.tmp.swapcase(), "mixedCASEdir")
        bg_session.spawn_bg("x", "m", swapped, "n")
        got = s.calls[-1][1].get("cwd")
        check("on-disk case restored", os.path.basename(got) == "MixedCaseDir", got)
        check("drive letter upper-cased", got[:1] == got[:1].upper(), got)
        missing = os.path.join(s.tmp, "no-such-dir")
        check("a missing path is left alone", bg_session.canonical_cwd(missing) == missing, missing)


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


def test_wait_for_bridge_is_bounded():
    print("test: wait_for_bridge polls until the reader is true or the timeout passes, on an injected clock")
    now = [0.0]
    sleeps = []

    def fake_sleep(n):
        sleeps.append(n)
        now[0] += n
    reads = []

    def reader_true_on(k):
        def read(short):
            reads.append(short)
            return len(reads) >= k
        return read
    real_wait = bg_session.wait_for_bridge
    with sandbox():
        got = real_wait(SHORT, timeout=30, interval=5, read=reader_true_on(3), clock=lambda: now[0],
                        sleep=fake_sleep)
        check("connects on the third read", got is True and len(reads) == 3 and sleeps == [5, 5],
              (got, reads, sleeps))
        now[0], reads[:], sleeps[:] = 0.0, [], []
        got = real_wait(SHORT, timeout=30, interval=5, read=lambda s: False, clock=lambda: now[0],
                        sleep=fake_sleep)
        check("never connects -> False after the timeout", got is False and sum(sleeps) == 30,
              (got, sleeps))


def test_bridge_up_reads_job_state():
    print("test: bridge_up is true only for a non-null bridgeSessionId in the job's state.json")
    with sandbox() as s:
        check("no job -> False", bg_session.bridge_up(SHORT) is False)
        write_job(s.backup)
        check("job without bridgeSessionId -> False", bg_session.bridge_up(SHORT) is False)
        job = os.path.join(s.backup, "jobs", SHORT, "state.json")
        for value, want in ((None, False), ("cse_abc", True)):
            with open(job, "w", encoding="utf-8") as f:
                json.dump({"sessionId": GUID, "bridgeSessionId": value}, f)
            check(f"bridgeSessionId {value!r} -> {want}", bg_session.bridge_up(SHORT) is want)


def test_bridge_retry_then_warn():
    print("test: a bridge that never connects is retried through a continuation, the failed job stopped, "
          "and a warning kept when retries run out")
    cases = {"connects on retry": ([False, True], 2, False),
             "never connects": ([False] * 3, 3, True)}
    for label, (answers, launches, warned) in cases.items():
        with sandbox() as s:
            write_job(s.main)
            stopped = []
            queue = list(answers)
            bg_session.wait_for_bridge = lambda *a, **k: queue.pop(0)
            bg_session.stop_session = lambda short, cfg: stopped.append(short)
            short = bg_session.spawn_bg("seed", "m", s.tmp, "n")
            bgs = [c[0] for c in s.calls]
            check(f"{label}: returns the short id", short == SHORT, short)
            check(f"{label}: {launches} launches", len(bgs) == launches, bgs)
            if launches > 1:
                retry = bgs[1]
                check(f"{label}: retry resumes the same guid with the reconnect seed",
                      flag_value(retry, "--resume") == GUID and retry[-1] == bg_session.RECONNECT_SEED, retry)
                check(f"{label}: retry keeps --remote-control", "--remote-control" in retry, retry)
            check(f"{label}: failed job stopped before each retry", len(stopped) == launches - 1, stopped)
            check(f"{label}: warning " + ("kept" if warned else "absent"),
                  (SHORT in bg_session.BRIDGE_WARNINGS) == warned, bg_session.BRIDGE_WARNINGS)


def test_bridge_check_skips_wakes_and_failed_launches():
    print("test: no bridge wait for a wake in place or a launch that returned no id")
    with sandbox() as s:
        write_transcript(s.main)
        write_job(s.main)
        bg_session.wait_for_bridge = lambda *a, **k: (_ for _ in ()).throw(AssertionError("waited"))
        check("wake in place returns without waiting",
              bg_session.spawn_bg(None, None, s.tmp, None, resume=GUID) == SHORT)
    with sandbox(returncode=1) as s:
        bg_session.wait_for_bridge = lambda *a, **k: (_ for _ in ()).throw(AssertionError("waited"))
        check("failed launch returns None without waiting", bg_session.spawn_bg("x", "m", s.tmp, "n") is None)


if __name__ == "__main__":
    test_wait_for_bridge_is_bounded()
    test_bridge_up_reads_job_state()
    test_bridge_retry_then_warn()
    test_bridge_check_skips_wakes_and_failed_launches()
    test_fresh_launch_flags()
    test_notify_sound_absent_when_unset()
    test_scratch_only_turns_off_worktree_guard()
    test_config_dir_follows_registry()
    test_resume_uses_transcript_account()
    test_resume_forgets_old_registry_entry()
    test_resume_wakes_background_job_in_place()
    test_resume_copies_when_wake_does_not_fit()
    test_wake_that_copies_the_job_is_replaced()
    test_failed_wake_returns_none()
    test_accounts_discovered_not_named()
    test_short_id_parsing()
    test_launch_cwd_uses_on_disk_case()
    test_write_receipt()
    test_claude_agents_merges_accounts()
    test_is_background_session()
    test_spawn_session_cli()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
