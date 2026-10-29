"""The record that a session launched another session and has not yet said what it does next.

It is a `launch` field on the launching session's entry in the live-session registry
(hooks/session_registry.py). spawn-session.py writes it after a fresh launch from inside a session.
The user-level Stop hook (session-lifecycle.py) reads it and blocks the launching session's turn from
ending until the session has resolved it: closed (the registry's SessionEnd removal drops the whole
entry, field included), or declared it is carrying on with its own work (`keep-open`). Without it, a
session that has handed its work off sits idle and looks like it is waiting on Russell.

The field's keys, which the hook reads directly, so keep them stable:
    mode   "handoff" (close now), "resume" (close paused; the launched session resumes this one),
           or "keep-open" (declared: this session carries on, so the hook leaves it alone)
    title  the launched session's title, for the hook's message
    short_id  the launched session's short id

A session with no registry entry gets no record, and `write` returns False so the caller can say so:
the entry is what find-orphans.py reads, so this module never creates one, and every session
registers at start, so a missing entry is a bug to surface, not a case to skip.

Command line, from inside the launching session (via the Bash tool):
    python "<this file>" keep-open      declare the launch a side quest this session carries on past
"""
import os
import sys

HOOKS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "hooks"))
sys.path.insert(0, HOOKS)
import session_registry  # noqa: E402


def read(session_id):
    entry = session_registry.load_registry().get(session_id)
    return entry.get("launch") if isinstance(entry, dict) else None


def write(session_id, mode, title, short_id):
    """Record the launch on the session's registry entry. Returns False when it has no entry."""
    found = []

    def mutate(registry):
        entry = registry.get(session_id)
        if isinstance(entry, dict):
            entry["launch"] = {"mode": mode, "title": title, "short_id": short_id}
            found.append(True)
    session_registry.update_registry(mutate)
    return bool(found)


def declare_keep_open(session_id):
    """Flip an unresolved launch to keep-open. Returns False when there is none to flip."""
    launch = read(session_id)
    if not launch:
        return False
    return write(session_id, "keep-open", launch.get("title", ""), launch.get("short_id", ""))


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
