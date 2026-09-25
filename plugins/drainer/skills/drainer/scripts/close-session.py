"""Thin resolver/forwarder — NOT the close primitive itself.

The real self-close primitive is session-mgr's end-session.py (it fires the
SessionEnd hook event, then ends this session — see that file).
This resolver finds the NEWEST INSTALLED copy of it and forwards, so a worker
always self-closes through the version-pinned plugin rather than a floating
working-clone path — the same pattern as spawn-handoff.py in this directory.
On a machine where the plugin isn't installed yet, it falls back to the
working-clone copy.

Workers run it via the Bash tool as their very last step:

    python "<this file>"
"""
import os
import subprocess
import sys

REL_PATH = os.path.join("skills", "resume-sessions", "scripts", "end-session.py")


def parse_version(name):
    try:
        return tuple(int(part) for part in name.split("."))
    except ValueError:
        return (0, 0, 0)


def find_real():
    base = os.path.expanduser(os.path.join(
        "~", ".claude", "plugins", "cache", "rrrutledge-claude-code-plugins", "session-mgr"))
    if os.path.isdir(base):
        for version in sorted(os.listdir(base), key=parse_version, reverse=True):
            candidate = os.path.join(base, version, REL_PATH)
            if os.path.isfile(candidate):
                return candidate
    # From plugins/drainer/skills/drainer/scripts up to plugins/, then into session-mgr.
    fallback = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "session-mgr", REL_PATH))
    if os.path.isfile(fallback):
        return fallback
    return None


def main():
    real = find_real()
    if not real:
        print("close-session: could not locate the session-mgr plugin's end-session.py "
              "(no installed copy and no working-clone fallback).")
        return 1
    return subprocess.run([sys.executable, real] + sys.argv[1:], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
