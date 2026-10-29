"""The record that a session launched another session and has not yet said what it does next.

spawn-session.py writes it after a fresh launch from inside a session. The user-level Stop hook
(session-lifecycle.py) reads it and blocks the launching session's turn from ending until the
session has resolved it: closed (end-session.py clears the marker), or declared it is carrying
on with its own work (`keep-open`). Without it, a session that has handed its work off sits idle
and looks like it is waiting on Russell.

One JSON file per launching session, `<session id>.launched`, in the state directory the hook
also uses. The hook reads the file directly, so keep the keys stable:
    mode   "handoff" (close now), "resume" (close paused; the launched session resumes this one),
           or "keep-open" (declared: this session carries on, so the hook leaves it alone)
    title  the launched session's title, for the hook's message
    short_id  the launched session's short id

Command line, from inside the launching session (via the Bash tool):
    python "<this file>" keep-open      declare the launch a side quest this session carries on past
"""
import json
import os
import sys

DEFAULT_DIR = os.path.expanduser("~/.claude/session-lifecycle")


def state_dir():
    return os.environ.get("LAUNCH_MARKER_DIR") or DEFAULT_DIR


def marker_path(session_id):
    return os.path.join(state_dir(), f"{session_id}.launched")


def write(session_id, mode, title, short_id):
    os.makedirs(state_dir(), exist_ok=True)
    with open(marker_path(session_id), "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "title": title, "short_id": short_id}, f)


def read(session_id):
    try:
        with open(marker_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clear(session_id):
    try:
        os.remove(marker_path(session_id))
    except OSError:
        pass


def declare_keep_open(session_id):
    """Flip an unresolved marker to keep-open. Returns False when there is none to flip."""
    marker = read(session_id)
    if not marker:
        return False
    write(session_id, "keep-open", marker.get("title", ""), marker.get("short_id", ""))
    return True


def main(argv):
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if argv != ["keep-open"] or not session_id:
        print("usage: launch_marker.py keep-open   (run inside the launching session)")
        return 1
    if not declare_keep_open(session_id):
        print("launch_marker: no unresolved launch recorded for this session; nothing to declare.")
        return 0
    print("launch_marker: recorded - this session carries on past its launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
