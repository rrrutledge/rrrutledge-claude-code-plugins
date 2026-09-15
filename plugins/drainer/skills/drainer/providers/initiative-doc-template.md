# Initiative doc - authoring template (reference)

This is **reference guidance for authoring an initiative doc**, kept centrally in the drainer plugin so consumer repos don't each carry a copy.
See `trello-provider.md` → Initiatives / INITIATIVE-LOOKUP for how the worker resolves and reads these.

An initiative doc lives in a consumer repo at `initiatives/<slug>.md`.
The `initiatives/` folder is the registry - a file existing makes the initiative known.
A card resolves to it via a **yellow** Trello label whose name slugifies to `<slug>`, or a board's `initiative: <slug>` default.

There are **two shapes** - pick by where the content of record lives:

## Shape A - source stub (content lives elsewhere)
When the program is already documented somewhere (a Confluence page, a wiki, any URL), don't duplicate it.
The file is just a pointer; the worker fetches the `source:` for the real content.

```markdown
---
source: https://your-wiki/pages/12345/
---
<slug> — content of record is the linked page.
```

## Shape B - inline content (no external source)
When there's no external doc (e.g. a personal pod with no wiki), put the content in the file itself.
Keep it short - a contact should grasp it in under a minute.

```markdown
# <Initiative Name>

## What it is
One or two plain sentences a contact would understand.

## Why it matters
The problem this solves and why this contact should care.

## The ask
The concrete thing you want contacts to do (the action the outreach drives toward).

## Roles (optional)
If contacts fall into types that change the ask, define them here.

## Links
- Board:
- Further reading:
```

Either shape holds only **content** (what / why / the ask) - never per-stage outreach instructions.
That advancement logic is generic and lives once in `trello-provider.md` (STAGE-PLAYBOOK); the worker combines it with this content to draft for the card's current column.

To add an initiative: (1) create `initiatives/<slug>.md` in the consumer repo using shape A or B; (2) make a yellow Trello label that slugifies to `<slug>` and put it on the cards (or set the board default).
No central list to edit.
