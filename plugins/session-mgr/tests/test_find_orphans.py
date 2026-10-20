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
from datetime import datetime, timedelta, timezone

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
    """A temp registry and wake log, a temp main account dir (plus a backup account dir), a stubbed
    `claude agents` snapshot, and stubbed cmdline/pid liveness. Yields (registry_seed,
    write_transcript, backup_dir)."""
    tmp = tempfile.mkdtemp(prefix="test-find-orphans-")
    main_dir = os.path.join(tmp, "main")
    backup_dir = os.path.join(tmp, "backup")
    os.makedirs(main_dir)
    os.makedirs(backup_dir)
    saved = (find_orphans.REGISTRY_PATH, find_orphans.active_session_ids, find_orphans.pid_still_claude,
             bg_session.claude_agents, bg_session.account_config_dirs, bg_session.DEFAULT_CONFIG_DIR,
             bg_session.WAKES_PATH)
    bg_session.WAKES_PATH = os.path.join(tmp, "wakes.json")
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
         bg_session.claude_agents, bg_session.account_config_dirs, bg_session.DEFAULT_CONFIG_DIR,
         bg_session.WAKES_PATH) = saved
        shutil.rmtree(tmp, ignore_errors=True)


def iso(when):
    return when.isoformat().replace("+00:00", "Z")


def ago(**kw):
    return iso(datetime.now(timezone.utc) - timedelta(**kw))


def write_job(account_dir, session_id, state="done", updated=None, flags=None):
    """A background job record, shaped like the service's jobs/<short id>/state.json."""
    job_dir = os.path.join(account_dir, "jobs", session_id.split("-")[0])
    os.makedirs(job_dir, exist_ok=True)
    with open(os.path.join(job_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"sessionId": session_id, "state": state, "cwd": "C:/fake/job",
                   "createdAt": "2026-10-01T00:00:00.000Z", "updatedAt": updated or ago(hours=1),
                   "respawnFlags": flags or ["--remote-control", "--permission-mode", "manual"],
                   "backend": "daemon", "template": "bg"}, f)


def said(text):
    """A transcript record of Russell typing `text` into the session."""
    return {"type": "user", "message": {"role": "user", "content": text}}


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
    print("test: agent_session_ids keeps pid-bearing and blocked entries, drops stopped/done/blank")
    ids = find_orphans.agent_session_ids([
        {"sessionId": "a", "pid": 1}, {"sessionId": "b", "state": "blocked"},
        {"sessionId": "c", "status": "idle"}, {"sessionId": "d", "state": "stopped"},
        {"sessionId": "e"}, {"id": "f", "pid": 2},
        {"sessionId": "g", "state": "done"}, {"sessionId": "h", "pid": 3, "status": "idle", "state": "done"}])
    check("live set", ids == {"a", "b", "c", "h"}, f"got {ids}")


def test_retired_done_job_is_orphan():
    print("test: a background job the service retired as done, gone from `claude agents` and the "
          "registry, is an orphan carrying its retirement time")
    sid = new_id()
    with sandbox([]) as (_, write_transcript, _b):
        updated = ago(hours=3)
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid, updated=updated)
        write_transcript(sid, [said("draft it")])
        orphans = find_orphans.find_confirmed_orphans()
        match = next((o for o in orphans if o["session_id"] == sid), None)
        check("orphan present", match is not None, f"got {orphans}")
        check("cwd from the job", bool(match) and match["cwd"] == "C:/fake/job", match)
        check("ended_at is the job's last update", bool(match) and match["ended_at"] == updated, match)


def test_retired_job_listed_done_without_pid_is_orphan():
    print("test: a job still listed as done with no pid (retired, not yet settled) is an orphan")
    sid = new_id()
    with sandbox([agent(sid, state="done")]) as (_, write_transcript, _b):
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid)
        write_transcript(sid, [said("hi")])
        check("orphan present", sid in orphan_ids())


def test_running_or_held_jobs_not_orphans():
    print("test: a job still running (any state), blocked, or stopped is never an orphan")
    running, blocked, stopped, held = new_id(), new_id(), new_id(), new_id()
    with sandbox([agent(running, pid=11, status="idle", state="done"),
                  agent(held, state="blocked")]) as (_, write_transcript, _b):
        for sid, state in ((running, "done"), (held, "blocked"), (blocked, "blocked"), (stopped, "stopped")):
            write_job(bg_session.DEFAULT_CONFIG_DIR, sid, state=state)
            write_transcript(sid, [said("hi")])
        ids = orphan_ids()
        for label, sid in (("running done job", running), ("listed blocked job", held),
                           ("unlisted blocked job", blocked), ("stopped job", stopped)):
            check(f"{label} not an orphan", sid not in ids, f"got {ids}")


def test_job_time_bounds():
    print("test: a job last touched before JOBS_SINCE, or within the settle grace, is not an orphan")
    old, fresh = new_id(), new_id()
    with sandbox([]) as (_, write_transcript, _b):
        write_job(bg_session.DEFAULT_CONFIG_DIR, old, updated="2026-09-25T16:11:31.291Z")
        write_job(bg_session.DEFAULT_CONFIG_DIR, fresh, updated=ago(minutes=1))
        write_transcript(old, [said("hi")])
        write_transcript(fresh, [said("hi")])
        ids = orphan_ids()
        check("pre-cutoff job left alone", old not in ids, f"got {ids}")
        check("just-touched job waits for the next run", fresh not in ids, f"got {ids}")


def test_job_self_closed_or_without_transcript_not_orphan():
    print("test: a retired job whose transcript ran a self-close, or that has no transcript, is not an orphan")
    closed, missing = new_id(), new_id()
    with sandbox([]) as (_, write_transcript, _b):
        write_job(bg_session.DEFAULT_CONFIG_DIR, closed)
        write_job(bg_session.DEFAULT_CONFIG_DIR, missing)
        write_transcript(closed, [ran("claude stop " + closed[:8])])
        ids = orphan_ids()
        check("self-closed job left alone", closed not in ids, f"got {ids}")
        check("job with no transcript left alone", missing not in ids, f"got {ids}")


def test_job_in_backup_account_is_orphan():
    print("test: a retired job held by the backup account is found too")
    sid = new_id()
    with sandbox([]) as (_, write_transcript, backup_dir):
        write_job(backup_dir, sid)
        write_transcript(sid, [said("hi")], account_dir=backup_dir)
        check("orphan present", sid in orphan_ids())


def test_registry_entry_judged_by_its_job():
    print("test: a registry entry with a job record is judged by the job - stopped stays closed, "
          "and a retired one is reported once, with its retirement time")
    stopped, retired = new_id(), new_id()
    with sandbox([]) as (seed, write_transcript, _b):
        seed(stopped)
        seed(retired)
        write_job(bg_session.DEFAULT_CONFIG_DIR, stopped, state="stopped")
        write_job(bg_session.DEFAULT_CONFIG_DIR, retired)
        write_transcript(stopped, [said("hi")])
        write_transcript(retired, [said("hi")])
        orphans = find_orphans.find_confirmed_orphans()
        ids = [o["session_id"] for o in orphans]
        check("stopped job's registry entry not an orphan", stopped not in ids, f"got {ids}")
        check("retired job reported once", ids.count(retired) == 1, f"got {ids}")
        check("reported with ended_at", all(o.get("ended_at") for o in orphans if o["session_id"] == retired))


def test_wake_backoff():
    print("test: a session woken before and left untouched waits out a doubling backoff; a message "
          "from Russell lifts it")
    sid = new_id()
    with sandbox([]) as (_, write_transcript, _b):
        write_transcript(sid, [said("one")])
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid, updated=ago(hours=3))
        bg_session.record_wake(sid)
        check("first wake counted as touched", bg_session.load_wakes()[sid]["untouched"] == 0,
              bg_session.load_wakes())
        check("untouched since the wake -> held 12h", sid not in orphan_ids())
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid, updated=ago(hours=13))
        check("past 12h -> orphan again", sid in orphan_ids())
        bg_session.record_wake(sid)
        check("second untouched wake counted", bg_session.load_wakes()[sid]["untouched"] == 1,
              bg_session.load_wakes())
        check("second round held past 12h", sid not in orphan_ids())
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid, updated=ago(hours=25))
        check("past 24h -> orphan again", sid in orphan_ids())
        write_job(bg_session.DEFAULT_CONFIG_DIR, sid, updated=ago(hours=3))
        write_transcript(sid, [said("one"), said("two")])
        check("a new message lifts the backoff", sid in orphan_ids())
        bg_session.record_wake(sid)
        check("a touched round resets the count", bg_session.load_wakes()[sid]["untouched"] == 0,
              bg_session.load_wakes())


def test_backoff_capped():
    print("test: the backoff stops doubling at WAKE_BACKOFF_CAP")
    sid = new_id()
    with sandbox([]) as (_, write_transcript, _b):
        write_transcript(sid, [said("one")])
        wakes = {sid: {"woken_at": ago(days=30), "turns": 1, "untouched": 40}}
        check("capped", find_orphans.wake_backoff(sid, wakes) == find_orphans.WAKE_BACKOFF_CAP)


def test_human_turns_counts_only_typed_messages():
    print("test: human_turns counts typed messages, not tool results, meta records or bookkeeping")
    sid = new_id()
    with sandbox([]) as (_, write_transcript, _b):
        write_transcript(sid, [
            said("plain string"),
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "block"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "skill body"}},
            {"type": "cost-state", "sessionId": sid},
            ran("ls")])
        check("two typed messages", bg_session.human_turns(sid) == 2, bg_session.human_turns(sid))
        check("no transcript -> 0", bg_session.human_turns(new_id()) == 0)


def test_self_close_read_from_tail_of_large_transcript():
    print("test: the self-close check reads only the tail, and still sees a close at the end of a large transcript")
    sid = new_id()
    with sandbox([]) as (seed, write_transcript, _b):
        seed(sid)
        filler = [said("x" * 2000) for _ in range(400)]
        write_transcript(sid, filler + [ran("claude stop " + sid[:8])])
        check("excluded", sid not in orphan_ids())


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


def test_bridgeless_live_sessions_repaired_then_raised():
    print("test: a live blocked/done session with no bridge is stopped and resumed; a working one is left; "
          "one still bridge-less after the attempts is raised as an orphan")
    saved = {k: getattr(bg_session, k) for k in ("stop_session", "spawn_bg", "wait_for_bridge", "find_job")}
    saved_path = find_orphans.BRIDGE_REPAIRS_PATH
    tmp = tempfile.mkdtemp(prefix="test-bridgeless-")
    find_orphans.BRIDGE_REPAIRS_PATH = os.path.join(tmp, "bridge-repairs.json")
    stopped, resumed = [], []
    outcome = {"bridged": True}
    bg_session.stop_session = lambda short, cfg: stopped.append(short)
    bg_session.find_job = lambda short: (None, {})
    bg_session.spawn_bg = lambda seed, model, cwd, name, resume=None, **kw: resumed.append(resume) or "newshort"
    bg_session.wait_for_bridge = lambda *a, **k: outcome["bridged"]
    try:
        def job(sid, state, bridge=None, backend="daemon"):
            return ("", {"sessionId": sid, "state": state, "bridgeSessionId": bridge, "backend": backend,
                         "cwd": "C:/repo", "createdAt": "2026-10-20T10:00:00Z"})
        blocked, done, working, bridged, notlive = new_id(), new_id(), new_id(), new_id(), new_id()
        jobs = [job(blocked, "blocked"), job(done, "done"), job(working, "working"),
                job(bridged, "blocked", bridge="cse_x"), job(notlive, "blocked")]
        active = {blocked, done, working, bridged}
        check("repaired sessions raise nothing", find_orphans.bridgeless_orphans(jobs, active) == [])
        check("blocked and done are stopped and resumed", sorted(resumed) == sorted([blocked, done]), resumed)
        check("working, already bridged and not-live are untouched",
              not {working, bridged, notlive} & set(resumed), resumed)
        check("repaired ones leave no count", not find_orphans._load_repairs(), find_orphans._load_repairs())
        outcome["bridged"] = False
        resumed.clear()
        jobs = [job(blocked, "blocked")]
        for cycle in range(find_orphans.BRIDGE_REPAIR_ATTEMPTS):
            check(f"failed repair, cycle {cycle + 1}: nothing raised yet",
                  find_orphans.bridgeless_orphans(jobs, {blocked}) == [])
        check("failed cycles are counted", find_orphans._load_repairs() == {
            blocked: find_orphans.BRIDGE_REPAIR_ATTEMPTS}, find_orphans._load_repairs())
        raised = find_orphans.bridgeless_orphans(jobs, {blocked})
        check("raised as an orphan after the attempts",
              [o["session_id"] for o in raised] == [blocked] and raised[0]["cwd"] == "C:/repo", raised)
        check("no further resume once raised", len(resumed) == find_orphans.BRIDGE_REPAIR_ATTEMPTS, resumed)
        check("count cleared once raised", not find_orphans._load_repairs())
    finally:
        for k, v in saved.items():
            setattr(bg_session, k, v)
        find_orphans.BRIDGE_REPAIRS_PATH = saved_path
        shutil.rmtree(tmp, ignore_errors=True)


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
    test_retired_done_job_is_orphan()
    test_retired_job_listed_done_without_pid_is_orphan()
    test_running_or_held_jobs_not_orphans()
    test_job_time_bounds()
    test_job_self_closed_or_without_transcript_not_orphan()
    test_job_in_backup_account_is_orphan()
    test_registry_entry_judged_by_its_job()
    test_wake_backoff()
    test_backoff_capped()
    test_human_turns_counts_only_typed_messages()
    test_self_close_read_from_tail_of_large_transcript()
    test_pid_still_claude_false_for_missing_pid()
    test_cli_prints_json()
    test_bridgeless_live_sessions_repaired_then_raised()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
