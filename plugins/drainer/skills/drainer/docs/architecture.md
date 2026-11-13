# drainer - architecture

The map of how a drainer works and *why* it's built this way.
This file is orientation; each runtime contract is owned by the engine file the section names, so read the rule there.

## What a drainer is

A **drainer** takes a queue of human-touch items and, for each: reads the underlying conversation → figures out the **ACTION** (reply / do work / nudge / stop / nothing) → **does the action** - which often means actually KICKING OFF the work (open a PR, file a ticket, run a check, update a system), sometimes BEFORE any reply - and stages any reply **in the user's voice** → **clears** the item.
The value is figuring out and *doing* the action, not just replying; the deliverable may be the work itself, not a message.
What may go out, and when, is `engine/worker-core.md`'s "Sending".

Outlook / Teams / outreach are all the **same loop** with different **sources**.
"Gone" is per source: an email is deleted/archived; a Teams chat is marked read; an outreach card is advanced or bumped to a later follow-up day.

## The poller (the one model)

The drainer runs as one long-running process, the **poller**, that holds each source at **zero un-started actionable items** all day: its feed cycle judges new items, and its dispatch pass opens a worker for each one that needs one (`engine/poller-core.md`).

- **needs-you →** a headless worker opens at once, so the user starts acting right away, or the item goes to the live worker already on its conversation.
  A dynamic buffer of workers waiting for the user decides how many open at a time (`engine/poller-core.md` step 5).
- **auto-handle →** a standing-rule item; its worker acts autonomously, clears the source, and reports in the digest without interrupting the user.
- **fyi / junk →** queued for the daily digest's once-a-day review.
- **A person waiting 48 business hours →** the daily digest's acknowledgement sweep lists the conversation, and an end-of-day review session sends a short ack on the user's approval while the item keeps its place in the queue (`engine/ack-review.md`).

Who clears an item, and when, is `engine/poller-core.md` steps 5-6 for the poller and `engine/worker-core.md` §2d for the worker.

## The poller is code; AI is judgment

The feed cycle and dispatch pass are deterministic algorithms, so they live in scripts (`scripts/run-poller.py`, `keeper.py`, `pending.py`): cheaper and more reliable than asking an AI to follow them on every run.
AI is used only where a judgment is needed - triage, the security screen, and the worker itself - and `engine/poller-core.md` "Where AI is used" lists every call.

This is the **poller / worker split**: the poller enumerates and triages but never does an item's work; each needs-you item gets a **worker** that handles it to completion in its own headless background session, so context stays bounded and nothing is half-done.

## Fail-safe, never miss

Every mechanism's worst case is *redundant work*, never a *dropped item*, and completion is observed on the source rather than reported by the worker.
The mechanisms that make this hold are listed in `engine/poller-core.md`, "Fail-safe" and "Reconcile".

## Config comes from merged main, not the checked-out branch

The poller and digest read their config - `drainer.local.md` knobs, `context.md` and provider overlays (`local_dir`), `trello-boards.yaml`, and `initiatives/` - from a **drainer-owned git worktree pinned to `origin/main`**, not from the real repo's working tree.
`ensure_main_worktree` (in `scripts/drainer_config.py`) fetches and hard-resets that worktree to `origin/main` at the start of every run, and both entry points read config from it (`read_config(config_repo, runtime_root=source_repo)`).
Without this, config was read from whatever branch a human session left checked out at the repo root, so a merged triage rule or board change stayed dormant until someone restored main by hand.

Two things stay anchored to the **real** repo, on purpose:

- **Runtime state** (`seen.json` with its `_acks` ledger, `ack-due.json`, `provider-health.json`, `judged-verdicts.json`, `background-backoff.json`, `digest-queue.json`, `seeds/`, `items/`, `pending/`) - `runtime_dir` resolves against `runtime_root` (the real repo), so switching config to the worktree never migrates state or triggers a re-enumeration burst.
- **Worker/digest cwd** - worker sessions run in the real repo, keeping its machine-local, gitignored `.claude/settings.local.json` (permission auto-approvals, MCP enablement).
  A worker that must commit branches into its own worktree (per the `git-workflow` skill), so it never disturbs the config worktree.
  Repo-tracked config a worker needs (above all `initiatives/<slug>.md`) is read from the config repo, whose path the worker seed carries.

Resetting the config worktree every cycle is safe because nothing writes to it in place.
It is **fail-safe**: on any git error - offline, no `origin`, or a refresh race between the poller and the daily digest - `ensure_main_worktree` returns the real repo, so the drainer still runs (against possibly stale config) rather than not at all.

## Two layers: the plugin vs. what each machine injects

| In the plugin (generic) | Injected per machine |
| --- | --- |
| `engine/` - poller contract, worker procedure, triage rubric, provider contract; `scripts/` - the deterministic glue | `.claude/drainer.local.md` - which providers are active, per-provider config |
| `providers/` - one adapter and one prose file per source | `context.md` (in `local_dir`) - who the user is, their systems, standing personal dispositions |
| `docs/`, `templates/` | **credentials + tuning env vars** (OS store / env) - e.g. `DRAINER_TARGET_REVIEWABLE` |

The shipped providers name no one's accounts, vendors, or workspaces; a rule that does lives on the machine that has it, in `<local_dir>/providers/` (`docs/writing-a-provider.md`, "Personal rules go in an overlay").

## Where the details live

Each contract has one home:

- **Triage** - the four buckets and the `sender` and `chaser` tests → `engine/triage.md`.
- **Security screen** - the separate input-guardrail pass on every item triage didn't bucket junk → `engine/screen.md`.
- **Poller contract** - where AI is used, enumerate, dispatch, clear timing, reconcile, seen-state → `engine/poller-core.md`.
- **Worker procedure** - read brain → situational-check → do → draft → advance, plus "Sending" and "Close silently" → `engine/worker-core.md`.
- **Daily digest** - the once-a-day interactive readout and reconciliation sweep → `engine/digest-core.md`.
- **Acknowledgement review** - the end-of-day session that drafts and, on the user's approval, sends the 48-hour acks → `engine/ack-review.md`, with every deterministic step in `scripts/ack_sweep.py`.
- **Provider contract** - the interface a source implements → `engine/provider.md`; to add one, see `docs/writing-a-provider.md`.
- **Per-machine facts** - who the user is and where things live → each machine's `context.md`, started from `templates/context.example.md`.
- **Scheduling** - two Scheduled Tasks (the poller and the daily digest), registered version-independently so a routine `claude plugin update` is picked up automatically → `scripts/install-schedule.ps1`, `scripts/install-digest-schedule.ps1`, `scripts/launch-drainer.py`.
- **Settings and extension points** → `docs/extending.md`.
