"""The headless worker path: spawn_bg's command/env/id-parsing, the `claude agents --json` reads
(live_session_ids / worker_counts), and the dynamic-concurrency dispatch rule (open_slots).

Run directly:
    python plugins/drainer/tests/test_headless_workers.py
"""
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller and provider_base import their siblings by bare name
import provider_base  # noqa: E402
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


# ----------------------------------------------------------------- spawn_bg: command, env, id parsing
class _FakeCompleted:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def _run_spawn_bg(returncode=0, stdout="Starting background service…\nbackgrounded · 6997ef2f · a worker\n",
                  raise_exc=None, base_env=None):
    """Call spawn_bg with subprocess.run stubbed; return (result_id, captured_args, captured_env)."""
    captured = {}

    def fake_run(args, cwd=None, env=None, **kw):
        captured["args"] = args
        captured["env"] = env
        captured["cwd"] = cwd
        if raise_exc:
            raise raise_exc
        return _FakeCompleted(returncode, stdout)

    real_run = provider_base.subprocess.run
    real_environ = provider_base.os.environ
    provider_base.subprocess.run = fake_run
    if base_env is not None:
        provider_base.os.environ = base_env
    try:
        rid = provider_base.spawn_bg("SEED TEXT", "sonnet", "C:/repo", "a worker")
    finally:
        provider_base.subprocess.run = real_run
        provider_base.os.environ = real_environ
    return rid, captured.get("args"), captured.get("env")


print("\nspawn_bg builds the headless launch command")
rid, args, env = _run_spawn_bg()
check("returns the parsed short id", rid, "6997ef2f")
check("invokes claude --bg", args[:2], ["claude", "--bg"])
check("runs under --permission-mode manual", "manual" in args and args[args.index("--permission-mode") + 1] == "manual", True)
check("passes the model", args[args.index("--model") + 1], "sonnet")
check("names the session", args[args.index("--name") + 1], "a worker")
check("disallows the four unused tools", args[args.index("--disallowedTools") + 1], "Artifact,Workflow,SendFeedback,PowerShell")
# The seed is the last arg, and `--` immediately precedes it so --disallowedTools cannot swallow it.
check("seed is the final arg", args[-1], "SEED TEXT")
check("`--` separates the seed as a positional", args[-2], "--")

print("\nspawn_bg clears the inherited identity env so the worker is a clean top-level session")
base = {"CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "g", "CLAUDE_PID": "1",
        "CLAUDE_HOST_PID": "999", "PATH": "keepme"}
rid, args, env = _run_spawn_bg(base_env=base)
for var in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_HOST_PID"):
    check(f"clears {var}", var in env, False)
check("keeps unrelated env (PATH)", env.get("PATH"), "keepme")

print("\nspawn_bg fails soft")
check("None on a non-zero exit", _run_spawn_bg(returncode=1)[0], None)
check("None when no id is in the output", _run_spawn_bg(stdout="Starting background service…\n")[0], None)
check("None on a subprocess error", _run_spawn_bg(raise_exc=OSError("boom"))[0], None)


# --------------------------------------------------------- agents-json reads: live_session_ids, worker_counts
def _with_agents(agent_list):
    """Stub poller._claude_agents to return `agent_list` (or None); restore after."""
    real = poller._claude_agents
    poller._claude_agents = lambda: agent_list
    return real


AGENTS = [
    {"kind": "background", "id": "aaa", "pid": 111, "status": "busy", "state": "working"},   # working -> not waiting
    {"kind": "background", "id": "bbb", "pid": 222, "status": "idle", "state": "idle"},      # parked -> waiting
    {"kind": "background", "id": "ccc", "pid": 333, "status": "idle", "state": "blocked"},   # parked for review -> waiting
    {"kind": "interactive", "sessionId": "iii", "pid": 444, "status": "idle"},              # his own idle tab -> waiting, and counts toward total
    # a stopped/self-closed worker: registry record survives with no pid and no status - a stale
    # "last known state", not a session still competing for Russell's attention.
    {"kind": "background", "id": "ddd", "state": "blocked"},
]

print("\nlive_session_ids returns the live background short ids only, excluding a stopped/dead record")
real = _with_agents(AGENTS)
check("the three live background ids (not the stopped ddd)", poller.live_session_ids(), {"aaa", "bbb", "ccc"})
poller._claude_agents = real

print("\nlive_session_ids fails safe to None when the scan fails")
real = _with_agents(None)
check("None when the scan returns None", poller.live_session_ids(), None)
poller._claude_agents = real

print("\nworker_counts: waiting = any LIVE session not busy (bg OR interactive); total = live sessions only")
real = _with_agents(AGENTS)
check("(waiting=3, total=4) - the pid-less ddd counts toward neither", poller.worker_counts(), (3, 4))
poller._claude_agents = real

print("\nworker_counts fails safe to None")
real = _with_agents(None)
check("None when the scan fails", poller.worker_counts(), None)
poller._claude_agents = real


# ---------------------------------------------------------------------------- dispatch rule: open_slots
cfg = {"target_reviewable": 5, "max_concurrent": 18}

print("\nopen_slots tops the waiting pile up toward target_reviewable")
check("empty buffer opens the full target", poller.open_slots(0, 0, 0, cfg), 5)
check("2 already waiting -> open 3 more", poller.open_slots(2, 4, 0, cfg), 3)
check("already at target -> open 0", poller.open_slots(5, 8, 0, cfg), 0)
check("over target -> floored at 0", poller.open_slots(7, 9, 0, cfg), 0)

print("\nopen_slots is bounded by the max_concurrent headroom")
check("near the cap bounds below the review buffer", poller.open_slots(0, 16, 0, cfg), 2)
check("at the cap opens 0", poller.open_slots(0, 18, 0, cfg), 0)
check("auto workers opened this cycle consume headroom", poller.open_slots(0, 15, 2, cfg), 1)

print("\nopen_slots fails closed on an unreadable count")
check("None waiting -> 0 (fail closed)", poller.open_slots(None, None, 0, cfg), 0)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
