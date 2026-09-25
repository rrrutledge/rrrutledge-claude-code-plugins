#!/usr/bin/env python3
"""Stable launcher for the drainer scheduled tasks (version-independent).

The DrainerDigest / DrainerKeeper scheduled tasks point at a COPY of this file
at a fixed path (``~/.claude/drainer/launch-drainer.py``, placed there by the
install-*.ps1 scripts). At run time it resolves whichever drainer version is
currently installed and execs that version's ``run-digest.py`` /
``run-poller.py``. A ``claude plugin update`` is therefore picked up
automatically -- the tasks never need re-registering for a routine bump.

Resolution order:
  1. ``installed_plugins.json`` -- the active install recorded by the plugin CLI
     (authoritative; honors downgrades/pins). Prefer a user-scope entry.
  2. Fallback: highest semver folder under the drainer cache directory.
"""
import argparse
import json
import os
import re
import subprocess
import sys

PLUGIN_KEY = "drainer@rrrutledge-claude-code-plugins"
HOME = os.path.expanduser("~")
REGISTRY = os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")
CACHE_DIR = os.path.join(
    HOME, ".claude", "plugins", "cache",
    "rrrutledge-claude-code-plugins", "drainer",
)


def _from_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    entries = data.get("plugins", {}).get(PLUGIN_KEY)
    if not entries:
        return None
    # Prefer a user-scope install, then the most recently updated.
    entries = sorted(
        entries,
        key=lambda e: (e.get("scope") == "user", e.get("lastUpdated", "")),
        reverse=True,
    )
    path = entries[0].get("installPath")
    return path if path and os.path.isdir(path) else None


def _from_cache_scan():
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return None
    versioned = [n for n in names if re.match(r"^\d+\.\d+", n)]
    if not versioned:
        return None
    best = max(versioned, key=lambda n: tuple(int(p) for p in re.findall(r"\d+", n)))
    return os.path.join(CACHE_DIR, best)


def resolve_install_path():
    return _from_registry() or _from_cache_scan()


def _auto_update():
    """Pull the latest published drainer version before resolving which one to run.

    Runs silently -- a failed update is not fatal; the launcher falls back to
    whatever is already installed.  Only updates the user scope (the one the
    scheduled tasks use); a local-scope pin is left alone.
    """
    claude = os.path.join(
        HOME, "AppData", "Local", "Programs", "claude", "claude.exe"
    )
    if not os.path.isfile(claude):
        import shutil
        claude = shutil.which("claude") or claude
    try:
        subprocess.run(
            [claude, "plugin", "update", PLUGIN_KEY, "--scope", "user"],
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # claude.exe is a console app; under the windowless pythonw poller it would otherwise
            # pop a fresh "Claude" console window every cycle and steal focus. Suppress it.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass  # never block the poller/digest over a failed update check


def main():
    ap = argparse.ArgumentParser(description="Version-independent drainer task launcher.")
    ap.add_argument("--mode", required=True, choices=["digest", "poller"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--print-target", action="store_true",
                    help="Print the resolved script path and exit (no launch).")
    args, passthrough = ap.parse_known_args()

    _auto_update()
    install_path = resolve_install_path()
    if not install_path:
        sys.stderr.write("drainer launcher: could not resolve an installed drainer version\n")
        return 2

    script = "run-digest.py" if args.mode == "digest" else "run-poller.py"
    target = os.path.join(install_path, "skills", "drainer", "scripts", script)
    if not os.path.isfile(target):
        sys.stderr.write("drainer launcher: %s not found\n" % target)
        return 3

    if args.print_target:
        print(target)
        return 0

    # Reuse the interpreter the task launched us with (python vs pythonw), so the
    # poller stays console-less and the digest keeps its visible behavior.
    # The poller and everything it spawns — worker sessions and the digest — run under
    # Russell's main account, so a worker session he opens (and drives from his phone's
    # Remote Control) is on the same account his phone is signed into. Only the
    # headless triage call moves to a background account, and it does that itself
    # via the drainer.local.md `background_config_dir` knob (run-poller.py scopes
    # CLAUDE_CONFIG_DIR to just that one call), so nothing is set process-wide here.
    cmd = [sys.executable, target, "--repo", args.repo, *passthrough]
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
