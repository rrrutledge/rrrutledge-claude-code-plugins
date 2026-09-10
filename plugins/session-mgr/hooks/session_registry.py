import json
import os
import sys
import time
import datetime

REGISTRY_DIR = os.path.expanduser("~/.claude/session-mgr")
REGISTRY_PATH = os.path.join(REGISTRY_DIR, "live-sessions.json")


def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_registry(registry):
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    tmp_path = REGISTRY_PATH + f".tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp_path, REGISTRY_PATH)


def update_registry(mutate):
    for attempt in range(5):
        registry = load_registry()
        mutate(registry)
        try:
            save_registry(registry)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def find_claude_ancestor_pid():
    """Walk this hook process's own ancestry to find the claude.exe PID that launched it.
    A bare `claude` launch (no --resume/--session-id) exposes its session id neither on its
    command line nor — confirmed empirically — in its own process environment (Claude Code
    only passes CLAUDE_CODE_SESSION_ID to the child processes it spawns, e.g. this hook and
    the Bash tool, never setting it on itself). Recording the launching PID directly lets
    find-orphans.py confirm such a session is still alive by PID liveness instead, sidestepping
    that gap entirely. None if psutil is unavailable or no claude.exe ancestor is found."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        proc = psutil.Process(os.getpid())
        while proc is not None:
            proc = proc.parent()
            if proc is not None and (proc.name() or "").lower() == "claude.exe":
                return proc.pid
    except psutil.Error:
        pass
    return None


def main():
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")

    if not session_id:
        return

    if event == "SessionStart":
        pid = find_claude_ancestor_pid()
        # CLAUDE_HOST_PID is set by the PowerShell profile only for a tab launched through the
        # interactive launcher (launch-session.ps1). A background or scheduled `claude` run — the
        # poller's own launches, anything started from C:\Windows\system32 — never loads that
        # profile and so has no host pid. Recording it marks which registry entries are real user
        # tabs: the ones a resume should bring back after an abrupt close, as opposed to ephemeral
        # runs that should stay closed.
        host_pid = os.environ.get("CLAUDE_HOST_PID")

        def add(registry):
            registry[session_id] = {
                "cwd": cwd,
                "started_at": datetime.datetime.now().isoformat(),
                "pid": pid,
                "host_pid": host_pid,
            }
        update_registry(add)
    elif event == "SessionEnd":
        # How a session ends decides whether it stays resumable. A deliberate end — /exit, /clear,
        # logout, or the plugin's own self_close primitive (end-session.py) — deregisters the
        # session, so it is done. A tab closed abruptly with the window/tab X instead reports
        # reason "other"; for a real user tab (one carrying a host_pid) the entry is LEFT in place
        # so find-orphans resumes it — the user parked the tab and wants it back. A reason-"other"
        # end with no host_pid is a background/ephemeral session and deregisters like any deliberate
        # close, so those are never resurrected.
        reason = payload.get("reason")

        def resolve(registry):
            entry = registry.get(session_id)
            if reason == "other" and entry and entry.get("host_pid"):
                return  # abrupt close of a real tab — keep it as a resumable orphan
            registry.pop(session_id, None)
        update_registry(resolve)


if __name__ == "__main__":
    main()
