"""The drainer's side of the headless worker path: how it finds session-mgr's bg_session launcher, the
diagnostic launch, the `claude agents --json` reads (live_session_ids / worker_counts), and the
dynamic-concurrency dispatch rule (open_slots). The launcher's own command, env, and id parsing are
session-mgr's and tested there.

Run directly:
    python plugins/drainer/tests/test_headless_workers.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
PLUGINS = os.path.dirname(PLUGIN_ROOT)
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


# ------------------------------------------------------------- resolving session-mgr's bg_session
print("\nprovider_base loads session-mgr's bg_session from the dev-repo sibling")
bg = sys.modules.get("bg_session")
want_path = os.path.join(PLUGINS, "session-mgr", "skills", "resume-sessions", "scripts", "bg_session.py")
check("registered as sys.modules['bg_session']", bg is provider_base.bg_session, True)
check("loaded from the session-mgr sibling", os.path.normcase(bg.__file__), os.path.normcase(want_path))
for name in ("spawn_bg", "launch", "write_receipt", "prompt_seed", "session_name", "claude_agents"):
    check(f"provider_base.{name} is bg_session's", getattr(provider_base, name) is getattr(bg, name), True)
check("the poller launches through the same spawn_bg", poller.spawn_bg is bg.spawn_bg, True)
check("session_mgr_script finds spawn-session.py",
      os.path.normcase(provider_base.session_mgr_script("spawn-session.py") or ""),
      os.path.normcase(os.path.join(os.path.dirname(want_path), "spawn-session.py")))

print("\nfind_skill_script takes a skill name that differs from the plugin name (installed-cache layout)")
with tempfile.TemporaryDirectory() as tmp:
    plugins = os.path.join(tmp, "plugins")
    start = os.path.join(plugins, "cache", "mkt", "drainer", "1.0.0", "skills", "drainer", "scripts", "x.py")
    for ver in ("1.9.0", "1.10.1"):
        d = os.path.join(plugins, "cache", "mkt", "session-mgr", ver, "skills", "resume-sessions", "scripts")
        os.makedirs(d)
        open(os.path.join(d, "bg_session.py"), "w").close()
    found = provider_base.find_skill_script(start, "session-mgr", os.path.join("scripts", "bg_session.py"),
                                            skill="resume-sessions")
    check("highest installed version wins (1.10.1 over 1.9.0)",
          found and found.split(os.sep)[-5], "1.10.1")
    check("no match when the skill name is left to default to the plugin name",
          provider_base.find_skill_script(start, "session-mgr", os.path.join("scripts", "bg_session.py")), None)


# ---------------------------------------------------------------------------- diagnostic launch
print("\n_spawn_diagnostic launches a background session and writes its receipt")
calls, receipts = [], []
real_spawn, real_receipt = poller.spawn_bg, poller.write_receipt
poller.spawn_bg = lambda seed, model, cwd, name, resume=None: calls.append((seed, model, cwd, name)) or "d1a90000"
poller.write_receipt = lambda anchor, sid: receipts.append((anchor, sid)) or sid
try:
    with tempfile.TemporaryDirectory() as tmp:
        poller._spawn_diagnostic("scan-failure-diagnostic", "drainer: scan; failed", "Diagnose: scan failed",
                                 "BODY", "C:/repo", tmp, "opus")
        prompt_file = receipts[0][0] if receipts else ""
        body = open(prompt_file, encoding="utf-8").read() if os.path.exists(prompt_file) else None
finally:
    poller.spawn_bg, poller.write_receipt = real_spawn, real_receipt
check("one launch", len(calls), 1)
if calls:
    seed, model, cwd, name = calls[0]
    check("seed leads with the summary", seed.startswith("Diagnose: scan failed "), True)
    check("seed points at the prompt file", f"'{prompt_file}'" in seed, True)
    check("model passed", model, "opus")
    check("cwd is the repo", cwd, "C:/repo")
    check("name is sanitized", name, "drainer: scan failed")
check("receipt written for the returned id", receipts and receipts[0][1], "d1a90000")
check("prompt file holds the body", body, "BODY")


# --------------------------------------------------------- agents-json reads: live_session_ids, worker_counts
def _with_agents(agent_list):
    """Stub the poller's claude_agents (all accounts) to return `agent_list` (or None); restore after."""
    real = poller.claude_agents
    poller.claude_agents = lambda: agent_list
    return real


AGENTS = [
    {"kind": "background", "id": "aaa", "sessionId": "aaa-1111-2222", "pid": 111, "status": "busy",
     "state": "working"},                                                                  # working -> not waiting
    {"kind": "background", "id": "bbb", "sessionId": "bbb-1111-2222", "pid": 222, "status": "idle",
     "state": "idle", "configDir": "C:/Users/me/.claude-backup"},                         # parked, other account
    {"kind": "background", "id": "ccc", "pid": 333, "status": "idle", "state": "blocked"},  # no guid listed
    {"kind": "interactive", "sessionId": "iii", "pid": 444, "status": "idle"},            # his own idle session
    # a stopped/self-closed worker: registry record survives with no pid and no status - a stale
    # "last known state", not a session still competing for Russell's attention.
    {"kind": "background", "id": "ddd", "sessionId": "ddd-1111-2222", "state": "blocked"},
]

print("\nlive_session_ids holds both the short id and the full guid of each live background session")
real = _with_agents(AGENTS)
check("short ids and guids of aaa/bbb/ccc, not the stopped ddd or the interactive session",
      poller.live_session_ids(), {"aaa", "aaa-1111-2222", "bbb", "bbb-1111-2222", "ccc"})
poller.claude_agents = real

print("\nlive_session_ids fails safe to None when the scan fails")
real = _with_agents(None)
check("None when the scan returns None", poller.live_session_ids(), None)
poller.claude_agents = real

print("\nworker_counts: waiting = any LIVE session not busy (bg OR interactive, any account); total = live only")
real = _with_agents(AGENTS)
check("(waiting=3, total=4) - the pid-less ddd counts toward neither", poller.worker_counts(), (3, 4))
poller.claude_agents = real

print("\nworker_counts fails safe to None")
real = _with_agents(None)
check("None when the scan fails", poller.worker_counts(), None)
poller.claude_agents = real


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
