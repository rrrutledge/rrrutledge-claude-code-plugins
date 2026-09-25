"""Test for scripts/find-orphans.py.

Every in-process test points the registry at a throwaway file in a temp dir, stubs the `claude
agents` listing with a recorded snapshot, and looks transcripts up in a fake account dir, so it
never reads or writes the real ~/.claude state or launches claude. The one CLI test runs the script
for real (read-only apart from its normal pruning).
Run directly: python plugins/session-mgr/tests/test_find_orphans.py
"""
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
FIND_ORPHANS = os.path.join(PLUGIN_ROOT, "skills", "resume-sessions", "scripts", "find-orphans.py")

spec = importlib.util.spec_from_file_location("find_orphans", FIND_ORPHANS)
find_orphans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find_orphans)
bg_session = find_orphans.bg_session

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def new_id():
    return str(uuid.uuid4())


def agent(session_id, *, pid=None, status=None, state=None, kind="background", config_dir=None):
    """One entry shaped like `claude agents --json` output, as claude_agents() returns it."""
    return {"id": session_id.split("-")[0] if kind == "background" else None, "sessionId": session_id,
            "kind": kind, "pid": pid, "status": status, "state": state, "configDir": config_dir}


# A recorded snapshot's shapes: a running background session, a parked (blocked, no pid) one, an
# interactive one, and a stopped one.
def snapshot(live_running, live_blocked, stopped, other_account_live=None, backup_dir=None):
    entries = [agent(live_running, pid=37636, status="busy", state="working"),
               agent(live_blocked, state="blocked"),
               agent(new_id(), pid=15244, status="idle", state=None, kind="interactive"),
               agent(stopped, state="stopped")]
    if other_account_live:
        entries.append(agent(other_account_live, pid=1234, status="idle", state="working",
                             config_dir=backup_dir))
    return entries


@contextlib.contextmanager
def sandbox(agents, active=(), pid_alive=lambda pid: False):
    """A temp registry, a temp main account dir (plus a backup account dir), a stubbed `claude
    agents` snapshot, and stubbed cmdline/pid liveness. Yields (registry_seed, write_transcript,
    backup_dir)."""
    tmp = tempfile.mkdtemp(prefix="test-find-orphans-")
    main_dir = os.path.join(tmp, "main")
    backup_dir = os.path.join(tmp, "backup")
    os.makedirs(main_dir)
    os.makedirs(backup_dir)
    saved = (find_orphans.REGISTRY_PATH, find_orphans.active_session_ids, find_orphans.pid_still_claude,
             bg_session.claude_agents, bg_session.account_config_dirs, bg_session.DEFAULT_CONFIG_DIR)
    find_orphans.REGISTRY_PATH = os.path.join(tmp, "live-sessions.json")
    find_orphans.active_session_ids = lambda: set(active)
    find_orphans.pid_still_claude = pid_alive
    bg_session.claude_agents = lambda: agents(backup_dir) if callable(agents) else agents
    bg_session.account_config_dirs = lambda: [None, backup_dir]
    bg_session.DEFAULT_CONFIG_DIR = main_dir

    def seed(session_id, pid=None):
        registry = find_orphans.load_registry()
        registry[session_id] = {"cwd": "C:/fake/repo", "started_at": "2026-07-20T00:00:00", "pid": pid}
        find_orphans.save_registry(registry)

    def write_transcript(session_id, records, account_dir=None):
        project = os.path.join(account_dir or main_dir, "projects", "C--fake-repo")
        os.makedirs(project, exist_ok=True)
        with open(os.path.join(project, f"{session_id}.jsonl"), "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

    try:
        yield seed, write_transcript, backup_dir
    finally:
        (find_orphans.REGISTRY_PATH, find_orphans.active_session_ids, find_orphans.pid_still_claude,
         bg_session.claude_agents, bg_session.account_config_dirs, bg_session.DEFAULT_CONFIG_DIR) = saved
        shutil.rmtree(tmp, ignore_errors=True)


def ran(command):
    """A transcript record of the assistant running `command` through the Bash tool."""
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


def orphan_ids():
    return {o["session_id"] for o in find_orphans.find_confirmed_orphans()}


def test_confirmed_orphan_returned():
    print("test: a registry entry nothing reports as running is returned as an orphan")
    sid = new_id()
    with sandbox([]) as (seed, _, _b):
        seed(sid)
        orphans = find_orphans.find_confirmed_orphans()
        match = next((o for o in orphans if o["session_id"] == sid), None)
        check("orphan present", match is not None, f"got {orphans}")
        check("cwd carried through", bool(match) and match["cwd"] == "C:/fake/repo")


def test_live_bg_sessions_never_orphans():
    print("test: a live background session (running, or parked with no pid) is never an orphan, "
          "even with no cmdline/pid match")
    running, blocked, stopped = new_id(), new_id(), new_id()
    with sandbox(snapshot(running, blocked, stopped)) as (seed, _, _b):
        seed(running)
        seed(blocked)
        ids = orphan_ids()
        check("running bg session not an orphan", running not in ids, f"got {ids}")
        check("blocked bg session not an orphan", blocked not in ids, f"got {ids}")


def test_live_in_other_account_not_orphan():
    print("test: a session live in the backup account's `claude agents` list is not an orphan")
    running, blocked, stopped, other = new_id(), new_id(), new_id(), new_id()
    with sandbox(lambda backup: snapshot(running, blocked, stopped, other, backup)) as (seed, _, _b):
        seed(other)
        check("other-account session not an orphan", other not in orphan_ids())


def test_stopped_or_missing_bg_session_is_orphan():
    print("test: a session listed as stopped, or not listed at all, is an orphan")
    running, blocked, stopped, gone = new_id(), new_id(), new_id(), new_id()
    with sandbox(snapshot(running, blocked, stopped)) as (seed, _, _b):
        seed(stopped)
        seed(gone)
        ids = orphan_ids()
        check("stopped session is an orphan", stopped in ids, f"got {ids}")
        check("unlisted session is an orphan", gone in ids, f"got {ids}")


def test_agents_unreadable_reports_nothing():
    print("test: claude_agents() None (couldn't look) -> no orphans, registry untouched")
    sid = new_id()
    with sandbox(None) as (seed, write_transcript, _b):
        seed(sid)
        write_transcript(sid, [ran("claude stop " + sid[:8])])
        check("no orphans", find_orphans.find_confirmed_orphans() == [])
        check("entry not pruned", sid in find_orphans.load_registry())


def test_cmdline_match_still_counts():
    print("test: a claude.exe cmdline match keeps a session live even when `claude agents` omits it")
    sid = new_id()
    with sandbox([], active={sid}) as (seed, _, _b):
        seed(sid)
        check("not returned", sid not in orphan_ids())


def test_bare_launch_excluded_via_live_pid():
    print("test: an entry whose recorded pid is still a live claude.exe is excluded (bare launch)")
    sid = new_id()
    with sandbox([], pid_alive=lambda pid: pid == 4242) as (seed, _, _b):
        seed(sid, pid=4242)
        check("not returned", sid not in orphan_ids())


def test_dead_pid_still_flagged_as_orphan():
    print("test: an entry with a recorded pid that's no longer alive is still an orphan")
    sid = new_id()
    with sandbox([]) as (seed, _, _b):
        seed(sid, pid=999999)
        check("orphan present", sid in orphan_ids())


def test_self_close_pruned_not_resumed():
    print("test: a dead session whose transcript ran a self-close is pruned, not resumed")
    commands = {
        "close-session.py": 'python "C:/x/close-session.py"',
        "end-session.py": 'python "C:/x/end-session.py"',
        "claude stop": "claude stop 6997ef2f",
        "taskkill": "taskkill /PID 123 /T /F",
    }
    for label, command in commands.items():
        sid = new_id()
        with sandbox([]) as (seed, write_transcript, _b):
            seed(sid)
            write_transcript(sid, [ran(command)])
            check(f"{label}: excluded from results", sid not in orphan_ids())
            check(f"{label}: pruned from registry", sid not in find_orphans.load_registry())


def test_self_close_found_in_other_account():
    print("test: the self-close check reads a transcript held by the backup account")
    sid = new_id()
    with sandbox([]) as (seed, write_transcript, backup_dir):
        seed(sid)
        write_transcript(sid, [ran("claude stop " + sid[:8])], account_dir=backup_dir)
        check("excluded", sid not in orphan_ids())
        check("pruned", sid not in find_orphans.load_registry())


def test_mention_only_not_pruned():
    print("test: a session that only MENTIONS close-session.py / claude stop (never ran it) is an orphan")
    sid = new_id()
    with sandbox([]) as (seed, write_transcript, _b):
        seed(sid)
        write_transcript(sid, [{"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The worker's last step runs close-session.py, or claude stop."}]}}])
        check("returned as orphan", sid in orphan_ids())
        check("still in registry", sid in find_orphans.load_registry())


def test_agent_session_ids():
    print("test: agent_session_ids keeps pid-bearing and non-stopped entries, drops stopped/blank")
    ids = find_orphans.agent_session_ids([
        {"sessionId": "a", "pid": 1}, {"sessionId": "b", "state": "blocked"},
        {"sessionId": "c", "status": "idle"}, {"sessionId": "d", "state": "stopped"},
        {"sessionId": "e"}, {"id": "f", "pid": 2}])
    check("live set", ids == {"a", "b", "c"}, f"got {ids}")


def test_pid_still_claude_false_for_missing_pid():
    print("test: pid_still_claude is False for a None/missing pid")
    check("None pid", find_orphans.pid_still_claude(None) is False)
    check("zero pid", find_orphans.pid_still_claude(0) is False)


def test_cli_prints_json():
    print("test: run as a script prints a JSON array to stdout")
    result = subprocess.run([sys.executable, FIND_ORPHANS], capture_output=True, text=True)
    check("exit code 0", result.returncode == 0, f"got {result.returncode}: {result.stderr}")
    try:
        parsed = json.loads(result.stdout)
        check("stdout is a JSON list", isinstance(parsed, list), result.stdout[:200])
    except json.JSONDecodeError:
        check("stdout is valid JSON", False, result.stdout[:200])


if __name__ == "__main__":
    test_confirmed_orphan_returned()
    test_live_bg_sessions_never_orphans()
    test_live_in_other_account_not_orphan()
    test_stopped_or_missing_bg_session_is_orphan()
    test_agents_unreadable_reports_nothing()
    test_cmdline_match_still_counts()
    test_bare_launch_excluded_via_live_pid()
    test_dead_pid_still_flagged_as_orphan()
    test_self_close_pruned_not_resumed()
    test_self_close_found_in_other_account()
    test_mention_only_not_pruned()
    test_agent_session_ids()
    test_pid_still_claude_false_for_missing_pid()
    test_cli_prints_json()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
