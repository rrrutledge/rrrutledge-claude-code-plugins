# Writing a provider

A **provider** teaches the engine how to read and clear ONE source.
The engine's loop is provider-agnostic; it calls your provider's operations and never knows whether you used an API, an MCP, or a browser.
The contract is `engine/provider.md`; the providers in `../providers/` are complete worked examples to copy from.

## Where it lives - two files that sit together
A provider is **two files** that sit together, because two consumers drive it:

- **`<source>-adapter.py`** - **code** the deterministic poller loads and drives (how to *read* the source).
- **`<source>-provider.md`** - **prose** the AI worker reads to act on one item (how to *act* and *clear*).

Put both in the plugin's **`providers/`** when the source is generic and shareable.
Put them in your machine's **`<local_dir>/providers/`** when the source is specific to one machine (a work-only internal system, a personal-only account) - that keeps it out of the shared, identity-free plugin.
The engine resolves each enabled provider by searching the plugin's `providers/` first, then `<local_dir>/providers/`, so either location runs through the same loop.

A machine enables the provider by name (with any config it needs) in `.claude/drainer.local.md` → `providers`.
All providers share the one generic worker flow (`engine/worker-core.md`).

## The adapter (code) - `<source>-adapter.py`
Define `class Provider(ProviderBase)` (from `scripts/provider_base.py`) exposing three methods the poller calls.
The poller loads it by name; no source mechanics live in the poller.

```python
import os, sys
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from provider_base import ProviderBase, run_node, slug

class Provider(ProviderBase):
    name = "<source>"                       # matches the drainer.local.md providers key

    def enumerate(self, limit):             # newest-first candidate item dicts, up to `limit`
        ...                                 # surface auth failures here; poller skips on error
        return items

    def stable_id(self, item):              # deterministic, stable across cycles (seen-state dedups on it)
        return f"{self.name}-..."

    def capture(self, item, iid, runtime_dir):  # write items/<iid>.json (+ body file); return the json path
        ...
        return json_file
```

## The prose (`<source>-provider.md`) - what the worker reads
Implement each as a named section so the worker can "do the item's provider's CLEAR step":

1. **AUTH-GLANCE** - confirm access cheaply; say what to do if not signed in.
2. **CAPTURE** - the `<id>.json` shape the worker can rely on (the adapter writes it; this documents it).
3. **CLEAR** - make ONE item "gone" (reversible, narrated).
4. **JUNK-LEARNING** - what to propose for junk, if anything, in priority order (unsubscribe → source-app notification settings → inbox rule).
5. **DRAFT-MODE** - the `message-draft` mode for replies for this source.

Classification is NOT a provider concern - every item is judged by `engine/triage.md`.

## Worked examples - the shipped providers
Each is a complete implementation to copy from:
- **`outlook-rest-provider.md`** + **`outlook-rest-adapter.py`** - a **REST two-file example** for an Outlook mailbox via the `ms-rest` skill's `outlook-mail.js` (token sniffed from the live Outlook web session; reads whatever account is signed in).
  Adapter: `enumerate` / `get` reads; prose: `delete` clear, Outlook-rule junk-learning, `message-draft` `outlook` mode.
  Copy this when the sibling skill needs a one-time browser sniff but reads run REST.
- **`outlook-graph-provider.md`** + **`outlook-graph-adapter.py`** - the **API two-file example** (personal Outlook.com via the Microsoft Graph API through the `ms-graph` skill).
  Adapter: `mail.js --list-inbox --json` enumerate, the Graph id scheme, `--show` capture.
  Prose: `--delete` clear, `--reply` draft, unsubscribe/rule junk-learning.
  Copy this when wrapping any pure-API/MCP source.
- **`teams-provider.md`** + **`teams-adapter.py`** - a **REST-read / browser-clear two-file example** via the `teams` skill's `teams-chat.js`: REST `enumerate`/`messages` reads, but mark-read CLEAR and reply DRAFT stay browser-driven (Teams' `isRead` flips only on a real open; no draft API).
  Includes the Teams footguns and the meeting-recording container case.
- **`trello-provider.md`** - a config-driven provider that delegates all reads/mutations to the `trello-outreach` skill (Start-date source: returns cards whose Start is now-or-earlier, plus cards with no Start; usually little).
- **`physical-task-provider.md`** + **`physical-task-adapter.py`** - a **second-gate example**: a source whose ENUMERATE isn't just "is this due" (like Trello's Start) but ALSO "is there a live window for it right now" (a free calendar gap ahead of the next real commitment, recomputed every cycle).
  Copy this when a source needs eligibility to depend on live external state, not just a stored date.

Copy whichever is closest, change the mechanics for your source, and a machine enables it in `.claude/drainer.local.md` → `providers` (the entry key is the source name; its value holds any config the provider needs).

## Tips by source type
- **Browser providers** drive everything via **browser-chauffeur** and draft via `message-draft`; the engine loop is identical, only the provider body differs (see outlook/teams).
- **API/MCP providers** are preferred where an API exists - same operations, cheaper/faster reads.
- **Delegating providers** (like Trello) hand reads/mutations to a sibling skill instead of re-implementing an API.

## Standing rules (AUTO-HANDLE)

A provider can carry an optional `## AUTO-HANDLE` section: standing decisions the worker executes on its own, reported in the digest rather than put to the user.
Each rule is a numbered item with a bold title.
The poller lifts the section into the triage prompt so the model can classify against it.

**Gate each rule with a `Trigger:`.**
The triage prompt is rebuilt every cycle, so an ungated rule pays its tokens on every poll - including the vast majority where nothing it cares about arrived.
A trigger is a cheap precondition checked in Python against the items already in hand:

```markdown
   - **Trigger:** `from=fred@fireflies\.ai; subject=^Your meeting recap`
```

Semicolon-separated `field=regex` pairs, ANDed, matched case-insensitively against that provider's items in the current batch (`from`, `subject`, `preview` - the raw enumerate fields).
The section is sent only when one of its rules could apply.
Gating is per **section**, not per rule, because rules cross-reference each other to disambiguate near-misses and dropping one of a pair would leave the other pointing at nothing.

A rule with no trigger, or one whose trigger can't be parsed, loads unconditionally - the failure mode of a bad trigger is a wasted prompt, never a rule that silently stops being enforced.

**Personal rules go in an overlay, not the plugin.**
A rule naming a specific account, vendor or workspace belongs on the machine that has it, in `<local_dir>/providers/<source>-provider.local.md`.
Its `## AUTO-HANDLE` section is appended to the shipped provider's, so a machine attaches its own standing rules to a generic provider without forking it.
The shipped providers stay free of anyone's private integrations.

## Checklist
- [ ] `providers/<source>-adapter.py` defines `Provider(ProviderBase)` with `name`/`enumerate`/ `stable_id`/`capture`.
- [ ] `providers/<source>-provider.md` defines the prose ops (AUTH-GLANCE, CAPTURE shape, CLEAR, JUNK-LEARNING, DRAFT-MODE) by name.
- [ ] `.claude/drainer.local.md` has the `providers.<source>` entry.
- [ ] CLEAR is reversible and narrated.
- [ ] No classification logic in the provider (uses `engine/triage.md`).
