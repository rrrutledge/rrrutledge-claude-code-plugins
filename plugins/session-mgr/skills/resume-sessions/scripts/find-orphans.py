"""find-orphans.py — the registry-confirmed half of crash-recovery detection.

Locates Claude Code sessions the live-session registry still lists as open and that aren't
currently running — either a crash or forced restart (which never fired SessionEnd) or a real
user tab closed abruptly with the window/tab X (which the SessionEnd hook keeps in the registry
rather than deregistering, because the user parked it and wants it resumed). Registry-only: this does NOT run the
resume-sessions skill's fallback scan (every session transcript's tail) — that stays
exclusive to the interactive skill, which calls it as a separate step. This script is
deliberately fast enough to run every drainer poll cycle (a few seconds' work, mostly the
`claude agents` listing per account and the psutil process scan).

Run directly, prints JSON to stdout:
    python find-orphans.py
    [{"session_id": "...", "cwd": "...", "started_at": "2026-07-20T13:04:11.123456"}, ...]

Shared by two callers:
  - the resume-sessions skill, from its Step 1
  - the drainer's orphan-sessions-adapter.py, via subprocess, resolved to the newest
    installed copy of this plugin (see that adapter's _find_orphans_script())
"""
import glob
import json
import os
import re
import sys

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bg_session  # noqa: E402

REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")
SESSION_RE = re.compile(r"--(?:resume|session-id)\s+([0-9a-fA-F-]{36})")
SELF_CLOSE_RE = re.compile(
    r"taskkill\s+/PID\s+\S+\s+/T\s+/F|close-session\.py|end-session\.py|claude\s+stop\b")

# The one `claude agents` state that means a listed session is over. A blocked or parked background
# session has no pid while it waits, yet the background service still holds it, so it is live.
STOPPED_STATES = {"stopped"}


def agent_session_ids(agents):
    """Session guids `claude agents` reports as live in any account: running (a pid), or listed
    with a status or state other than stopped."""
    ids = set()
    for agent in agents:
        sid = agent.get("sessionId")
        if not sid:
            continue
        marks = {m for m in (agent.get("status"), agent.get("state")) if m}
        if agent.get("pid") or (marks and not marks & STOPPED_STATES):
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
    for config_dir in bg_session.account_config_dirs():
        root = os.path.expanduser(config_dir or bg_session.DEFAULT_CONFIG_DIR)
        matches = glob.glob(os.path.join(root, "projects", "*", f"{session_id}.jsonl"))
        if matches:
            return matches[0]
    return None


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
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    for line in lines[-30:]:
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if _executed_self_close(record):
            return True
    return False


def find_confirmed_orphans():
    """Registry entries whose session isn't currently running, minus any that closed
    themselves on purpose. A session is running when `claude agents` lists it as live in any
    account, when a claude.exe command line names it, or when its recorded pid is still a
    claude.exe - any one of those is enough. When `claude agents` can't be read at all, nothing
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
    registry = load_registry()
    orphans = []
    to_prune = []
    for session_id, info in registry.items():
        if session_id in active:
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
    return orphans


def main():
    print(json.dumps(find_confirmed_orphans()))


if __name__ == "__main__":
    main()
