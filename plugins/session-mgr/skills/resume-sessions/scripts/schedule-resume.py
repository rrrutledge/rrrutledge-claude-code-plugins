"""Emit the one command that resumes THIS session once another session's blocking work is done.

The resume-on-completion pattern: a session whose remaining work is blocked on
another session finishing (a peer already doing that work, or a fresh session it
spawns to do it) captures how to resume itself, hands the resume instruction to
that other session, and closes now instead of idling. When the other session's
work is genuinely done, it runs the command this script prints and the blocked
session comes back in the background, with full history, to finish the rest. See
resume-on-completion.md next to this file for the whole pattern.

This script produces the fiddly part - the exact `spawn-session.py --resume <id>`
line, pointed at the version-pinned installed launcher - so no session has to
hand-assemble it. It only PRINTS the command; delivering it (a SendMessage to a
peer, a line in a spawned session's handoff doc) and self-closing afterward are
the calling session's own steps, since those are tool calls a script can't make.

Run from inside the blocked session via the Bash tool:

    python "<this file>" --title "ROOST - Anne outreach"

Everything else defaults from the session's own environment:

    --session-id   the session to resume        (default: $CLAUDE_CODE_SESSION_ID)
    --cwd          working dir to resume it in   (default: the current directory)
    --title        a new name for the resumed session (default: keep its own)

The command is emitted for a Git Bash / Bash-tool caller (the shell a peer or a
spawned session runs it from). It carries only forward-slash paths, a guid and an
optional title, each quoted, so it's safe to drop verbatim into a SendMessage body
or a handoff doc, and the safe-compounds hook auto-approves it when run.
"""
import argparse
import os
import sys

# The launcher's CLI, relative to a session-mgr plugin-cache version dir.
LAUNCHER_REL = os.path.join("skills", "resume-sessions", "scripts", "spawn-session.py")


def parse_version(name):
    try:
        return tuple(int(part) for part in name.split("."))
    except ValueError:
        return (0, 0, 0)


def resolve_launcher():
    """Newest INSTALLED copy of spawn-session.py, else the working-clone sibling.

    Mirrors close-session.py's find_real(): whichever copy of this script runs, the
    command it emits points at the version-pinned installed launcher, so a resume is
    never launched from a floating dev-clone branch. Falls back to the launcher next
    to this file (same scripts dir) on a machine where the plugin isn't installed."""
    base = os.path.expanduser(os.path.join(
        "~", ".claude", "plugins", "cache", "rrrutledge-claude-code-plugins", "session-mgr"))
    if os.path.isdir(base):
        for version in sorted(os.listdir(base), key=parse_version, reverse=True):
            candidate = os.path.join(base, version, LAUNCHER_REL)
            if os.path.isfile(candidate):
                return candidate
    sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spawn-session.py")
    if os.path.isfile(sibling):
        return sibling
    return None


def fwd(path):
    """Windows path -> forward slashes, the form the Bash tool and the safe-compounds hook
    read cleanly."""
    return path.replace("\\", "/")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the command that resumes this session.")
    parser.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--title")
    # Accepted and ignored, so an older caller that still names a terminal window keeps working.
    parser.add_argument("--window", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not args.session_id:
        print("schedule-resume: no session id. Pass --session-id, or run this from inside the "
              "session via the Bash tool so $CLAUDE_CODE_SESSION_ID is set.", file=sys.stderr)
        return 1

    launcher = resolve_launcher()
    if not launcher:
        print("schedule-resume: could not locate spawn-session.py (no installed copy and no "
              "working-clone sibling).", file=sys.stderr)
        return 1

    command = (f'python "{fwd(launcher)}" --resume {args.session_id} '
               f'--cwd "{fwd(os.path.abspath(args.cwd))}"')
    if args.title:
        command += f' --title "{args.title}"'
    print(command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
