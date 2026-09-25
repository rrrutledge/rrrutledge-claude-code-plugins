"""Launch a handoff from a drainer worker as a headless `claude --bg` session - the same way the poller
launches the worker itself, so a handoff never opens a Windows Terminal tab or steals desktop focus.

A worker hands off heavy or unrelated work to a fresh session (worker-core.md step 3). This launches it
through the poller's own spawn_bg, so the new session gets exactly a worker's launch: Remote Control on (reachable from the
phone), manual permission mode, the same disallowed tools, and a clean top-level identity rather than one
inherited from the worker that launched it.

The seed stays a single plain pointer at the brief - the full brief lives in the handoff doc, which the new
session opens with the Read tool, so quotes and metacharacters in the brief never reach the command line.

Usage (via the Bash tool, from the worker):
    python "<skill>/scripts/spawn-handoff.py" --title "<short title>" --repo "<repo dir>" \
        --brief "<repo dir>/.tmp/handoff-<slug>.md" --model claude-sonnet-5

Prints the new background session's short id (the handle `claude attach/logs/stop` and `claude agents`
take) and writes it to `<brief>.session`, the same receipt file the tab launcher writes.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provider_base import spawn_bg  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--title", required=True, help="short session name, shown in `claude agents` and on the phone")
    ap.add_argument("--repo", required=True, help="working directory for the new session")
    ap.add_argument("--brief", required=True, help="the handoff doc (.tmp/handoff-<slug>.md) holding the full brief")
    ap.add_argument("--model", required=True, help="explicit model id, per handoffs.md's model-choice rule")
    a = ap.parse_args()

    brief = os.path.abspath(a.brief)
    if not os.path.isfile(brief):
        print(f"spawn-handoff: brief not found: {brief}")
        return 1
    seed = (f"Resume from the handoff at {brief}. Read it fully, then begin the work it describes "
            "without waiting for further input.")
    bg_id = spawn_bg(seed, a.model, a.repo, a.title)
    if not bg_id:
        print("spawn-handoff: `claude --bg` launch failed or returned no session id.")
        return 1
    with open(brief + ".session", "w", encoding="utf-8") as f:
        f.write(bg_id)
    print(f"Handoff launched in the background: session {bg_id} ({a.title}). "
          f"Watch it with `claude attach {bg_id}` or from claude.ai/code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
