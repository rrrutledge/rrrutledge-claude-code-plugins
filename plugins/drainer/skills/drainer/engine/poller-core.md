# drainer poller-core — the continuous keeper's contract

The drainer runs as a **continuous keeper**: a poller runs one short cycle every few
minutes and holds each source at **zero un-started actionable items**. The cycle is a deterministic
algorithm, so it is implemented as a **script** — `scripts/run-poller.py` — not prose an AI re-derives
each run. This doc is the *contract*: what the script does and where AI is (and isn't) used. The full
rationale is in `docs/superpowers/specs/2026-06-17-drainer-continuous-keeper-redesign.md`.

## Where AI is used (and where it isn't)

The script owns everything deterministic. AI is invoked for exactly three things:

1. **One triage call per new item per cycle** — `run-poller.py` builds the general-rules brain once
   per cycle (`engine/triage.md`, the local `context.md`, each provider's AUTO-HANDLE rules — gated
   against the whole cycle's batch, same as before), then sends `claude -p` one call per item with
   that brain as a byte-identical prefix and the single item's payload as the only variable part. Full
   model attention lands on one item at a time instead of splitting across a whole cycle's items, and
   the repeated stable prefix lets the API's automatic prompt caching serve it cheaply after the first
   item's call writes it — paid once per cycle, cheap delta per item. The first item runs alone (to
   finish writing the cache); the rest run concurrently, capped at `TRIAGE_PARALLEL_CALLS`.
2. **One security-screen call per new item per cycle triage didn't already bucket junk** — a **separate**
   `claude -p` pass from triage, its own focused brain (`engine/screen.md` + `context.md`), judging one
   question: is this content trying to manipulate the agent or induce an action against the user's
   interests? It is kept separate precisely so a classification call carrying four other dimensions can't
   crowd out the guardrail — the same attention-dilution that drove triage to one-call-per-item. Same
   cache pattern (first alone, rest concurrent) on the background account. On a flag the item loses all
   autonomy (forced to needs-you, never auto-handled or digested); an item it can't screen is held out of
   dispatch (fail-closed), so nothing is acted on unscreened.
   Junk is the one bucket the screen skips: it never resolves a pointer and never acts without the user's
   own review at digest time, so there's nothing there for the screen to protect against, and triage's own
   `kind: phishing` marking already carries the deceptive ones to the report-phishing digest action —
   flagging them again here would only force a duplicate, unnecessary escalation on content that's already
   correctly triaged. needs-you, auto-handle, and fyi (which *does* resolve a pointer autonomously, see
   `engine/digest-core.md` step 2) all still get screened. See the screen's dispatch handling in the cycle
   below.
3. **The per-item worker session** — each needs-you (or auto-handle) item opens a worker tab running
   `engine/worker-core.md` (the actual reply/work, draft-only; auto-handle runs the standing rule and
   self-clears without surfacing to the user). The worker re-applies the screen (`engine/screen.md`) to
   any content it resolves that the screen pass never saw (a pointer's real body).

Everything else — enumerate, stable ids, the seen-state check, the concurrency cap, capture,
spawn, record — is code. No AI re-implements the loop.

## What `run-poller.py` does each cycle

`python run-poller.py --repo <project> [--dry-run]`

1. **Read config** from `<project>/.claude/drainer.local.md`: enabled providers, `runtime_dir`.
   Also reads `target_open_tabs` from the `DRAINER_TARGET_OPEN_TABS`
   environment variable (default 12) — the only per-cycle throttle in the whole loop (see step 5).
2. **Reconcile** (`reconcile_unhandled()`) — re-queue any email item whose source object is still
   unhandled with no live worker session on it, so it re-enumerates below. See the section after this
   list for the rule and its guards.
3. **Per provider — enumerate everything eligible:** call the provider's enumerate (for outlook-graph,
   `mail.js --list-inbox --json --top=<ENUMERATE_PAGE_SIZE>` — read+unread, newest-first, no time
   window). There is no per-cycle work cap: every cycle asks every source for everything it currently
   has to offer (`ENUMERATE_PAGE_SIZE`, a generous constant in `run-poller.py`, bounds only how many
   rows one API call requests — a technical page size, not a throttle; a backlog bigger than that one
   page just carries into the next cycle). Compute each item's stable id and drop any already in
   seen-state (`scripts/seen-state.js`). Whatever remains all becomes triage/dispatch input this cycle;
   `target_open_tabs` in step 5 is what actually limits how much of it gets worked on at once.
4. **Triage** each new item with its own `claude -p` call → bucket (needs-you / auto-handle / fyi /
   junk) + kind + complexity (simple / complex). Every call this cycle shares one prompt prefix —
   `engine/triage.md`, the local `context.md`, and each enabled provider's AUTO-HANDLE section (so
   the model can recognize a standing-rule item; the rules live in the provider docs, surfaced here at
   triage time) — with only the one item's payload varying per call, so the model spends its full
   attention on that item instead of a whole cycle's batch at once.
4b. **Screen** each new item — except one step 4 already bucketed junk — with its own separate `claude -p`
   call (`engine/screen.md` + `context.md`) → `{flagged, reason}`. On a flag, the item's bucket is forced
   to needs-you and the flag is stamped onto the captured item, so it can never be auto-handled or
   digested and its worker leads with the warning. An item whose triage verdict was itself unavailable
   this cycle is still sent to screen (fail-closed defaults it to screen-needed, not skipped — only a
   confirmed `junk` verdict skips the call). **Fail-closed:** an item whose triage OR (non-skipped) screen
   call couldn't produce a verdict this cycle is left **unrecorded** and unhandled — held out of dispatch
   and retried next cycle — so nothing is ever acted on that wasn't screened. (Contrast triage's own
   fail-safe for an item the model silently *skips* inside a working call: that still defaults to
   needs-you. The fail-closed hold is for the call itself being unavailable.)
5. **Dispatch** (deterministic):
   - **needs-you** → hold this item if an earlier item from the **same correspondent** is still open (see
     "Hold by correspondent" below); otherwise, if live Claude Code tabs system-wide (`total_claude_tabs()`
     — every running `claude.exe` process: drainer worker tabs, the drainer itself, and any tab Russell
     opened by hand) is below `target_open_tabs`: capture to `items/<id>.json`, spawn a worker tab
     (`spawn-tab.cmd`) **with an explicit model chosen by complexity** (`worker_model` for simple,
     `worker_model_complex` for complex — so a worker never inherits a 1M-context session default the
     account can't use), then record seen **after** the spawn succeeds. At the target: leave it
     **unrecorded** so a later cycle picks it up (throttle + fail-safe). If the live-tab scan itself fails,
     the throttle is skipped entirely for that cycle (fail-safe: never block dispatch just because tabs
     couldn't be counted).
   - **auto-handle** → capture + spawn a worker tab too (it needs a browser to act), but the worker runs
     the standing rule autonomously and clears the source right away, so it resolves fast and is
     dispatched unconditionally, never throttled by `target_open_tabs`. It's recorded with its own
     `auto-handle` triage; the worker takes worker-core's auto-handle branch (act → CLEAR → queue a
     digest entry → close up) and never interrupts the user. The digest reports it under "Auto-handled."
   - **fyi / junk** → capture, add to the digest queue (`seen-state.js queue-add`), record seen, **then
     archive the source** (the provider's `clear`) so mail that Russell has effectively already
     dispositioned leaves his inbox at triage instead of sitting there as noise through to the digest. The
     archive runs **last**, after the item is safely queued and recorded, so a failed or absent archive
     never loses it: an item whose archive fails just stays in the inbox until the digest, which still
     clears it on review. A
     provider with a reversible-archive CLEAR (the inbox email providers) overrides `clear`; a provider
     whose CLEAR isn't a plain archive (e.g. outlook-graph-junk, whose CLEAR *un-junks* into the inbox) or
     that has no inbox returns `None` and stays in the inbox for the digest to clear. The triage-time
     archive changes only when an fyi/junk item leaves the inbox, not whether Russell reviews it in the
     digest.
6. **Clear timing.** Workers clear needs-you on completion. fyi/junk are archived at triage above (for
   providers whose CLEAR is a reversible archive); the daily digest still runs its review-gated CLEAR on
   each one, a harmless no-op for a message already archived here and the real clear for any provider that
   couldn't archive at triage.

## Reconcile: completion is read off the source, not off a receipt

Before enumerating, each cycle runs `reconcile_unhandled()` - the one place the poller decides whether
dispatched work actually finished. The rule is a single observable condition:

> An item whose source object is still unhandled, with no live worker session on it, is not handled.
> Drop its seen key so it re-enumerates.

The success case needs no signal at all: a worker that finished archived the message, so the message is
gone from the inbox and its item is excluded automatically. What is left is work nobody completed, and
one condition covers every way that happens - the tab was closed, the worker died, or its archive call
silently failed. Because the reconcile runs ahead of the enumerate, anything it re-queues is re-dispatched
in the same cycle.

Three guards keep it from re-queuing work that is fine:

- **the digest queue is excluded** - an fyi/junk item is archived at triage (dispatch step 6) so it's
  normally already gone from the inbox, and it never had a worker session; excluding the queue is the
  belt-and-suspenders that keeps even an item whose triage-time archive failed - still sitting in the
  inbox, awaiting the digest by design - from re-queuing on every cycle
- **a live session guid** (`claude --session-id <guid>`, recorded in `seeds/<id>.prompt.txt.session`)
  means a tab is open on the item: being worked, or parked for Russell. Liveness is the whole test -
  an open tab is left alone however long it's up, so there is no timeout.
- **the launch grace** (`orphan_grace_minutes`) covers the window in which a just-dispatched worker
  hasn't written its `.session` file yet and so briefly looks session-less

Both fail-safes point at reconciling nothing rather than re-queuing live work: a process scan that
can't run skips the cycle, and a provider whose inbox listing fails skips that provider (an empty id
set would otherwise read as "every item is archived"). `reconciled.json` memoizes the items already
proven gone from the inbox, so the scan's per-cycle cost stays flat as seen-state grows; deleting it
just makes the next cycle re-derive the set.

**Email only.** `still_in_inbox_ids()` is implemented on the gmail and outlook-graph adapters and returns
`None` everywhere else, so Slack, Teams, Trello and orphan-sessions are skipped and resurface on their
own source's terms. A source with an inbox deeper than the 500-message listing hides its oldest messages
from the check, which costs a missed catch and never causes a wrong re-queue.

**Per-provider isolation + health.** Each provider's enumerate is wrapped: a failure (expired creds,
network/API blip, or a missing helper at adapter-load) raises a typed `ProviderError` that the poller
catches, so one dead source never aborts the cycle — the others still drain. Every cycle records each
provider's outcome to `<runtime_dir>/provider-health.json` (`consecutive_failures`, `last_error`,
`last_error_kind` [`auth` = transient/self-heals, `config` = deploy error], `last_error_ts`,
`last_ok_ts`). Because the poller is headless, this file is how a silently-dead provider becomes
visible: the daily digest reads it and surfaces any provider with a sustained failure streak so Russell
knows to refresh the credential. (Dry-run doesn't write it — it's often run without the live creds.)

**Dry-run** (`--dry-run`) does steps 1–5 and prints a triage report (counts + per-item bucket + intended
action, including any held at the cap) plus the count of items the reconcile would re-queue, with no
spawns, no queueing, no records, no clears, and no re-queues.

## Hold by correspondent: one open item per person, so one tab reads them all

Two items from the same person close together should be handled by **one tab reading both**, not two
tabs racing independently and possibly drafting two separate replies.
This is the general case behind exact-duplicate notifications (a stack of Securus "new mail" notices for
the same incarcerated contact) as well as a real person sending two genuinely different emails minutes
apart.
The fix is not to detect sameness - that needs content judgment and is fragile.
It is to **hold a second item from a correspondent out of dispatch while an earlier item from them is
still open**, and let the worker's existing situational-check (it reads the whole thread across
Inbox/Archive/Deleted Items before drafting) do the grouping once, in one tab, with full context.

At dispatch, before spawning a worker for a needs-you item, the poller compares its **correspondent
identity** against the correspondents that currently have an open item.
If one matches, the item is left **unrecorded** - the exact "leave it for a later cycle" pattern the
`target_open_tabs` throttle uses - so it just waits in the source and re-evaluates next cycle.

"Currently has an open item" is recomputed **every cycle from live state**, never a persisted "waiting"
flag - a hold that could get stuck waiting forever (a crashed worker, a bug) would be worse than the
problem it solves.
The signal is the same liveness test `reconcile_unhandled` trusts: a correspondent is held-open when a
live `claude --session-id` worker is running on one of their captured items (`open_correspondents`),
plus any correspondent already dispatched earlier in this same cycle (so even two duplicates arriving in
one cycle don't both spawn - the first claims the identity, the rest wait).
If a worker dies without clearing, reconcile re-queues *its* item within one cycle, and on that same
cycle the hold behind it also goes false - it is read off the same signal, not a separate flag.
Worst case is one poll cycle's delay, never indefinite starvation.
A live-session scan that fails yields no held-open set, so a cross-cycle hold **fails open** (dispatch
proceeds); the same-cycle dedup still holds a burst.

**Correspondent identity is per-provider, not always the envelope From address.**
For direct email the From address *is* the correspondent, so two messages from the same person share it.
For a **relay** sender the From address does not identify who wrote - Securus/JPay's shared
`donotreply@jpay.com` fronts every incarcerated contact, LinkedIn notification mail is from LinkedIn
itself - so keying on it would wrongly collapse unrelated people onto one identity.
For a recognized relay the identity is instead the name parsed out of the notification (for Securus, the
"Message from: &lt;NAME&gt;" line), namespaced so it can never collide with a real address; when the name
can't be extracted the item is treated as having **no** identity (never held) rather than falling back to
the shared address, so two different real people are never merged.
The per-relay recognition and extraction live in `provider_base.RELAY_CORRESPONDENTS`; each captured
email item persists its resolved `correspondent` in `items/<id>.json` so `open_correspondents` can read
it back off a live session. New relays plug in by adding a registry entry.

## Fail-safe (why "process twice" is fine, "miss once" never happens)

- Seen-state is a separate id store, not the read/unread flag — losing it re-processes (safe).
- A seen-id is recorded only **after** dispatch succeeds — an aborted cycle loses no item.
- Workers are idempotent: a duplicate tab's situational-check resolves quietly. No overlap lock.
- Completion is observed on the source, never reported: an item is done because its source object is
  handled, so a worker that dies, is closed, or fails its own archive call re-queues rather than vanishing.

## Provider-agnostic orchestration
`run-poller.py` reads which providers are enabled from `drainer.local.md` and **dynamically loads each
one's adapter** from `providers/<name>-adapter.py` (beside its prose `providers/<name>-provider.md`).
An adapter implements `provider_base.ProviderBase` — `enumerate` / `stable_id` / `capture` — and is the
only place a source's mechanics live (for outlook-graph: `mail.js`, the Graph id scheme, the captured
item shape). The poller's loop (drop-seen, cap, triage, dispatch, record) holds no source or tool name;
a provider enabled in config without an adapter file is skipped with a note. New sources (Gmail, Trello,
…) plug in by adding an adapter; the triage rubric, seen-state, cap, and worker flow are unchanged.
