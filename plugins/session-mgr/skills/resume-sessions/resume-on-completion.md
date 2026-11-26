# Resume-on-completion - pause a blocked session and pick it up when another session finishes

Use this when a session's remaining work is genuinely blocked on **another specific session finishing** - a fresh session this session spawns to do that work now, or a peer session already doing it - rather than on Russell answering in this session.
The default when blocked is to launch a session to do the blocking work right away and set up the resume.
The blocked session captures how to resume itself, hands the resume instruction to that other session, and closes now.
When the other session's work is done, it runs the resume command and the blocked session comes back in the background with its full history to finish the rest.

Closing right away takes the blocked session off Russell's session list and, for a drainer worker, releases the drain slot it was holding, so neither sits idle while the blocking work runs.

## When this pattern applies (and when a tracker card is still right)

**The test: my next step cannot start until a session I launched, or a named peer, finishes - so I hand that session the resume command and close paused, in the same turn.**
It fires at the launch: a session whose own next step reports or builds on the launched work is blocked on that session from the launch on.
A Russell-only step inside the launched session is that session's to wait on, so that session stays open for it (or parks a sign-in per `/session-mgr:phone-mode`) and this one closes paused.

Reach for resume-on-completion only when a **specific, identifiable session** is doing the blocking work, so there is an exact session to resume against:

- **A fresh session this session spawns** to do the blocking work now, launched as a background session through session-mgr's `spawn-session.py` (a drainer worker reaches it through `<skill>/scripts/spawn-handoff.py`, per `worker-core.md` step 3).
- **An existing peer session** already doing the blocking work is the variant (find it with `ListAgents`): Russell redirects a worker to let that pre-existing session finish first, then it comes back.
  It gets the same resume instruction.

When the blocker is instead an **external party** replying on their own schedule, or nothing specific can be resumed against, use the existing **tracker-card** pattern (drainer `worker-core.md` §6, "Waiting on someone else → tracker card"): file a follow-up card and let a future poller cycle pick the remainder back up.
Resume-on-completion supplements that pattern for the identifiable-session case, and files its own backstop card when no card tracks the work (step 3).

## The steps

**A fresh spawn does steps 1 and 2 in the launch itself: add `--resume-me` to the `spawn-session.py` call (a drainer worker's `spawn-handoff.py` forwards it).**
The launcher appends the resume command and the "run it once this is done" instruction to the end of the brief, where the new session reads it with the Read tool (a seed line truncates on the command's quotes), then prints the remaining steps; record the paper trail (step 3) and close paused (step 4) in that same turn.
It also writes the wake note that step 1 describes, beside the brief (`<brief>.resume-note.md`).
Steps 1 and 2 below are the by-hand path for an existing peer.

1. **Capture the resume command.**
   From inside the blocked session, via the Bash tool, run:
   ```
   python "$HOME/Dev/rrrutledge/rrrutledge-claude-code-plugins/plugins/session-mgr/skills/resume-sessions/scripts/schedule-resume.py" \
       --note-file ".tmp/resume-note-<slug>.md" --done-title "<the peer session's title>"
   ```
   It reads this session's id from `$CLAUDE_CODE_SESSION_ID` and its working directory from the current directory (override either with `--session-id` / `--cwd`), writes the wake note to `--note-file` (the cue that the peer's work is done), and prints the exact `python "<spawn-session.py>" --resume <guid> --cwd "<cwd>" --prompt-file "<note>"` line that brings this session back as a background session, told why it woke.
   The command carries only paths and a guid, so it drops verbatim into a message or a doc without anything mangling it.

2. **Hand that command to the peer with `SendMessage`.**
   Say what the peer must confirm is done first (Russell submitted / merged / confirmed - whatever "done" means for that task), then paste the command and tell the peer to write a short report to `.tmp/<slug>.report.md` in its own working directory first (what it did, where to find it, anything that differed from the ask), then run the command via its Bash tool with `--report-file ".tmp/<slug>.report.md"` added, which adds the report to the note.

3. **Record a paper trail**, so the pause is recoverable if the other session never follows through.
   Leave a dated comment on the work's Trello card noting the pause and naming the session you handed the resume to, and keep the card in an active list with its Start set to when to check back (see the `trello` skill), so the drainer surfaces it again even if the resume never lands.
   When no card tracks the work and the source holds nothing that would resurface it, file a backstop card for the remaining steps on the board the work belongs to (a drainer worker uses Russell's board per its `context.md`), and give it that comment and Start.
   The resumed session finishes that card along with the work.

4. **Close this session now, paused**, via the **`session-mgr:close`** skill with `--pause` (a drainer worker: `python <skill>/scripts/close-session.py --pause`).
   This fires `SessionEnd` cleanly, so the session deregisters from the live-session registry rather than looking crash-interrupted.
   `--pause` stops the session instead of removing it, because a plain close deletes its worktree and the resume needs that worktree intact.

The resume carries the wake note as a follow-up prompt, so the session comes back under a new session id (the old id leaves the live-session registry), carrying the full history, and starts a turn on that note, registers that id when `SessionStart` fires, and finishes the remaining work exactly where it stopped.

## Why this is safe against a duplicate worker (drainer)

When a drainer worker's item is a Trello card, the poller stamps that card's id into seen-state the moment it dispatches the worker.
The id stays "already seen" for as long as the card's Start date holds, and a card with a future Start is not startable, so a worker that closes under this pattern **without clearing the card**, its Start either untouched or moved to step 3's check-back date, gets no second worker dispatched onto it while the resume is pending (see `providers/trello-provider.md`, CAPTURE and DUE-CARD outcome 4).
Leave the card un-cleared: the work is not done, and the resumed session is what CLEARs it once the remaining work finishes.

The resumed session is not mistaken for a stale orphan: while it runs it appears in `claude agents --json`, which `find-orphans.py` reads for liveness, and the original session's clean self-close already deregistered it, so `find-orphans.py` has nothing to resurrect in the gap between close and resume.
