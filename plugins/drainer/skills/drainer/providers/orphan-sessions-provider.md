# orphan-sessions provider — crash-recovered Claude Code sessions (highest priority)

A provider for sessions the live-session registry confirms need resuming and aren't currently
running: a session interrupted by a crash or forced restart (which never fires `SessionEnd`), or
a real user tab closed abruptly with the window/tab X (which fires `SessionEnd` with reason
`"other"`, and the registry hook keeps rather than deregisters, because the tab carries a
`host_pid` marking it as a launched interactive tab the user parked and wants back). A deliberate
end — `/exit`, `/clear`, or the `session-mgr:close` self-close — deregisters the session instead,
so it is not resumed. Implements
`../engine/provider.md`'s adapter contract; classify by `../engine/triage.md` — though in
practice this source skips AI triage entirely (see below). id prefix: `orphan-`.

## Not like the other sources: no worker, no CLEAR, no draft

Every other provider's needs-you item opens a **fresh** worker tab that reads
`../engine/worker-core.md` and drafts a reply. This source is different: resuming the
session (via `run-poller.py`'s dedicated `spawn_resume_tab()`, using `launch-session.ps1
-Resume <session_id>` in the session's own original `cwd`) IS the entire action. Russell
continues in his own resumed conversation from there — there is nothing for a worker to
read, act on, or clear, because the "item" isn't a message waiting for a reply, it's
Russell's own interrupted work. Because of this, the sections below that a normal
provider's worker would use are N/A rather than omitted (per `../engine/provider.md`'s "MUST
define" contract) — the sections still exist so it's clear they were considered, not
forgotten.

## AUTH-GLANCE

N/A — no external account to sign into. The registry
(`~/.claude/session-mgr/live-sessions.json`) and running-process scan this source reads are
local to the machine.

## Deterministic triage (not an AUTH-GLANCE-adjacent judgment call)

Every enumerated item is unconditionally `needs-you` — an orphaned session unconditionally
needs resuming, no judgment involved. `run-poller.py`'s pre-triage block stamps
`_bucket="needs-you"`, `_kind="resume"`, `_complexity="simple"` for every `orphan-sessions`
item before the AI triage call, the same tautology-bypass the `trello` adapter gets for its
always-needs-you startable cards, so this source never reaches the AI triage call at all.

## CAPTURE (needs-you)

`items/<id>.json`: `{"id","source":"orphan-sessions","triage":"needs-you","kind":"resume",`
`"session_id","cwd","started_at","ts":"<ISO now>"}`. No body file — there's nothing to
display beyond the session's own `cwd` and crash time; the resumed session carries its own
full history once reopened.

## CLEAR

N/A — there is no source-side state to advance or mark read. Dispatch (successfully
launching the resume tab) is recorded as seen the same fail-safe-after-dispatch way every
other source's needs-you item is; there is no separate CLEAR step because there is no
separate source state pointing back at this item once the tab is open.

## JUNK-LEARNING

N/A — a crash-orphaned session is never junk; there's no inbound noise to teach a filter
against.

## DRAFT-MODE

N/A — this source never drafts anything. Resuming reopens Russell's own prior conversation;
whatever he does inside it (including any drafting) is that resumed session's own business,
unrelated to this provider.
