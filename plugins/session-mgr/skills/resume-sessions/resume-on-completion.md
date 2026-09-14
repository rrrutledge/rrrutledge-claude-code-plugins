# Resume-on-completion - pause a blocked session and pick it up when another tab finishes

Use this when a session's remaining work is genuinely blocked on **another specific tab or session finishing** - a fresh tab this session spawns to do that work now, or a peer session already doing it - rather than on Russell answering in this tab.
The default when blocked is to launch a tab to do the blocking work right away and set up the resume.
The blocked session captures how to resume itself, hands the resume instruction to that other tab, and closes now.
When the other tab's work is done, it launches the resume tab and the blocked session comes back with its full history to finish the rest.

This frees the terminal tab immediately, and - for a drainer worker - the drain slot it was holding, so neither sits open cycling under Russell's attention with nothing to do.

## When this pattern applies (and when a tracker card is still right)

Reach for resume-on-completion only when a **specific, identifiable tab or session** is doing the blocking work, so there is an exact session to resume against:

- **A fresh tab this session spawns** to do the blocking work now (a drainer worker spawns via `<skill>/scripts/spawn-tab.cmd`; a plain session via the handoff launcher).
- **An existing peer session** already doing the blocking work is the variant (find it with `ListAgents`): Russell redirects a worker to let that pre-existing tab finish first, then it comes back.
  It gets the same resume instruction.

When the blocker is instead an **external party** replying on their own schedule, or nothing specific can be resumed against, use the existing **tracker-card** pattern (drainer `worker-core.md` §6, "Waiting on someone else → tracker card"): file a follow-up card and let a future poller cycle pick the remainder back up.
Resume-on-completion supplements that pattern for the identifiable-session case; it does not replace it.

## The steps

1. **Capture the resume command.**
   From inside the blocked session, via the Bash tool, run:
   ```
   python "$HOME/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/skills/resume-sessions/scripts/schedule-resume.py" --title "<short title for the resumed tab>"
   ```
   It reads this session's id from `$CLAUDE_CODE_SESSION_ID` and its working directory from the current directory (override either with `--session-id` / `--cwd`), and prints the exact `wt.exe … launch-session.ps1 -Resume <id>` line that brings this session back.
   The command carries only paths, a guid, a window name and a title, so it drops verbatim into a message or a doc without anything mangling it.

2. **Hand that command to the tab doing the blocking work.**
   - **Existing peer:** send it with `SendMessage`.
     Say what the peer must confirm is done first (Russell submitted / merged / confirmed - whatever "done" means for that task), then paste the command and tell the peer to run it via its Bash tool at that point.
   - **Fresh spawn:** write the blocking work's brief to a `.tmp/handoff-*.md` doc and put the same instruction at its end - "once this is done and Russell confirms, run: `<command>`".
     Keep the command in the doc (opened with the Read tool), never in the seed line, which truncates on the command's quotes.

3. **Record a paper trail**, so the pause is recoverable if the other tab never follows through.
   A drainer worker leaves a dated comment on its Trello card noting the pause and naming the session it handed the resume to; a plain session notes it wherever that work is tracked.

4. **Close this session now** via the **`session-mgr:close`** skill (a drainer worker: `python <skill>/scripts/close-session.py`).
   This fires `SessionEnd` cleanly, so the session deregisters from the live-session registry rather than looking crash-interrupted.

The resumed session re-fires `SessionStart` when it comes back, re-registering itself, and finishes the remaining work exactly where it stopped.

## Why this is safe against a duplicate worker (drainer)

A drainer worker's item is a Trello card, and the poller stamps that card's id into seen-state the moment it dispatches the worker.
The id stays "already seen" for as long as the card's Start date holds, and only a CLEAR bumps Start (minting a fresh id), so a worker that closes under this pattern **without clearing the card and without bumping Start** leaves the card in seen-state, and no second worker is dispatched onto it while the resume is pending (see `providers/trello-provider.md`, CAPTURE).
Leave the card un-cleared: the work is not done, and the resumed session is what CLEARs it once the remaining work finishes.

The resumed session is not mistaken for a stale orphan: while it runs it appears in the active-session scan (its `--resume <id>` is on its command line), and the original session's clean self-close already deregistered it, so `find-orphans.py` has nothing to resurrect in the gap between close and resume.
