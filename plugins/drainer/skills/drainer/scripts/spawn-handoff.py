"""Thin forwarder to session-mgr's spawn-session.py - the launcher every background session goes through.

It exists so worker prompts (worker-core.md) and scripts that already know the drainer's path keep one
stable entry point for a handoff. It resolves spawn-session.py the way the drainer resolves any sibling
skill script (dev-repo sibling first, else the highest installed version) and forwards every argument
unchanged; all flags are spawn-session.py's. The exit code is spawn-session.py's too.

    python "<drainer-skill>/scripts/spawn-handoff.py" <spawn-session.py flags>
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provider_base import session_mgr_script  # noqa: E402


def main():
    real = session_mgr_script("spawn-session.py")
    if not real:
        print("spawn-handoff: could not locate the session-mgr plugin's spawn-session.py.")
        return 1
    return subprocess.run([sys.executable, real] + sys.argv[1:], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
