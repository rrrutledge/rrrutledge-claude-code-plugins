"""Emit the one command that resumes THIS session once another tab's blocking work is done.

The resume-on-completion pattern: a session whose remaining work is blocked on
another tab/session finishing (a peer already doing that work, or a fresh tab it
spawns to do it) captures how to resume itself, hands the resume instruction to
that other tab, and closes now instead of idling. When the other tab's work is
genuinely done, it runs the command this script prints and the blocked session
comes back with full history to finish the rest. See resume-on-completion.md next
to this file for the whole pattern.

This script produces the fiddly part — the exact `wt.exe ... launch-session.ps1
-Resume <id>` line, pointed at the version-pinned installed launcher — so no
session has to hand-assemble it (the bug the pattern doc's manual reference case
hit). It only PRINTS the command; delivering it (a SendMessage to a peer, a line
in a spawned tab's handoff doc) and self-closing afterward are the calling
session's own steps, since those are tool calls a script can't make.

Run from inside the blocked session via the Bash tool:

    python "<this file>" --title "ROOST - Anne outreach"

Everything else defaults from the session's own environment:

    --session-id   the session to resume        (default: $CLAUDE_CODE_SESSION_ID)
    --cwd          working dir to resume it in   (default: the current directory)
    --window       Windows Terminal target window(default: drainer, per the
                   standing rule against -w 0/last)
    --title        the resumed tab's title       (default: "Resumed session")

The command is emitted for a Git Bash / Bash-tool caller (the shell a peer or a
spawned tab runs it from). It carries only paths, a guid, a window name and a
title across the wt line — never free-text prose — so it survives wt tokenizing
unchanged, and it's safe to drop verbatim into a SendMessage body or a handoff
doc (both plain text, no shell re-tokenizing).
"""
import argparse
import os
import sys

# The installed launcher path, relative to a session-mgr plugin-cache version dir.
LAUNCHER_REL = os.path.join("skills", "resume-sessions", "scripts", "launch-session.ps1")


def parse_version(name):
    try:
        return tuple(int(part) for part in name.split("."))
    except ValueError:
        return (0, 0, 0)


def resolve_launcher():
    """Newest INSTALLED copy of launch-session.ps1, else the working-clone sibling.

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
    sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch-session.ps1")
    if os.path.isfile(sibling):
        return sibling
    return None


def fwd(path):
    """Windows path -> forward slashes, the form launch-session.ps1's callers use for
    -d/-File so wt and PowerShell read the drive path cleanly."""
    return path.replace("\\", "/")


def main():
    parser = argparse.ArgumentParser(description="Print the command that resumes this session.")
    parser.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--window", default="drainer")
    parser.add_argument("--title", default="Resumed session")
    args = parser.parse_args()

    if not args.session_id:
        print("schedule-resume: no session id. Pass --session-id, or run this from inside the "
              "session via the Bash tool so $CLAUDE_CODE_SESSION_ID is set.", file=sys.stderr)
        return 1

    launcher = resolve_launcher()
    if not launcher:
        print("schedule-resume: could not locate launch-session.ps1 (no installed copy and no "
              "working-clone sibling).", file=sys.stderr)
        return 1

    wt = fwd(os.path.expanduser(os.path.join(
        "~", "AppData", "Local", "Microsoft", "WindowsApps", "wt.exe")))
    command = (
        f'"{wt}" -w {args.window} new-tab -d "{fwd(args.cwd)}" '
        f'--title "{args.title}" powershell -NoExit '
        f'-File "{fwd(launcher)}" -Resume "{args.session_id}"'
    )
    print(command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
