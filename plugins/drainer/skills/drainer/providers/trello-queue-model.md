# trello queue model - how cards enter and rank in the drain (adapter-facing)

This is the **poller/adapter-facing** half of the Trello provider: how the adapter reads config, decides which cards are startable, and ranks them.
A worker acting on one card never does any of this - it receives the card already selected, parsed (`contacts`, `channelLabel`, `initiative`), and ranked in its item JSON, so `trello-provider.md` (the worker-facing half) does not carry this.
Read this when changing the adapter (`trello-adapter.py`), the queue policy (`provider_base.band_rank`), or the config format.

## Config
- **Boards** - the single source of truth is `<repo>/trello-boards.yaml` (a `boards:` list of `{name, id}`), the same registry the `trello-outreach` skill reads.
  The drainer drains **every** board in it, so adding a board is a one-file edit.
  (Fallback: a `providers.trello.boards` list in `.claude/drainer.local.md` if no registry file exists.)
  - **Format the adapter parses:** the poller runs on bare stdlib Python (no PyYAML), so the adapter extracts boards by a fixed indent convention rather than full YAML - each board is `  - name:` at **two-space** indent with its `    id:` at **four-space** indent.
    Keep that shape.
    Any deeper per-board fields (`purpose`, `template_cards`, …) are free-form and ignored by the drainer.
- **Initiatives** - shared outreach programs many cards belong to.
  The **registry is the `initiatives/` folder** in the repo: one `initiatives/<slug>.md` per program (a file existing ⇒ the initiative exists - no central list to maintain).
  The file either holds the content inline or, via a `source:` frontmatter pointer, redirects to where the content lives (a Confluence/other URL).
  See `initiative-doc-template.md` (in this skill) for the two shapes - source-stub vs inline-content.
  A card is tagged with an initiative two ways (a per-card tag wins over the board default):
  - **Per-card** - a Trello **yellow label** (yellow is the initiative color).
    The adapter resolves it: label name → slug → `initiatives/<slug>.md`.
    The yellow label is held out of contact classification, so it's never mistaken for a contact name.
  - **Board default** - an `initiative: <slug>` field on a board entry in `trello-boards.yaml` (every card on that board inherits it - best when the whole board is one program).
    The four-space `initiative:` field is parsed by the adapter alongside `id`.
    The adapter writes the resolved slug as `initiative` on the item.
    See `trello-provider.md`'s INITIATIVE-LOOKUP for how the worker loads the content and STAGE-PLAYBOOK for the generic per-stage activity.
- Drainer knobs in `.claude/drainer.local.md` → `providers.trello`:
  - `skip_lists` - terminal/parking lists to ignore (e.g. Abandoned, Finished, Adopted, Templates).
  - `skip_labels` - labels whose cards are suppressed (default `[Blocked]` → hides ⛔ Blocked cards).
    Matched as a case-insensitive substring, so `blocked` catches `⛔ Blocked`.
  - `status_labels` - dependency-state labels held out of contact classification (default `[Blocked, Waiting]`), so a ⛔/⏳ label is never read as a person's name.
  - `label_vocab` - `{channels: [...], features: [...]}`; any label not in those is a contact name.
- Credentials: `TRELLO_API_KEY` / `TRELLO_TOKEN` in the environment (used by the `trello` skill).

## ENUMERATE
Via the `trello` skill, list cards across the configured boards that sit in an **active** list (not in `skip_lists`), are **not** wearing a `skip_labels` label (⛔ Blocked), and are **startable** - Start now-or-earlier, or no Start at all.
A future Start is the only thing that holds a card back.
Rank a card by its **Start date** (its go-live), most recent first, and an undated card by its **creation date** (decoded from the card's ObjectId).

Rank is `(priority band, level band, referral band, date)`, all descending - level breaks ties within a band, referral breaks ties within a band+level, date breaks ties within a band+level+referral.
The order of those three bands is defined in exactly one place, `provider_base.band_rank`, which both this adapter's enumerate and the poller's cross-source sort call; to reorder the queue (e.g. put referral back ahead of level), change the tuple there.
A Job Search Outreach card's band reflects its card type.
A **person follow-up card** carries the **`👤 Contact`** label and is pinned **one band above** neutral, so a live contact thread is worked ahead of email/Slack and every application - following up with an existing contact is the highest-value move.
An **application card** carries a **priority label** named exactly `P1`, `P2`, or `P3` (optionally with a 🎯 prefix), written by the job-board poller (personal-ai-pod `job-board-poll.js`): `P1` stays **at** the neutral band so a fresh top-fit role is caught the same day as email, while `P2`/`P3` sit **below** it.
Every other board carries neither label and orders purely by date.
The band each tier maps to - and how to change it - is defined in one place, the adapter's `_PRIORITY_BAND`.

A card's level band comes from its `desc`: `job-board-poll.js` writes a `Priority: P<n> · <category> · Director/VP-level` or `· IC-level` line into every Job Search Outreach card it scores.
Level-0 is the shared neutral level email/Slack and ordinary Trello cards also carry, so a card whose desc contains `Director/VP-level` (or carries no priority line yet) resolves to that same neutral level and interleaves with today's mail by date; only a card whose desc contains `IC-level` drops to level -1 and waits behind its priority band's neutral-level items - see the adapter's `_level_band`.

The referral band comes from a **`🤝 Referral` label** (a role at a company where someone in Russell's network will refer him): it breaks ties within a band+level, lifting a referral role ahead of a cold one of the **same level** - but a leadership role without a referral is still worked before an IC role even with one, because level leads referral.
See the adapter's `_referral_band`.
The priority, referral, and 👤 Contact labels, like ⛔/⏳ status labels, are held out of the contact parse so none is read as a person.

Build a stable id: `trello-<card-name-slug>-<last6 of cardId>-<startYYYYMMDDHHMM|nodue>` where the stamp is the card's Start to minute precision, or the fixed sentinel `nodue` when it has none.
That stamp is part of the id on purpose: a card recurs every cycle (a nudge or CLEAR bumps its Start out), and seen-state keeps a drained id forever, so without the stamp a card would be marked seen on its first drain and never resurface.
Carrying the time-of-day and not just the date lets a deliberate same-day reschedule (morning to afternoon) mint a fresh id and dispatch again that day, while a card at its default creation time keeps one id per calendar day.
Parse each card's labels with `label_vocab` into channel / features / contacts (⛔/⏳ status labels are held out) so the worker knows where the conversation lives, and resolve the card's `initiative` (the initiative-colored label's slug, else the board default).
