# orphan-sessions provider - crash-recovered and retired Claude Code sessions (highest priority)

A provider for sessions the live-session registry confirms need resuming and aren't currently running: a session interrupted by a crash or forced restart (which never fires `SessionEnd`), or a session Russell started by hand in a terminal whose tab closed abruptly with the window/tab X (which fires `SessionEnd` with reason `"other"`, and the registry hook keeps rather than deregisters, because the entry carries a `host_pid` marking it as a hand-started terminal session the user parked and wants back).
It also covers a background session the background service retired for idling (or for low memory) without anyone closing it, found through the service's own job records even after the service drops it from `claude agents` and its registry entry is gone.
A deliberate end - `/exit`, `/clear`, an executed `claude stop`, or the `session-mgr:close` self-close - deregisters or stops the session instead, so it is not resumed.
The `resume-sessions` skill's Step 1 holds the full detection rules, including the wake backoff for a session Russell keeps leaving untouched.
Implements `../engine/provider.md`'s adapter contract; classify by `../engine/triage.md` - though in practice this source skips AI triage entirely (see below). id prefix: `orphan-`.

## Not like the other sources: no worker, no CLEAR, no draft

Every other provider's needs-you item opens a **fresh** worker session that reads `../engine/worker-core.md` and drafts a reply.
This source is different: resuming the session (via `run-poller.py`'s dedicated `spawn_resume()`, which reopens it as a background session with `claude --bg --resume <session_id>` in the session's own original `cwd`) IS the entire action.
The resumed session carries the full history under a new session id, and the launcher drops the old id from session-mgr's live-session registry so it never reads as crashed again.
A retired background session is woken in place instead (see the `resume-sessions` skill's Step 1).
Orphans take needs-you worker slots like any other item, ahead of every other source, so a backlog of them comes back a few at a time as the waiting pile drops below `target_reviewable` rather than all at once.
A resume that returns no session id stays unrecorded and is tried again next cycle.
The resumed session keeps the orphan's original name, read back from the orphan's transcript (its last custom title, else its AI-generated title), so several resumed sessions stay distinguishable and a second crash still reads the same name; a session with no recorded name is titled `Resume: <directory>`.
Russell continues in his own resumed conversation from there - there is nothing for a worker to read, act on, or clear, because the "item" isn't a message waiting for a reply, it's Russell's own interrupted work.
Because of this, the sections below that a normal provider's worker would use are N/A rather than omitted (per `../engine/provider.md`'s "MUST define" contract) - the sections still exist so it's clear they were considered, not forgotten.

## AUTH-GLANCE

N/A - no external account to sign into.
The registry (`~/.claude/session-mgr/live-sessions.json`) and the live-session list (`claude agents --json`) this source reads are local to the machine.

## Deterministic triage (not an AUTH-GLANCE-adjacent judgment call)

Every enumerated item is unconditionally `needs-you` - an orphaned session unconditionally needs resuming, no judgment involved.
`run-poller.py`'s pre-triage block stamps `_bucket="needs-you"`, `_kind="resume"`, `_complexity="simple"` for every `orphan-sessions` item before the AI triage call, the same tautology-bypass the `trello` adapter gets for its always-needs-you startable cards, so this source never reaches the AI triage call at all.

## CAPTURE (needs-you)

`items/<id>.json`: `{"id","source":"orphan-sessions","triage":"needs-you","kind":"resume",` `"session_id","cwd","started_at","ended_at","ts":"<ISO now>"}`.
`ended_at` is a retired background session's retirement time (null otherwise); the item id is built from it when present, else from `started_at`, so each later retirement of the same session is a new item.
No body file - there's nothing to display beyond the session's own `cwd` and when it ended; the resumed session carries its own full history once reopened.

## CLEAR

N/A - there is no source-side state to advance or mark read.
Dispatch (successfully launching the resumed session) is recorded as seen the same fail-safe-after-dispatch way every other source's needs-you item is; there is no separate CLEAR step because there is no separate source state pointing back at this item once the session is open.

## JUNK-LEARNING

N/A - a crash-orphaned session is never junk; there's no inbound noise to teach a filter against.

## DRAFT-MODE

N/A - this source never drafts anything.
Resuming reopens Russell's own prior conversation; whatever he does inside it (including any drafting) is that resumed session's own business, unrelated to this provider.
