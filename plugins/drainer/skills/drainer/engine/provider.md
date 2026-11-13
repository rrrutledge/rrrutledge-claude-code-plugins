# drainer provider - the interface every provider implements

The engine is source-agnostic: it operates on generic *items* and delegates every source-specific step to that item's **provider**.
The providers a machine runs are listed in `.claude/drainer.local.md` → `providers`.
This file is the provider contract; `docs/writing-a-provider.md` walks through writing one against it.

Classification is shared and lives in `engine/triage.md`; a provider never restates or redefines the buckets, it only implements how to enumerate, capture, and clear for its surface.

A provider is **two files** that sit together, because two different consumers drive a provider:

- **`providers/<source>-adapter.py`** - the **poller-facing code**.
  The deterministic poller (`scripts/run-poller.py`) loads it dynamically and drives it; this is where the source's *reading* mechanics live.
- **`providers/<source>-provider.md`** - the **worker-facing prose**.
  The AI worker session reads it to act on one item; this is where *acting* and *clearing* live.

All providers share the one generic worker flow (`engine/worker-core.md`); there's no per-provider worker prompt to write.

## Adapter (code) - what the poller drives

`<source>-adapter.py` defines `class Provider(ProviderBase)` (from `scripts/provider_base.py`).
Every adapter defines these:

- **`name`** - the source key as it appears in `drainer.local.md` → `providers`.
- **`enumerate(limit)`** - return up to `limit` candidate item dicts (newest-first; Start-now-or-earlier for date-gated sources like Trello).
  Surface an auth failure clearly here (the poller skips the source on error).
- **`stable_id(item)`** - a deterministic id, stable across cycles (the poller dedups on it via seen-state).
- **`capture(item, iid, runtime_dir)`** - write `<runtime_dir>/items/<iid>.json` (fields: `id`, `source`, `triage`, `kind`, `from`, `received`, `snippet`, deep-link `url`, `messageId`, body-file pointer) plus the body file, and return the `<iid>.json` path.
  The poller calls this for every dispatched item.

`ProviderBase` gives every other hook a default an adapter overrides when its source supports it: among them `clear` (the poller's own clears, `engine/poller-core.md` steps 5-6), `still_in_inbox_ids` (reconcile), `conversation_key` and `correspondent` (routing and holds), `triage_text`, `screen_signal`, and `rescue`.
Each hook's docstring in `provider_base.py` says what it does and what its default is.

## Prose (markdown) - what the worker reads

`<source>-provider.md` defines a section, named exactly, for each operation the worker or the daily digest needs:

- **AUTH-GLANCE** - how to confirm signed-in and what to do if not (also informs the adapter's enumerate).
- **CAPTURE** - the item-file shape the worker can rely on (the adapter writes it; this documents it).
- **CLEAR** - clear/advance ONE item so it doesn't resurface (the source's "gone").
  Used by the worker (`engine/worker-core.md` §2d and step 6) and the daily digest's dispose step.
  Must be reversible and narrated.
- **JUNK-LEARNING** - what to propose for a junk item, **if anything**, in the provider's own priority order (the email providers share `providers/email-base.md`'s).
  A provider may propose nothing.
- **DRAFT-MODE** - the `message-draft` skill mode used when composing a reply for this source.

And, where the source needs one:

- **SITUATIONAL-CHECK** - how to pull the full current conversation for this source, for worker-core §2.
- **AUTO-HANDLE** - standing rules the worker executes on its own (`engine/auto-handle.md`); the poller lifts this section into the triage prompt, gated by `Trigger:` lines (`docs/extending.md`, "Triggers").
- **REPORT-PHISHING** - how to report a `kind: phishing` item to the mail provider (`engine/digest-core.md` step 3).
- **RESOLVE-A-POINTER** - the source's mechanics for recovering a pointer's link (`engine/pointers.md`).
