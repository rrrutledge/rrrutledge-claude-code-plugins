"""End this Claude Code session the way a clean exit would, then close it for good.

A session that force-kills its own host process (`taskkill /T /F`) dies before
Claude Code can fire the SessionEnd hook event, so anything wired to that event
never runs. In particular the live-session registry (hooks/session_registry.py)
keeps the session listed as open, and resume-sessions later resurrects a tab
that closed itself on purpose - and every resurrection re-registers it, so the
stale entry comes back forever.

This script is the correct self-close primitive. It fires this plugin's
SessionEnd hooks exactly as the harness would - the same commands from
hooks/hooks.json, the same JSON payload on stdin - rather than presuming what
the event does, and only then tears the session down.

There are two kinds of session to close, and the first question is always
"is this a background session?" (bg_session.is_background_session):

    Background session - a `claude --bg` session run by the background
        service, with no hosting tab. Force-killing its own claude process
        does not close it: the service respawns it under a new pid. Its clean
        close is `claude stop <short id>`, which tells the service to end that
        session for good. This holds even when CLAUDE_HOST_PID is set: a
        service started from a profile-loaded PowerShell passes that
        terminal's host pid on to every background session, and killing it
        would take down an unrelated terminal.
    Terminal session (not background, numeric CLAUDE_HOST_PID) - a session
        Russell started by hand in a PowerShell tab. Its close is the
        force-kill of that hosting process tree.

A session that is not background and has no host pid, but does carry
CLAUDE_PID and a session id, also closes through `claude stop`, so a
background session whose job state can't be read still closes cleanly.

Usage, from inside the session that wants to close (via the Bash tool):

    python "<this file>"

Everything needed comes from the session's own environment:

    CLAUDE_CODE_SESSION_ID - set by Claude Code for its child processes
    CLAUDE_JOB_DIR         - a background session's job dir, whose
                             state.json marks it as background
    CLAUDE_HOST_PID        - the tab's hosting PID, set by the user's
                             PowerShell profile when the tab launched
    CLAUDE_PID             - the session's own claude process pid, set by
                             Claude Code for its child processes

If it is none of these, the exit code is 1: nothing is killed, the session
keeps running, and the real SessionEnd fires whenever it actually ends.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_session import is_background_session  # noqa: E402

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "..", "..", ".."))
HOOKS_JSON = os.path.join(PLUGIN_ROOT, "hooks", "hooks.json")
DEFAULT_HOOK_TIMEOUT = 10


def pid_is_ancestor(pid, label):
    """The only session this script may close is its own - the same self-target
    rule safe-compounds proves for a literal `taskkill /PID`. Walk our own
    ancestry to confirm the claimed pid (the tab host, or the worker's own claude
    process) is really upstream of us, so a stale or hand-set CLAUDE_HOST_PID /
    CLAUDE_PID can never take down an unrelated session. `label` names which pid
    for the refusal message."""
    try:
        import psutil
    except ImportError:
        print(f"end-session: psutil unavailable - cannot verify the {label} PID is "
              "this session's own ancestor; refusing to close. Close it manually.")
        return False
    try:
        proc = psutil.Process(os.getpid())
        while proc is not None:
            if proc.pid == pid:
                return True
            proc = proc.parent()
    except psutil.Error:
        pass
    return False


def fire_session_end(session_id):
    """Run every SessionEnd hook command from this plugin's hooks.json with the
    payload the harness itself would send."""
    try:
        with open(HOOKS_JSON, encoding="utf-8") as f:
            hooks_config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"end-session: could not read {HOOKS_JSON} ({e}); no hooks fired.")
        return

    payload = json.dumps({
        "hook_event_name": "SessionEnd",
        "session_id": session_id,
        "cwd": os.getcwd(),
        "reason": "self_close",
    })

    for matcher_group in hooks_config.get("hooks", {}).get("SessionEnd", []):
        for hook in matcher_group.get("hooks", []):
            if hook.get("type") != "command":
                continue
            command = hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN_ROOT)
            timeout = hook.get("timeout", DEFAULT_HOOK_TIMEOUT)
            try:
                subprocess.run(command, shell=True, input=payload, text=True,
                               timeout=timeout, check=False)
                print(f"end-session: fired SessionEnd hook: {command}")
            except (OSError, subprocess.TimeoutExpired) as e:
                print(f"end-session: SessionEnd hook failed ({e}): {command}")


def stop_own_bg_session(session_id):
    """Close a headless `claude --bg` worker by asking the background service to
    stop this session. Force-killing the worker's own claude process only makes
    the service respawn it under a new pid, so `claude stop` is the clean teardown
    - it drops the session off the live list (`claude agents`) for good, leaving it
    only in the stopped history. The handle `claude stop` takes is the short id: the
    session guid's first hyphen-delimited segment (its 8-hex prefix); the full guid
    is rejected. `claude` is a trusted bare command, so this auto-approves even under
    --permission-mode manual."""
    short_id = session_id.split("-", 1)[0]
    print(f"end-session: stopping headless background session {short_id}.")
    subprocess.run(["claude", "stop", short_id], check=False)
    return 0


def close_headless(session_id, claude_pid):
    """Deregister and stop a background session. When CLAUDE_PID is known, the
    same self-target guard as the tab path, pointed at the session's own claude
    process, first confirms we are really running inside it."""
    if claude_pid and claude_pid.isdigit() and not pid_is_ancestor(int(claude_pid), "worker"):
        print(f"end-session: PID {claude_pid} is not an ancestor of this process - "
              "refusing to stop the session. Close it manually.")
        return 1
    fire_session_end(session_id)
    return stop_own_bg_session(session_id)


def main():
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    claude_pid = os.environ.get("CLAUDE_PID")

    if is_background_session():
        # Background session: `claude stop`, whatever CLAUDE_HOST_PID says - a host pid
        # here was inherited from the terminal that started the background service.
        if not session_id:
            print("end-session: background session, but CLAUDE_CODE_SESSION_ID is unset - "
                  "no short id to stop it by. Close it manually.")
            return 1
        return close_headless(session_id, claude_pid)

    host_pid = os.environ.get("CLAUDE_HOST_PID")
    if host_pid and host_pid.isdigit():
        # Terminal session: close by force-killing the hosting PowerShell process tree.
        if not pid_is_ancestor(int(host_pid), "host"):
            print(f"end-session: PID {host_pid} is not an ancestor of this process - "
                  "refusing to kill it. Close the tab manually.")
            return 1
        if session_id:
            fire_session_end(session_id)
        else:
            print("end-session: CLAUDE_CODE_SESSION_ID is unset - killing the tab "
                  "without firing SessionEnd (nothing to deregister it by).")
        print(f"end-session: killing host process tree (PID {host_pid}).")
        subprocess.run(["taskkill", "/PID", host_pid, "/T", "/F"], check=False)
        return 0

    if claude_pid and claude_pid.isdigit() and session_id:
        # No job state and no hosting tab, but a claude pid and session id: close via
        # `claude stop` rather than a process kill.
        return close_headless(session_id, claude_pid)

    print("end-session: not a background session, and neither CLAUDE_HOST_PID (a tab) nor "
          "CLAUDE_PID plus a session id is set - nothing to close. Stop normally instead; "
          "SessionEnd will fire on the real exit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
