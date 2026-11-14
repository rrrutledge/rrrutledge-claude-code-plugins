# Writing a provider

A **provider** teaches the engine how to read and clear ONE source.
The engine's loop is provider-agnostic; it calls your provider's operations and never knows whether you used an API, an MCP, or a browser.
The contract is `engine/provider.md`; the providers in `../providers/` are complete worked examples to copy from.

## Where it lives
A provider is two files, an adapter and a prose file (`engine/provider.md`).
Put both in the plugin's **`providers/`** when the source is generic and shareable.
Put them in your machine's **`<local_dir>/providers/`** when the source is specific to one machine (a work-only internal system, a personal-only account) - that keeps one machine's accounts and systems out of the shared plugin.
The engine resolves each enabled provider by searching the plugin's `providers/` first, then `<local_dir>/providers/`, so either location runs through the same loop.

A machine enables the provider by name (with any config it needs) in `.claude/drainer.local.md` → `providers`.

## The adapter (code) - `<source>-adapter.py`
The skeleton below defines the hooks every adapter needs; `engine/provider.md` lists them and the optional ones.

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
Write each section `engine/provider.md` names as its own heading, so the worker can "do the item's provider's CLEAR step".
An email provider takes its shared rules from `providers/email-base.md` and writes only its own mechanics.

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
- **`trello-provider.md`** + **`trello-adapter.py`** - a config-driven provider that delegates all reads/mutations to the `trello` skill's `trello_utils.py` (Start-date source: returns cards whose Start is now-or-earlier, plus cards with no Start; usually little).
- **`physical-task-provider.md`** + **`physical-task-adapter.py`** - a **second-gate example**: a source whose ENUMERATE isn't just "is this due" (like Trello's Start) but ALSO "is there a live window for it right now" (a free calendar gap ahead of the next real commitment, recomputed every cycle).
  Copy this when a source needs eligibility to depend on live external state, not just a stored date.

Copy whichever is closest, change the mechanics for your source, and enable it as above (the entry key is the source name; its value holds any config the provider needs).

## Tips by source type
- **Browser steps** (Teams' mark-read CLEAR and reply drafts) run through **browser-chauffeur** and `message-draft`; the engine loop is identical, only the provider body differs (see teams).
- **API/MCP providers** are preferred where an API exists - same operations, cheaper/faster reads.
- **Delegating providers** (like Trello) hand reads/mutations to a sibling skill instead of re-implementing an API.

## Standing rules (AUTO-HANDLE)

A provider can carry an optional `## AUTO-HANDLE` section: standing decisions the worker executes on its own, reported in the digest rather than put to the user.
Each rule is a numbered item with a bold title.
The poller lifts the section into the triage prompt so the model can classify against it.

**Gate each rule with a `Trigger:`** (syntax in `docs/extending.md`, "Triggers").
The triage prompt is rebuilt every cycle, so an ungated rule pays its tokens on every poll - including the vast majority where nothing it cares about arrived.
The section is sent only when one of its rules could apply.
Gating is per **section**, not per rule, because rules cross-reference each other to disambiguate near-misses and dropping one of a pair would leave the other pointing at nothing.

**Personal rules go in an overlay, not the plugin.**
A rule naming a specific account, vendor or workspace belongs on the machine that has it, in `<local_dir>/providers/<source>-provider.local.md`.
Its `## AUTO-HANDLE` section is appended to the shipped provider's, so a machine attaches its own standing rules to a generic provider without forking it.
The shipped providers stay free of anyone's private integrations.

## Checklist
Each item checks one part of `engine/provider.md`'s contract:
- [ ] The adapter defines `Provider(ProviderBase)` with every required hook.
- [ ] The prose file has each required section by name.
- [ ] `.claude/drainer.local.md` has the `providers.<source>` entry.
- [ ] CLEAR is reversible and narrated.
- [ ] The provider holds no classification logic.
