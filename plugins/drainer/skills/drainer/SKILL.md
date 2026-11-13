---
skill: drainer
description: Drain human-touch queues to zero - email, Teams, Slack, Trello outreach - with one shared loop. For each item: read the thread, do the action it implies (open a PR, file a ticket, run a check, update a system), draft any reply in the user's voice (staged for review), then clear the item. Use to drain the inbox / Teams / Slack / outreach to zero. Per-machine settings come from .claude/drainer.local.md.
instructions: |-
  ## Drainer

  A **drainer** holds a queue of human-touch items at zero: for each one it reads the conversation, does the action the item implies, drafts any reply, and clears the item.
  `docs/architecture.md` describes the model and why it is built this way; this file is the map to where each part lives.

  ### 1. Load per-machine settings FIRST
  Read **`.claude/drainer.local.md`** in the current project (YAML frontmatter): which `providers` are active, per-provider config (e.g. Trello board ids), and `local_dir` (a folder holding `context.md`).
  If it's missing, copy `templates/drainer.local.example.md` to `.claude/drainer.local.md` and help the user fill it in.

  Then read `context.md` (from `local_dir`) and the active providers.

  ### 2. Where each part lives
  Each file below is the one home for what it names; this list states none of their rules.
  - **`engine/poller-core.md`** - the poller: where AI is used, its feed cycle and dispatch pass, when it clears an item, reconcile, and the fail-safes.
  - **`engine/triage.md`** - the one rubric: the four buckets, plus `sender` and `chaser`.
  - **`engine/screen.md`** - the security screen every non-junk item passes, and what a flag does.
  - **`engine/worker-core.md`** - the per-item worker's needs-you flow, including when anything is sent ("Sending") and how an item resolves without Russell ("Close silently").
    It names the situational files at the point each one applies: `engine/pointers.md` (resolving a pointer to its real content) and `engine/browser-gate.md` (a browser gate only Russell can clear).
  - **`engine/auto-handle.md`** - an `auto-handle` item's autonomous branch and the disposition values, named directly in the worker's seed.
  - **`engine/digest-core.md`** - the daily digest that empties the fyi/junk queue with Russell's review (`scripts/run-digest.py`).
  - **`engine/ack-review.md`** - the end-of-day acknowledgement batch for a person left waiting 48 business hours.
  - **`engine/provider.md`** - the contract a provider implements.
  - **`providers/`** - one adapter and one prose file per source; `providers/email-base.md` holds the rules every email provider shares.
  - **`docs/extending.md`** - per-machine settings, triggers, credentials, and setting up a new machine; **`docs/writing-a-provider.md`** - adding a source.
---
