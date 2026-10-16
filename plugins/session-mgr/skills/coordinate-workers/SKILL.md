---
name: coordinate-workers
description: Use when a session is about to split a job across several worker sessions (background `claude --bg` sessions) and collect their results, or when a handoff names this skill. Covers the coordinator loop - job folder, launching workers, pausing until the last one reports, assembling one review round for Russell - so `claude agents` shows only the sessions that need him.
---

# Coordinate workers

A coordinator splits one job across worker sessions, pauses while they run, and comes back on its own when the last one finishes.
The goal: in `claude agents`, the needs-you marker (the green star) sits only on sessions waiting for Russell.

## The one rule

**End a turn with a question only when the question is for Russell.**
A session that ends its turn shows as `blocked` and wears the marker, whoever the turn was for.
Anything meant for the other session goes in a result file.

## The loop

1. **Make the job folder** in the main checkout, never inside a worktree (a worktree's `.tmp/` is deleted when its session is removed): `<main checkout>/.tmp/results/<job>/`.
   Write `job.json` in it:
   ```json
   {"job": "<job>", "card_id": "<Trello card id, or empty>", "resume_command": "<see step 2>", "sealed": false, "workers": []}
   ```
2. **Capture your own resume command** with `schedule-resume.py` (see `resume-sessions/resume-on-completion.md`, step 1), and put the printed line in `resume_command`.
3. **Launch each worker**, one at a time, with its own brief:
   ```
   python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --title "<worker title>" --cwd "<repo dir>" --brief "<brief file>" --model <model> --worker-of "<job folder>"
   ```
   The launcher copies the brief into the job folder with the worker contract appended, and registers the worker (short id, slug, result path) in `job.json`.
   The contract tells the worker its result path, the result shape, and how to finish.
4. **Seal the job**, then pause at once:
   ```
   python "<session-mgr>/skills/resume-sessions/scripts/worker-done.py" --job "<job folder>" --seal
   ```
   Until the job is sealed, no worker resumes you, because workers still being launched would look like everyone is done.
   If the output says every worker already reported, skip the pause and go to step 6.
   Otherwise close with `end-session.py --pause` (the `session-mgr:close` command), which stops you and keeps your worktree for the resume.
   A paused coordinator is off the live list, and a grey "Stopped" row in Agent View.
5. **The last worker resumes you.**
   Each worker, when done, writes its result file, posts a one-line comment on the job's Trello card, and closes itself.
   The worker that finds every result in place runs your `resume_command` (under a lock, so two workers finishing together resume you once).
6. **Read the Summary and Open items of each result file**, and only those.
   Pull a large file listed under Artifacts only when a step needs it, and hand any big read to a subagent.
   Remove any worker still listed in `claude agents` with `claude rm <short id>` (a sweep for stragglers).
   A `failed` result goes to Russell with its Open items, or back out as a fresh worker, your call.
7. **Assemble everything** that reaches another person and stage it in one review round, then ask Russell for "send it" and end your turn.
   That question is for him, so the marker is correct.
8. **After he has sent**, correct the card's Start date, then close and remove yourself with `session-mgr:close` (no `--pause`).

Compact after each round of workers reports.
That is safe because every detail is on disk in the job folder.

## Result file format

Each worker writes `<job folder>/<worker slug>.md`, in the shape `WORKER_CONTRACT` in `spawn-session.py` defines.
Git carries code changes: a worker that changes a repo opens its PR as usual and the result file points to it.

## What each session shows in `claude agents`

| Situation | What the session does | Shows as |
|---|---|---|
| Worker is working | Works. | `working` |
| Worker needs something only Russell can give | Asks him and ends its turn. | `blocked`, the marker |
| Worker is done | Writes its result, runs `worker-done.py`. | Gone |
| Coordinator is waiting on workers | Seals, then closes with `--pause`. | Off the live list |
| Coordinator has everything staged for Russell | Asks for "send it" and ends its turn. | `blocked`, the marker |
| Coordinator's job is finished | Closes and removes itself. | Gone |

## When a worker hangs or dies

A worker that never writes its result never resumes you.
Pad the job card's Start date past the longest expected worker run (the `trello` skill's guard pattern), so the card surfaces through the drainer and catches the stall.
`job.json` lists the workers, and the ones without a result file are the ones that never reported.

## Why a worker, not a subagent

A subagent cannot talk to Russell, dies with its lead, and cannot root in another repo.
Use a worker session when Russell may need to step in.
Keep subagents for a heavy read that would bloat your own context.
