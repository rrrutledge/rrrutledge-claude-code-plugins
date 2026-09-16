# drainer provider - the interface every provider implements

The engine is source-agnostic: it operates on generic *items* and delegates every source-specific step to that item's **provider**.
The providers a machine runs are listed in `.claude/drainer.local.md` → `providers`.
Classification is shared and lives in `engine/triage.md` - providers never restate it.

A provider is **two files** that sit together, because two different consumers drive a provider:

- **`providers/<source>-adapter.py`** - the **poller-facing code**.
  The deterministic poller (`scripts/run-poller.py`) loads it dynamically and drives it; this is where the source's *reading* mechanics live.
- **`providers/<source>-provider.md`** - the **worker-facing prose**.
  The AI worker tab reads it to act on one item; this is where *acting* and *clearing* live.

## Adapter (code) - what the poller drives

`<source>-adapter.py` defines `class Provider(ProviderBase)` (from `scripts/provider_base.py`) with:

- **`name`** - the source key as it appears in `drainer.local.md` → `providers`.
- **`enumerate(limit)`** - return up to `limit` candidate item dicts (newest-first; Start-now-or-earlier for date-gated sources like Trello).
  Surface an auth failure clearly here (the poller skips the source on error).
- **`stable_id(item)`** - a deterministic id, stable across cycles (the poller dedups on it via seen-state).
- **`capture(item, iid, runtime_dir)`** - write `<runtime_dir>/items/<iid>.json` (fields: `id`, `source`, `triage`, `kind`, `from`, `received`, `snippet`, deep-link `url`, `messageId`, body-file pointer) plus the body file, and return the `<iid>.json` path.
  The poller calls this for every dispatched item.

## Prose (markdown) - what the worker reads

`<source>-provider.md` MUST define a section, named exactly, for each operation the **worker** needs:

- **AUTH-GLANCE** - how to confirm signed-in and what to do if not (also informs the adapter's enumerate).
- **CAPTURE** - the item-file shape the worker can rely on (the adapter writes it; this documents it).
- **CLEAR** - clear/advance ONE item so it doesn't resurface (the source's "gone").
  Used by the worker's ADVANCE step and the digest's dispose step.
  Must be reversible and narrated.
- **JUNK-LEARNING** - what to propose for a junk item, **if anything**, in priority order (unsubscribe → source-app notification settings → inbox rule).
  A provider may propose nothing.
- **DRAFT-MODE** - the `message-draft` skill mode used when composing a reply for this source.

All providers share the one generic worker flow (`engine/worker-core.md`); there's no per-provider worker prompt to write.

## The buckets (shared)
Every provider classifies items per `triage.md` (the single rubric).
Providers don't redefine the buckets - they only implement how to ENUMERATE/CAPTURE/CLEAR for their surface.

## How to add a source
1. Write `providers/<source>-adapter.py` - `class Provider(ProviderBase)` with `name`/`enumerate`/ `stable_id`/`capture`.
2. Write `providers/<source>-provider.md` defining the prose operations above.
3. A machine enables it in `.claude/drainer.local.md` → `providers` (the entry key is the source name; its value holds any config the provider needs).
   The poller loads the adapter by name and the worker reads the prose - no changes to the loop.
   See `docs/writing-a-provider.md` for a worked walkthrough.
