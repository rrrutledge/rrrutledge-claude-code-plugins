# trello provider — outreach boards (Trello API)

A provider for the **outreach** source. The card's **Start date IS the queue**: harvested on
every run like any other source, it returns the cards that are startable now and not wearing a ⛔
Blocked label. A card is startable once its Start has arrived, or immediately when it has no Start; a
future Start is the only thing that holds a card back - "not okay to begin until this day". Start is the
one date the queue reads (see STARTABLE-TASK MODEL): the "work-on-it / go-live" day, whether the card is
an outreach follow-up or a task. It rides the single schedule with no special cadence. All Trello reads and mutations
go through the **`trello`** skill's `trello_utils.py` - never the Trello REST API directly. The
credentials sit in the environment, so a raw `curl` to `api.trello.com` is tempting; it skips the
shared auth, timeout, and read-after-write verification, and a raw write is blocked by the
safe-compounds hook, which points back to `trello_utils`. Implements `../engine/provider.md`; classify
by `../engine/triage.md`. id prefix: `trello-`.

## Config
- **Boards** — the single source of truth is `<repo>/trello-boards.yaml` (a `boards:` list of
  `{name, id}`), the same registry the `trello-outreach` skill reads. The drainer drains **every** board
  in it, so adding a board is a one-file edit. (Legacy fallback: a `providers.trello.boards` list in
  `.claude/drainer.local.md` if no registry file exists.)
  - **Format the adapter parses:** the poller runs on bare stdlib Python (no PyYAML), so the adapter
    extracts boards by a fixed indent convention rather than full YAML — each board is `  - name:` at
    **two-space** indent with its `    id:` at **four-space** indent. Keep that shape. Any deeper
    per-board fields (`purpose`, `template_cards`, …) are free-form and ignored by the drainer.
- **Initiatives** — shared outreach programs many cards belong to. The **registry is the
  `initiatives/` folder** in the repo: one `initiatives/<slug>.md` per program (a file existing ⇒ the
  initiative exists — no central list to maintain). The file either holds the content inline or, via a
  `source:` frontmatter pointer, redirects to where the content lives (a Confluence/other URL). See
  `initiative-doc-template.md` (in this skill) for the two shapes — source-stub vs inline-content. A
  card is tagged with an initiative two ways (a per-card
  tag wins over the board default):
  - **Per-card** — a Trello **yellow label** (yellow is the initiative color). The adapter resolves it:
    label name → slug → `initiatives/<slug>.md`. The yellow label is held out of contact
    classification, so it's never mistaken for a contact name.
  - **Board default** — an `initiative: <slug>` field on a board entry in `trello-boards.yaml` (every
    card on that board inherits it — best when the whole board is one program). The four-space
    `initiative:` field is parsed by the adapter alongside `id`.
  The adapter writes the resolved slug as `initiative` on the item. See INITIATIVE-LOOKUP for loading
  the content and STAGE-PLAYBOOK for the generic per-stage activity.
- Drainer knobs in `.claude/drainer.local.md` → `providers.trello`:
  - `skip_lists` — terminal/parking lists to ignore (e.g. Abandoned, Finished, Adopted, Templates).
  - `skip_labels` — labels whose cards are suppressed (default `[Blocked]` → hides ⛔ Blocked cards).
    Matched as a case-insensitive substring, so `blocked` catches `⛔ Blocked`.
  - `status_labels` — dependency-state labels held out of contact classification (default
    `[Blocked, Waiting]`), so a ⛔/⏳ label is never read as a person's name.
  - `label_vocab` — `{channels: [...], features: [...]}`; any label not in those is a contact name.
Credentials: `TRELLO_API_KEY` / `TRELLO_TOKEN` in the environment (used by the `trello` skill).

## AUTH-GLANCE
Confirm `TRELLO_API_KEY`/`TRELLO_TOKEN` are set. If not, tell the user to set them and stop.

## STARTABLE-TASK MODEL
The default posture is **hungry to start**: everything is startable now unless it's genuinely blocked.
Start is the one date, alongside two labels + a description convention:
- **Start date = "work-on-it / ping-back"** — the day the card should resurface, and the only date the
  queue reads. A startable-now card carries **no** Start; a future Start is the only way to hold a card
  back until a chosen day; a ⏳ Waiting card's Start is when to re-nudge.
- **⏳ Waiting** (label) = blocked on a **person's** reply. Still surfaces on its ping-back Start so the
  worker can re-nudge. Record `Waiting-for: <name> · <channel>` in the description (Phase 2's reply
  matcher reads it).
- **⛔ Blocked** (label) = blocked on **another card** finishing. Suppressed entirely (`skip_labels`)
  until unblocked. Record `Blocked-by: <upstream-shortlink>[, …]` in the description; optionally attach
  the upstream card for a human-visible link. (The `trello` skill's SKILL.md owns applying this at
  creation time — it's the mechanics layer every Trello write goes through.)

**Trello's native Due field is a separate, informational date the queue never reads.** It's fine to set
Due as a plain deadline marker when a card genuinely has one, alongside Start — but setting only Due,
expecting it to hold a card back, nudge it, or make it resurface, silently does nothing: the drainer's
queue and every nudge/advance operation in this file act on Start alone. When rescheduling a card for any
reason (nudge, hold-back, advance, a Russell-requested date change), set **Start**, not Due.

**Unblock has a push and a pull.** When an upstream card is finished (moved to a terminal/skip list or
archived), call `trello_utils.cascade_unblock(board_id, finished_card_id, session)`: it scans the board
once, finds cards whose `Blocked-by:` names the finished card, and for each whose **last** blocker just
cleared, strips its ⛔ label and sets **Start = today** so it surfaces on the next drain. (Phase 2 will
add a second trigger — an inbound reply resolving a ⏳ card — to this same cascade.)

The push only fires when *a worker* finishes the upstream card through this flow, and only looks within
that one board — it's blind to a blocker finished any other way (Russell moving it by hand in Trello, a
session that forgot the call) or living on a *different* board than the card it blocks (e.g. a
resume-prep task on Personal Follow-Up blocking an application card on Job Search Outreach — a real case
that surfaced in practice). The poller adapter's `_enumerate` therefore also runs
`trello_utils.sweep_unblock(board_ids, session)` **once per cycle across every board in the registry**
(not per board) as a pull backstop: one combined scan (2 read calls per board, not one per blocker) that
frees any card whose *every* named blocker is already done, wherever it lives. It's a no-op when nothing
needs freeing, so a blocked card is guaranteed to resurface once its blockers finish — no one has to
remember to call cascade_unblock, and cross-board blocking just works.

## ENUMERATE
Via the `trello` skill, list cards across the configured boards that sit in an **active** list (not in
`skip_lists`), are **not** wearing a `skip_labels` label (⛔ Blocked), and are **startable** — Start
now-or-earlier, or no Start at all. A future Start is the only thing that holds a card back. Rank a card
by its **Start date** (its go-live), most recent first, and an undated card by its **creation date**
(decoded from the card's ObjectId).

Rank is `(priority band, level band, referral band, date)`, all descending — level breaks ties within
a band, referral breaks ties within a band+level, date breaks ties within a band+level+referral. The
order of those three bands is defined in exactly one place, `provider_base.band_rank`, which both this
adapter's enumerate and the poller's cross-source sort call; to reorder the queue (e.g. put referral back
ahead of level), change the tuple there. A Job Search Outreach card's band reflects its card type. A
**person follow-up card** carries the **`👤 Contact`** label and is pinned **one band above** neutral, so
a live contact thread is worked ahead of email/Slack and every application - following up with an existing
contact is the highest-value move. An **application card** carries a **priority label** named exactly
`P1`, `P2`, or `P3` (optionally with a 🎯 prefix), written by the job-board poller (personal-ai-pod
`job-board-poll.js`): `P1` stays **at** the neutral band so a fresh top-fit role is caught the same day as
email, while `P2`/`P3` sit **below** it. Every other board carries neither label and orders purely by
date. The band each tier maps to - and how to change it - is defined in one place, the adapter's
`_PRIORITY_BAND`.

A card's level band comes from its `desc`: `job-board-poll.js` writes a
`Priority: P<n> · <category> · Director/VP-level` or `· IC-level` line into every Job Search Outreach
card it scores. Level-0 is the shared neutral level email/Slack and ordinary Trello cards also carry, so
a card whose desc contains `Director/VP-level` (or carries no priority line yet) resolves to that same
neutral level and interleaves with today's mail by date; only a card whose desc contains `IC-level` drops
to level -1 and waits behind its priority band's neutral-level items — see the adapter's `_level_band`.

The referral band comes from a **`🤝 Referral` label** (a role at a company where someone in Russell's
network will refer him): it breaks ties within a band+level, lifting a referral role ahead of a cold one
of the **same level** — but a leadership role without a referral is still worked before an IC role even
with one, because level leads referral. See the adapter's `_referral_band`. The priority, referral, and
👤 Contact labels, like ⛔/⏳ status labels, are held out of the contact parse so none is read as a person.

Build a stable id:
`trello-<card-name-slug>-<last6 of cardId>-<startYYYYMMDD|nodue>` where the stamp is the card's Start
date, or the fixed sentinel `nodue` when it has none. That date is part of the id on purpose: a card
recurs every cycle (a nudge or CLEAR bumps its Start out), and seen-state keeps a drained id forever, so
without the stamp a card would be marked seen on its first drain and never resurface. Parse each card's labels
with `label_vocab` into channel / features / contacts (⛔/⏳ status labels are held out) so the worker
knows where the conversation lives, and resolve the card's `initiative` (the initiative-colored label's
slug, else the board default).

## CAPTURE (needs-you)
The card itself is the item, and **we own it** — unlike inbound mail/Teams, the same card recurs every
follow-up, so the card is a durable cache for everything needed to act.

**The card is already protected against a duplicate worker — start research directly, nothing needs
claiming first.** The poller records this card's id in seen-state the moment it dispatches this session,
and every later cycle drops the card as already-seen for as long as its go-live date holds. Only a CLEAR
mints a fresh id — the go-live date is part of the id (see ENUMERATE), and CLEAR is what bumps it out — so
the card resurfaces on its new date, never while this work is in flight.

**Leave the Start date untouched until CLEAR.** Bumping it to "claim" the card forges the very id CLEAR is
meant to mint later — one seen-state never recorded — so the next cycle reads a brand-new item and spawns
a second worker on top of this one. The Start date moves only when the work advances (CLEAR), never to
mark a card in-flight.

Read its description + structured comments FIRST; then run INITIATIVE-LOOKUP for program context; act on
that before re-discovering anything. Write `items/<id>.json`:
`{ "id","source":"trello","triage":"needs-you","kind":"reply|work","cardId","board","list","name",`
`"start","url","contacts":[...],"channelLabel":"<Email|Teams|Slack|...>","initiative":"<slug|null>","ts":"<ISO now>" }`
Then find the relevant **thread** (email / Teams / Slack) for the contact + channel and read it to
decide the move — a card's `url`/description links to one specific message, not the whole conversation,
so pull full context per **that source's own SITUATIONAL-CHECK guidance** before deciding the move
(the counterparty may have replied since capture, or the user's own follow-up may still be unanswered —
a clarifying question left hanging turns a "ready to act" card into one blocked on the other party;
don't miss that). For **email** threads, additionally search the whole mailbox in both directions
(incoming from the contact AND your sent replies) — recent messages may have been swept out of the
inbox by a prior drain cycle:
- **outlook-rest / outlook-graph**: search inbox + Archive + Deleted Items (paginated) — CLEAR
  moves handled messages to **Archive** (older items cleared before this behavior changed may still
  sit in Deleted Items).
- **gmail**: search All Mail with no `in:` filter — CLEAR archives (not trashes), so everything is
  in All Mail.

A card's last comment can also point to a different channel entirely than the one it's sitting in (a
"DM me your X," a "connect A with B" that's really an email intro) — see `../engine/worker-core.md` §2,
"An ask can hop channels," which every worker (Trello included) already follows.

**Cache back to the card.** Anything you learn that the next pass would otherwise re-derive — the
thread deep link, the contact's role/handle, where they are in the outreach, the last message
gist/date, the agreed next step — write into the card's description/comments via the `trello` skill, in
a stable structured shape. CLEAR (advance) then updates that cache. Over time a card should carry
enough that a follow-up needs almost no re-discovery. (A dedicated card schema for this is worth
designing — see the project's Trello-caching follow-up.)

## INITIATIVE-LOOKUP
Many outreach cards share one program — the user is following up with many people to onboard them to
the same initiative — so a card like "Acme Corp / Jane Doe" carries no per-card context of its own.
The initiative supplies the **content** (what the program is, why it matters, the ask); the generic
STAGE-PLAYBOOK below supplies the **activity** for the card's current column. Load once, before
drafting:
1. **Take the resolved slug.** The item's `initiative` field (set by the adapter from the
   initiative-colored label, else the board default). Empty → no initiative; proceed as a plain card
   (draft from the card/thread alone, ask the user if blank).
2. **Open `initiatives/<slug>.md`** from the merged-main config repo named in your seed prompt (not the
   working directory — the config repo is a worktree pinned to origin/main, so a merged initiative doc is
   read even when the working tree is on a feature branch) and read its content. If it carries a `source:`
   frontmatter pointer, the content lives there instead — fetch it by shape:
   - an **http(s) URL** → a Confluence page (`*.atlassian.net/wiki`) via Atlassian MCP or the
     `confluence-investigator` skill (extract the numeric page id from the URL when one is needed); any
     other URL via plain web fetch.
   - otherwise the file body itself is the content.
   You want: what the program is, why it matters to the contact, the ask, and any role the contact
   plays if the initiative defines roles. (If the file is missing, the slug is still a useful hint —
   note the gap and draft from the card/thread.)
3. **Draft from content + playbook.** Combine the initiative content with the STAGE-PLAYBOOK intent for
   the card's column to ground a meaningful message — no need to ask the user what the outreach is
   about. If the content genuinely lacks something the message needs, that's a gap to fix in the
   initiative doc, not a question to bounce to the user every cycle.

## STAGE-PLAYBOOK (how to advance a card to the next phase)
A card's column is a starting point, not something to describe back. For each phase the goal is to move
the contact to the **next** phase: situation-check first (did they reply? did the planned step happen?),
then take or draft the action that drives progression. Every outreach board is some version of the same
awareness→commitment→done funnel, so this advance logic lives here once and stays **generic**; the
initiative content (INITIATIVE-LOOKUP) supplies what any given step actually involves for that program.

Map the card's column onto the nearest phase by intent — boards name and sub-divide these differently:

- **Not yet aware → aware.** They don't know about it. Action: send the first-contact intro — what it
  is, why it matters to them, an invite to talk. (Sending it advances the card.)
- **Aware → interested.** They've heard of it. Action: follow up to land a yes — answer questions,
  surface the benefit, ask what they need to move forward.
- **Interested → committed.** They want in. Action: nail down the concrete next step, and if the work
  needs scheduling, get it on a calendar.
- **Committed / scheduled → in progress.** A plan or date exists. Action: confirm it still holds and
  that they've started; nudge to re-confirm if it has slipped.
- **In progress → done.** They're working on it. Action: check for blockers, offer help, push toward
  completion.
- **Done.** Terminal — close the loop and thank them. (Usually a `skip_lists` list.)
- **Abandoned.** Terminal — not pursuing; no action.

Some boards insert their own steps between these (e.g. a reconciliation or review stage); treat such a
column as the nearest generic phase and let the initiative doc say what that step requires.

Always situation-check before acting: if they've already replied or the step already happened, the move
is usually to **advance** the card (CLEAR) rather than send again; if it's simply not yet time to follow
up, silently bump the Start date (CLEAR) and surface no tab.

## CLEAR (advance the card)
Only **after** the user confirms they sent/handled the message, advance the card via the `trello` skill:
- **nudge** — bump the Start date out N days (they haven't replied; follow up later). Use the cadence below.
- **advance** — move to a later stage + set the next Start date (it progressed).
- **stop** — move to Abandoned + clear the Start date (not pursuing). If a message draft was staged for
  this card and never sent, discard it too (e.g. `node gmail.js --delete-draft=<draft-id>` for a Gmail
  draft) — an abandoned card means the draft is dead weight, not a reminder to revisit.
Each advance also posts a dated comment recording what happened, so the board reflects reality.

**The CLEAR op is the item's digest disposition.** When a worker auto-handles a card and self-closes it
(worker-core §6a), the CLEAR it performed is what the digest reports: **stop → `abandoned`**, **advance →
`advanced`**, **nudge → `nudged`**. Stamp that value as `disposition` on `items/<id>.json`, with the dated
comment's one-liner as `dispositionReason`, before the digest `queue-add` (worker-core §6a). The digest
then lists an abandoned or advanced card under its Auto-handled "state changed" group with that reason, and
folds a nudge into the "checked, no change" count, so a closed-req abandon reads as a real signal, not a
deferral.

A card reaches the digest as `nudged` only on the **silent-bump** path: the situational check found recent
activity since the Start date was last set - the contact replied, or Russell already answered on the
thread - so a follow-up right now would be premature, and the card's Start is bumped out with nothing sent.
A follow-up Russell actually sends is a needs-you item he already saw, never an auto-handled nudge. So a
`nudged` card in the digest is always "checked, activity in flight, too early to act," never an unanswered
card pushed without a follow-up going out.

A ⏳ Waiting card nudges the same way — bump its **Start** (ping-back date) out. Whenever a card is
**finished** (moved to a terminal/skip list), fire
`trello_utils.cascade_unblock(board_id, finished_card_id, session)` so any ⛔ Blocked cards waiting on
it are freed (⛔ stripped, Start set to today) on the spot.

### Recurring tasks — one card, bumped forward, never archived
A card whose description carries a **`Recurs: <cadence>`** line (e.g. `Recurs: weekly`, `Recurs:
monthly`, `Recurs: every Thursday`) is a standing task, not a one-off — `weekly-job-board-sweep` on the
Job Search Outreach board is a live example, resurfacing every week under a fresh Start date rather than
ever reaching a terminal list. This applies to any board, not just outreach: a personal to-do that
repeats ("call the bank every Thursday," "back up photos monthly") gets exactly the same treatment.

On CLEAR, once the work for this occurrence is done, **do not** move a `Recurs:` card to Abandoned or
Finished. Instead **bump its Start date forward by the stated cadence** (same mechanism as the ordinary
**nudge** op above, just driven by the card's own marker instead of a reply-cadence tier) and post the
usual dated comment. The card goes quiet until that new Start arrives, then resurfaces as a fresh
drainable item — the id scheme already stamps the Start date into the card's id for exactly this
reason (see ENUMERATE), so this occurrence and the next one are never confused in seen-state.

A `Recurs:` card is never `stop`ped for being "done" — completing one occurrence isn't the end of the
task, only this cycle of it. `stop` still applies if Russell explicitly says to abandon the recurring
task altogether (clear the `Recurs:` line too, so it can't come back by mistake).

### Nudge cadence

**A stated timeframe beats the fixed tiers below.** When the counterparty already gave a concrete
expected response time, follow up relative to that time instead of picking a tier:
- **Personal contact named their own timeline** ("I'll get back to you by Friday") — bump to a little
  *after* that time, giving them some grace past their own estimate.
- **Business or process gave an upper-bound estimate** ("may take up to 10 business days") — bump to a
  little *before* that bound, so the check-in lands inside their stated window instead of waiting for
  it to expire.

Otherwise, when no such timeframe was given, pick the tier based on how closely the user works with the contact:

**Frequent collaborator** — someone the user works with regularly who would treat this as a normal part of their day:
- After sending → bump **3 business days**
- After 1st follow-up (no reply) → bump **1 week**

**Infrequent contact** — someone outside the user's regular workflow, or where this ask isn't part of their day job:
- After sending → bump **1 week + 1 day**
- After 1st follow-up (no reply) → bump **2 weeks**

When unsure, default to infrequent. When the ask requires real commitment or internal approval from the contact (e.g. sponsorship money, a formal agreement), start at **2 weeks** instead of 1 — regardless of how closely the user works with them.

If the situational check finds **nothing to do right now**, silently bump the Start date and finish —
surface no tab. "Not yet time to follow up" is decided by the **nudge cadence above**: it's
nothing-to-do only while that interval hasn't elapsed since the last outbound message (or they replied
and the user already answered). Once the card's Start has arrived, they still haven't replied, **and** the
cadence interval has elapsed, it *is* time to follow up — **draft the nudge** (needs-you), don't bump the
date again. (A started card whose cadence has run out is not "nothing to do" — that misread is what turns
a card into one that gets bumped forever without a follow-up ever going out.)

## JUNK-LEARNING
N/A — outreach cards are curated, not inbound noise.

## DRAFT-MODE
`message-draft` skill, in the mode matching the card's channel (`outlook` / `teams` / `slack`).
