"""find-orphans.py - the confirmed half of crash-recovery detection.

The rule it enforces: a session that closed itself on purpose (session-mgr:close, end-session.py,
an executed `claude stop`) stays closed; a session that ended any other way gets resumed. It finds
those sessions in two places, as the resume-sessions skill's Step 1 lays out:
  - The live-session registry: entries still listed as open that aren't running.
  - The background service's own job records (bg_session.job_records), for the retired background
    sessions the registry no longer lists.
This does NOT run the resume-sessions skill's fallback scan (every session transcript's tail) -
that stays exclusive to the interactive skill, which calls it as a separate step. This script is
deliberately fast enough to run every drainer poll cycle (a few seconds' work, mostly the
`claude agents` listing per account and the psutil process scan).

Run directly, prints JSON to stdout:
    python find-orphans.py
    [{"session_id": "...", "cwd": "...", "started_at": "2026-07-20T13:04:11.123456"}, ...]
A session found through its job record also carries "ended_at", when the service last touched it -
its retirement.

Shared by two callers:
  - the resume-sessions skill, from its Step 1
  - the drainer's orphan-sessions-adapter.py, via subprocess, resolved to the newest
    installed copy of this plugin (see that adapter's _find_orphans_script())
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bg_session  # noqa: E402

REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")
SESSION_RE = re.compile(r"--(?:resume|session-id)\s+([0-9a-fA-F-]{36})")
SELF_CLOSE_RE = re.compile(
    r"taskkill\s+/PID\s+\S+\s+/T\s+/F|close-session\.py|end-session\.py|claude\s+stop\b")

# The `claude agents` states that mean a listed session without a pid is over: stopped, or retired
# as done (listed only briefly, until the service settles it). A blocked or parked background
# session also has no pid while it waits, yet the service still holds it, so it is live.
ENDED_STATES = {"stopped", "done"}

# Job states that are never resumed from the job record. "stopped" is an executed `claude stop` - by
# the session closing itself or by Russell - so a deliberate close. "blocked" stays listed in `claude
# agents` and revivable from the app, so it is not lost.
JOB_STATES_LEFT_ALONE = {"stopped", "blocked"}
# Job records last touched before this are never resumed. The service's mislabelled retirements
# began losing sessions on 2026-09-28; everything older predates that and is left where it lies, so
# older job records aren't mass-resurrected.
JOBS_SINCE = datetime(2026, 9, 28, tzinfo=timezone.utc)
# A job touched within this window is left for the next run: a session launched or woken just after
# `claude agents` was read isn't listed yet, and a retirement may still be settling.
JOB_SETTLE_GRACE = timedelta(minutes=5)
# Re-waking a session Russell left untouched since its last wake waits this long after it is retired
# again, doubling for every further untouched wake up to WAKE_BACKOFF_CAP. Without it, a session
# nobody answers would be woken, retired after 8 idle hours, and woken again, forever.
WAKE_BACKOFF_BASE = timedelta(hours=12)
WAKE_BACKOFF_CAP = timedelta(days=4)


def agent_session_ids(agents):
    """Session guids `claude agents` reports as live in any account: running (a pid), or listed
    with a status or state other than stopped or done."""
    ids = set()
    for agent in agents:
        sid = agent.get("sessionId")
        if not sid:
            continue
        marks = {m for m in (agent.get("status"), agent.get("state")) if m}
        if agent.get("pid") or (marks and not marks & ENDED_STATES):
            ids.add(sid)
    return ids


def active_session_ids():
    """Session IDs of every currently-running `claude.exe` process, read from its command
    line (--resume/--session-id). Falls back to the process's own CLAUDE_CODE_SESSION_ID
    environment variable for a bare launch with neither flag, but that fallback has been
    confirmed dead in practice: Claude Code only sets CLAUDE_CODE_SESSION_ID for the child
    processes it spawns (hooks, the Bash tool), never on its own process, so a bare `claude`
    launch never matches here by either signal. `find_confirmed_orphans()` covers that case
    separately via `pid_still_claude()`, which doesn't depend on either signal."""
    ids = set()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if (proc.info["name"] or "").lower() != "claude.exe":
            continue
        cmdline_str = " ".join(proc.info["cmdline"] or [])
        m = SESSION_RE.search(cmdline_str)
        if m:
            ids.add(m.group(1))
            continue
        try:
            env = proc.environ()
            sid = env.get("CLAUDE_CODE_SESSION_ID")
            if sid:
                ids.add(sid)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return ids


def pid_still_claude(pid):
    """True if `pid` is still a live claude.exe process. This is the liveness check for a
    bare `claude` launch (no --resume/--session-id): such a session's id appears neither on
    its command line nor in its own process environment (see active_session_ids), so
    session_registry.py's SessionStart hook instead records the launching claude.exe PID
    directly (by walking its own ancestry). Checking that PID's liveness sidesteps the
    session-id-matching gap entirely. Missing/non-numeric pid, or a PID now reused by some
    other process, both return False."""
    if not pid:
        return False
    try:
        proc = psutil.Process(int(pid))
        return (proc.name() or "").lower() == "claude.exe"
    except (psutil.Error, ValueError, TypeError):
        return False


def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(registry):
    tmp = REGISTRY_PATH + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp, REGISTRY_PATH)


def transcript_path(session_id):
    """The session's own .jsonl transcript, in whichever Claude account's projects folder holds it
    (one subfolder per project). None if no account has it."""
    return bg_session.transcript_file(session_id)


def _tail_lines(path, count, chunk=512 * 1024):
    """The last `count` lines of the file at `path`, reading only its final `chunk` bytes - a
    transcript can run to many MB, and this runs for every candidate on every drainer cycle."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - chunk))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    if size > chunk:
        lines = lines[1:]  # the first line is cut mid-record
    return lines[-count:]


def _executed_self_close(record):
    """True if one transcript record is an assistant message that actually RAN a self-close: a
    Bash tool_use whose command is a taskkill /T /F, a `claude stop`, or a close-session.py /
    end-session.py invocation, or a Skill tool_use invoking session-mgr:close. A plain-text mention of those
    names — reading a doc that references them, discussing the drainer — is not a match, because
    only a command that actually executed closed the tab on purpose. Keying on the mention alone
    would wrongly prune a real tab that merely had that text in its recent context (e.g. a session
    editing the drainer), so it would never resume after an abrupt close."""
    message = record.get("message") or {}
    if message.get("role") != "assistant":
        return False
    for block in (message.get("content") or []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if SELF_CLOSE_RE.search(json.dumps(block.get("input") or {})):
            return True
        if block.get("name") == "Skill" and "close" in (block.get("input") or {}).get("skill", ""):
            return True
    return False


def closed_itself_on_purpose(session_id):
    """True if the session's last ~30 transcript records show it deliberately closing its own tab
    (an executed taskkill /T /F, a close-session.py / end-session.py run, or a session-mgr:close
    skill call) — such a session dies before SessionEnd can fire, so its registry entry survives
    even though the close was intentional, and it must not be resumed. Only an actually-executed
    self-close counts (see _executed_self_close). Missing/unreadable transcript -> False: fail
    toward including a session as an orphan, never toward silently dropping a real one."""
    path = transcript_path(session_id)
    if not path:
        return False
    try:
        lines = _tail_lines(path, 30)
    except OSError:
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if _executed_self_close(record):
            return True
    return False


def _parse_time(value):
    """An ISO timestamp from a job record as an aware UTC datetime, or None."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def wake_backoff(session_id, wakes):
    """How long after its latest retirement `session_id` waits before it is woken again. Zero unless
    an earlier automated wake (bg_session.record_wake) found it and Russell has typed nothing into it
    since: then WAKE_BACKOFF_BASE, doubled for each earlier wake he also left untouched, up to
    WAKE_BACKOFF_CAP. Any message from him resets it to zero."""
    entry = wakes.get(session_id)
    if not isinstance(entry, dict):
        return timedelta(0)
    if bg_session.human_turns(session_id) > entry.get("turns", 0):
        return timedelta(0)
    return min(WAKE_BACKOFF_BASE * (2 ** min(int(entry.get("untouched", 0)), 16)), WAKE_BACKOFF_CAP)


def retired_job_orphans(jobs, active, now=None):
    """Background sessions the service has stopped running without anyone closing them: every job
    record in `jobs` (bg_session.job_records) whose session isn't live, whose state isn't one left
    alone (JOB_STATES_LEFT_ALONE), and that the service last touched since JOBS_SINCE and outside
    the settle grace - minus any whose transcript shows an executed self-close or is missing
    (nothing to resume from), and any still inside its wake backoff."""
    now = now or datetime.now(timezone.utc)
    wakes = bg_session.load_wakes()
    orphans, seen = [], set()
    for _, job in jobs:
        sid = job["sessionId"]
        if sid in active or sid in seen or job.get("state") in JOB_STATES_LEFT_ALONE:
            continue
        ended = _parse_time(job.get("updatedAt") or job.get("lastTerminalAt") or job.get("createdAt"))
        if ended is None or ended < JOBS_SINCE or now - ended < JOB_SETTLE_GRACE:
            continue
        if not transcript_path(sid) or closed_itself_on_purpose(sid):
            continue
        if now - ended < wake_backoff(sid, wakes):
            continue
        seen.add(sid)
        orphans.append({
            "session_id": sid,
            "cwd": job.get("cwd"),
            "started_at": job.get("createdAt"),
            "ended_at": job.get("updatedAt") or ended.isoformat(),
        })
    return orphans


def find_confirmed_orphans():
    """Registry entries and retired background jobs whose session isn't currently running, minus
    any that closed themselves on purpose. A session is running when `claude agents` lists it as
    live in any account, when a claude.exe command line names it, or when its recorded pid is still
    a claude.exe - any one of those is enough. When `claude agents` can't be read at all, nothing
    is reported: a background session has no process of its own to find, so missing data would
    otherwise resurrect every live one.
    Self-closed entries are pruned from the registry in place (not
    just excluded from the return value) so a later run doesn't re-litigate them — same
    behavior the resume-sessions skill has always documented for this check. Pruning isn't
    wrapped in the retry-on-write-race loop session_registry.py's hook uses: a missed prune
    just means the same (harmless) exclusion happens again next run, never a lost orphan."""
    agents = bg_session.claude_agents()
    if agents is None:
        return []
    active = agent_session_ids(agents) | active_session_ids()
    jobs = list(bg_session.job_records())
    job_ids = {job["sessionId"] for _, job in jobs}
    registry = load_registry()
    orphans = []
    to_prune = []
    for session_id, info in registry.items():
        # A background session with a job record is judged from that record below, which knows
        # whether the service stopped it and when it was retired.
        if session_id in active or session_id in job_ids:
            continue
        if pid_still_claude(info.get("pid")):
            continue
        if closed_itself_on_purpose(session_id):
            to_prune.append(session_id)
            continue
        orphans.append({
            "session_id": session_id,
            "cwd": info.get("cwd"),
            "started_at": info.get("started_at"),
        })
    if to_prune:
        registry = load_registry()
        for sid in to_prune:
            registry.pop(sid, None)
        save_registry(registry)
    return orphans + retired_job_orphans(jobs, active)


def main():
    print(json.dumps(find_confirmed_orphans()))


if __name__ == "__main__":
    main()
