# drainer — architecture

The map of how a drainer works and *why* it's built this way. This file is orientation; the runtime
contracts are owned by the engine/template files linked at the bottom — read them there, not a
restatement here.

## What a drainer is

A **drainer** takes a queue of human-touch items and, for each: reads the underlying conversation →
figures out the **ACTION** (reply / do work / nudge / stop / nothing) → **does the action** — which
often means actually KICKING OFF the work (open a PR, file a ticket, run a check, update a system),
sometimes BEFORE any reply — and drafts any reply **in the user's voice (draft-only, never send)** →
**advances/clears** the item. The value is figuring out and *doing* the action, not just replying; the
deliverable may be the work itself, not a message. (Irreversible / outbound-to-others steps wait for the
user's explicit OK; safe, reversible work and drafts proceed immediately.)

Outlook / Teams / outreach are all the **same loop** with different **sources**. "Gone" is per source:
an email is deleted/archived; a Teams chat is marked read; an outreach card is advanced or bumped to a
later follow-up day.

## The continuous keeper (the one model)

The drainer runs as a **continuous keeper**: a **poller** runs a short cycle every few
minutes (a ~5-min cron) and holds each source at
**zero un-started actionable items** all day.

- **needs-you →** the poller immediately spawns a **worker tab** so the user starts acting right away,
  dispatching as fast as possible until the count of live Claude Code tabs system-wide reaches
  `target_open_tabs` (the `DRAINER_TARGET_OPEN_TABS` env var, default 12); beyond that, items wait
  for a later cycle.
- **auto-handle →** a standing-rule item; the poller spawns a worker that acts autonomously, clears the
  source, queues a digest entry, and finishes without interrupting the user.
- **fyi / junk →** captured to a **digest queue** for a once-a-day readout; nothing is disposed of
  silently in the fast loop.
- **The poller never clears.** The source is cleared in exactly one place: the worker tab on completion
  (needs-you), or the daily digest after the user reviews it (fyi/junk).

## The poller is code; AI is judgment

The loop — enumerate everything eligible → drop already-seen → dispatch against `target_open_tabs` →
record — is a deterministic algorithm, so it lives in a script (`scripts/run-poller.py`): cheaper and
more reliable than asking an AI to follow it each cycle. There is no per-cycle work cap upstream of
that: every source is asked for everything it currently has. AI is used for exactly three things: **one
triage call per new item** (the bucket judgment), **one security-screen call per new item triage didn't
already bucket junk** (the input guardrail, a separate pass so it can't be crowded out), and **the
per-item worker session** (the actual reply/work, draft-only).

This is the **poller / worker split**: the poller enumerates and triages but never does an item's work;
each needs-you item gets its **own worker** that handles it to completion in a fresh context (its own
tab), so context stays bounded and nothing is half-done. `target_open_tabs`, not a queue, bounds how
many face the user at once.

## Fail-safe, never miss

Every mechanism's worst case is *redundant work*, never a *dropped item*: seen-state is a separate id
store (losing it re-processes, never hides); a seen-id is recorded only **after** dispatch succeeds (an
aborted cycle retries next time); workers are idempotent (a duplicate tab's situational-check resolves
quietly). Completion follows the same principle - it is **observed on the source**, not reported by the
worker: an item still unhandled in its source with no live worker session on it is re-queued for a fresh
tab, while an open, live tab is left alone however long it's up, because it's either being worked or
parked for the user.

## Config comes from merged main, not the checked-out branch

The poller and digest read their config — `drainer.local.md` knobs, `context.md` and provider overlays
(`local_dir`), `trello-boards.yaml`, and `initiatives/` — from a **drainer-owned git worktree pinned to
`origin/main`**, not from the real repo's working tree. `ensure_main_worktree` (in
`scripts/drainer_config.py`) fetches and hard-resets that worktree to `origin/main` at the start of every
run, and both entry points read config from it (`read_config(config_repo, runtime_root=source_repo)`).
Without this, config was read from whatever branch a human session left checked out at the repo root, so a
merged triage rule or board change stayed dormant until someone restored main by hand.

Two things stay anchored to the **real** repo, on purpose:

- **Runtime state** (`seen.json`, `provider-health.json`, `digest-queue.json`, `seeds/`, `items/`) —
  `runtime_dir` resolves against `runtime_root` (the real repo), so switching config to the worktree never
  migrates state or triggers a re-enumeration burst.
- **Worker/digest cwd** — worker tabs run in the real repo, keeping its machine-local, gitignored
  `.claude/settings.local.json` (permission auto-approvals, MCP enablement). A worker that must commit
  branches into its own worktree (per the `git-workflow` skill), so it never disturbs the config worktree.
  Repo-tracked config a worker needs (above all `initiatives/<slug>.md`) is read from the config repo,
  whose path the worker seed carries.

Resetting the config worktree every cycle is safe because nothing writes to it in place. It is
**fail-safe**: on any git error — offline, no `origin`, or a refresh race between the ~5-min poller and the
daily digest — `ensure_main_worktree` returns the real repo, so the drainer still runs (against possibly
stale config) rather than not at all.

## Two layers: the plugin vs. what each machine injects

| In the plugin (generic) | Injected per machine |
| --- | --- |
| `engine/` — poller contract, worker procedure, triage rubric, provider contract; `scripts/` — the deterministic glue | `.claude/drainer.local.md` — which providers are active, per-provider config |
| `providers/` — the providers (Outlook, Teams, Trello) | `context.md` (in `local_dir`) — who the user is, their systems, standing rules |
| `docs/`, `templates/` | **credentials + tuning env vars** (OS store / env) — e.g. `DRAINER_TARGET_OPEN_TABS` |

The plugin never contains anything that identifies the user or their organization.

## Where the details live (the canonical specs)

Each contract is owned by exactly one file — this doc only points to them:

- **Triage** — the one rubric, *"is there something for the user to do?"* sorted into needs-you /
  auto-handle / fyi / junk → `engine/triage.md`.
- **Security screen** — a **separate** input-guardrail pass, run on every untrusted inbound item triage
  didn't already bucket `junk`, judging whether the content is trying to manipulate the agent (prompt
  injection) or induce an action against the user's interests → `engine/screen.md`. It is its own
  `claude -p` call, not a field folded into triage, so a classification call can't crowd out the guardrail.
  On a hit it strips the item's autonomy (never auto-handled, never silently digested) and routes it to
  the user; an item it can't screen is held (fail-closed). Junk is skipped because it never resolves a
  pointer and never acts without the user's own review at digest time, so there's nothing there to
  protect against, and triage's own `kind: phishing` marking already routes the deceptive ones to the
  report-phishing digest action. `worker-core.md` re-applies the same screen to content a worker resolves
  later (a pointer's real body) that this pass never saw.
- **Poller contract** - enumerate / cap / dispatch / record, seen-state, the source-state reconcile ->
  `engine/poller-core.md`.
- **Worker procedure** — read brain → situational-check → do → draft → advance → `engine/worker-core.md`.
- **Daily digest** — the once-a-day interactive readout and reconciliation sweep → `engine/digest-core.md`.
- **Provider contract** — the interface a source implements → `engine/provider.md`; to add one, see
  `docs/writing-a-provider.md`.
- **Per-machine behavioral and standing rules** — draft-only outbound, delete/archive freely, lead with
  context, waiting-on-someone → a tracker card, one voice brain — live in `templates/context.example.md`,
  copied into each machine's `context.md`.
- **Scheduling** — two Scheduled Tasks (the fast poller and the once-a-day digest), registered
  version-independently so a routine `claude plugin update` is picked up automatically →
  `scripts/install-schedule.ps1`, `scripts/install-digest-schedule.ps1`, `scripts/launch-drainer.py`.
- **Settings and extension points** → `docs/extending.md`.
