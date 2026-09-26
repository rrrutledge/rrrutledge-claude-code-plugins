# trello provider - outreach boards (Trello API)

A provider for the **outreach** source.
The card's **Start date IS the queue**: harvested on every run like any other source, it returns the cards that are startable now and not wearing a ⛔ Blocked label.
A card is startable once its Start has arrived, or immediately when it has no Start; a future Start is the only thing that holds a card back - "not okay to begin until this day".
Start is the one date the queue reads (see STARTABLE-TASK MODEL): the "work-on-it / go-live" day, whether the card is an outreach follow-up or a task.
It rides the single schedule with no special cadence.
All Trello reads and mutations go through the **`trello`** skill's `trello_utils.py` - never the Trello REST API directly.
The credentials sit in the environment, so a raw `curl` to `api.trello.com` is tempting; it skips the shared auth, timeout, and read-after-write verification, and a raw write is blocked by the safe-compounds hook, which points back to `trello_utils`.
Implements `../engine/provider.md`; classify by `../engine/triage.md`.
id prefix: `trello-`.

How the adapter reads config (boards registry, initiative tagging, drainer knobs) and how it selects and ranks startable cards lives in the companion **`trello-queue-model.md`** - poller/adapter-facing detail a worker acting on one card never needs, since the card arrives already selected, parsed (`contacts`, `channelLabel`, `initiative`), and ranked in its item JSON.
The rest of this file is worker-facing: how to act on one card and CLEAR it.

## AUTH-GLANCE
Confirm `TRELLO_API_KEY`/`TRELLO_TOKEN` are set.
If not, tell the user to set them and stop.

## STARTABLE-TASK MODEL
The default posture is **hungry to start**: everything is startable now unless it's genuinely blocked.
Start is the one date, alongside two labels + a description convention:
- **Start date = "work-on-it / ping-back"** - the day the card should resurface, and the only date the queue reads.
  A startable-now card carries **no** Start; a future Start is the only way to hold a card back until a chosen day; a ⏳ Waiting card's Start is when to re-nudge.
- **⏳ Waiting** (label) = blocked on a **person's** reply.
  Still surfaces on its ping-back Start so the worker can re-nudge.
  Record `Waiting-for: <name> · <channel>` in the description (Phase 2's reply matcher reads it).
- **⛔ Blocked** (label) = blocked on **another card** finishing.
  Suppressed entirely (`skip_labels`) until unblocked.
  Record `Blocked-by: <upstream-shortlink>[, …]` in the description; optionally attach the upstream card for a human-visible link.
  (The `trello` skill's SKILL.md owns applying this at creation time - it's the mechanics layer every Trello write goes through.)

**Trello's native Due field is a separate, informational date the queue never reads.**
It's fine to set Due as a plain deadline marker when a card genuinely has one, alongside Start - but setting only Due, expecting it to hold a card back, nudge it, or make it resurface, silently does nothing: the drainer's queue and every nudge/advance operation in this file act on Start alone.
When rescheduling a card for any reason (nudge, hold-back, advance, a Russell-requested date change), set **Start**, not Due.

**Unblock has a push and a pull.**
When an upstream card is finished (moved to a terminal/skip list or archived), call `trello_utils.cascade_unblock(board_id, finished_card_id, session)`: it scans the board once, finds cards whose `Blocked-by:` names the finished card, and for each whose **last** blocker just cleared, strips its ⛔ label and sets **Start = today** so it surfaces on the next drain.
(Phase 2 will add a second trigger - an inbound reply resolving a ⏳ card - to this same cascade.)

The push only fires when *a worker* finishes the upstream card through this flow, and only looks within that one board - it's blind to a blocker finished any other way (Russell moving it by hand in Trello, a session that forgot the call) or living on a *different* board than the card it blocks (a task on one board blocking a card on another).
The poller adapter's `_enumerate` therefore also runs `trello_utils.sweep_unblock(board_ids, session)` **once per cycle across every board in the registry** (not per board) as a pull backstop: one combined scan (2 read calls per board, not one per blocker) that frees any card whose *every* named blocker is already done, wherever it lives.
It's a no-op when nothing needs freeing, so a blocked card is guaranteed to resurface once its blockers finish - no one has to remember to call cascade_unblock, and cross-board blocking just works.

## CAPTURE (needs-you)
The card itself is the item, and **we own it** - unlike inbound mail/Teams, the same card recurs every follow-up, so the card is a durable cache for everything needed to act.

**The card is already protected against a duplicate worker - start research directly, nothing needs claiming first.**
The poller records this card's id in seen-state the moment it dispatches this session, and every later cycle drops the card as already-seen for as long as its go-live date holds.
Only a CLEAR mints a fresh id - the go-live date is part of the id (see the id scheme in `trello-queue-model.md`), and CLEAR is what bumps it out - so the card resurfaces on its new date, never while this work is in flight.

**Leave the Start date untouched until CLEAR.**
Bumping it to "claim" the card forges the very id CLEAR is meant to mint later - one seen-state never recorded - so the next cycle reads a brand-new item and spawns a second worker on top of this one.
The Start date moves only when the work advances (CLEAR), never to mark a card in-flight.

Read its description + structured comments FIRST; then run INITIATIVE-LOOKUP for program context; act on that before re-discovering anything.
Write `items/<id>.json`:
`{ "id","source":"trello","triage":"needs-you","kind":"reply|work","cardId","board","list","name",`
`"start","url","contacts":[...],"channelLabel":"<Email|Teams|Slack|...>","initiative":"<slug|null>","ts":"<ISO now>" }`
Then find the relevant **thread** (email / Teams / Slack) for the contact + channel and read it to decide the move - a card's `url`/description links to one specific message, not the whole conversation, so pull full context per **that source's own SITUATIONAL-CHECK guidance** before deciding the move (the counterparty may have replied since capture, or the user's own follow-up may still be unanswered - a clarifying question left hanging turns a "ready to act" card into one blocked on the other party; don't miss that).
For **email** threads, additionally search the whole mailbox in both directions (incoming from the contact AND your sent replies) - recent messages may have been swept out of the inbox by a prior drain cycle:
- **outlook-rest / outlook-graph**: search inbox + Archive + Deleted Items (paginated) - CLEAR moves handled messages to **Archive**, and some older cleared items sit in Deleted Items, so search there too.
- **gmail**: search All Mail with no `in:` filter - CLEAR archives (not trashes), so everything is in All Mail.

A card's last comment can also point to a different channel entirely than the one it's sitting in (a "DM me your X," a "connect A with B" that's really an email intro) - see `../engine/worker-core.md` §2, "An ask can hop channels," which every worker (Trello included) already follows.

**Cache back to the card.**
Anything you learn that the next pass would otherwise re-derive - the thread deep link, the contact's role/handle, where they are in the outreach, the last message gist/date, the agreed next step - write into the card's description/comments via the `trello` skill, in a stable structured shape.
CLEAR (advance) then updates that cache.
Over time a card should carry enough that a follow-up needs almost no re-discovery.
(A dedicated card schema for this is worth designing - see the project's Trello-caching follow-up.)

## INITIATIVE-LOOKUP
Many outreach cards share one program - the user is following up with many people to onboard them to the same initiative - so a card like "Acme Corp / Jane Doe" carries no per-card context of its own.
The initiative supplies the **content** (what the program is, why it matters, the ask); the generic STAGE-PLAYBOOK below supplies the **activity** for the card's current column.
Load once, before drafting:
1. **Take the resolved slug.**
   The item's `initiative` field (set by the adapter from the initiative-colored label, else the board default).
   Empty → no initiative; proceed as a plain card (draft from the card/thread alone, ask the user if blank).
2. **Open `initiatives/<slug>.md`** from the merged-main config repo named in your seed prompt (not the working directory - the config repo is a worktree pinned to origin/main, so a merged initiative doc is read even when the working tree is on a feature branch) and read its content.
   If it carries a `source:` frontmatter pointer, the content lives there instead - fetch it by shape:
   - an **http(s) URL** → a Confluence page (`*.atlassian.net/wiki`) via Atlassian MCP or the `confluence-investigator` skill (extract the numeric page id from the URL when one is needed); any other URL via plain web fetch.
   - otherwise the file body itself is the content.
     You want: what the program is, why it matters to the contact, the ask, and any role the contact plays if the initiative defines roles.
     (If the file is missing, the slug is still a useful hint - note the gap and draft from the card/thread.)
3. **Draft from content + playbook.**
   Combine the initiative content with the STAGE-PLAYBOOK intent for the card's column to ground a meaningful message - no need to ask the user what the outreach is about.
   If the content genuinely lacks something the message needs, that's a gap to fix in the initiative doc, not a question to bounce to the user every cycle.

## STAGE-PLAYBOOK (how to advance a card to the next phase)
A card's column is a starting point, not something to describe back.
For each phase the goal is to move the contact to the **next** phase: situation-check first (did they reply? did the planned step happen?), then take or draft the action that drives progression.
Every outreach board is some version of the same awareness→commitment→done funnel, so this advance logic lives here once and stays **generic**; the initiative content (INITIATIVE-LOOKUP) supplies what any given step actually involves for that program.

Map the card's column onto the nearest phase by intent - boards name and sub-divide these differently:

- **Not yet aware → aware.**
  They don't know about it.
  Action: send the first-contact intro - what it is, why it matters to them, an invite to talk.
  (Sending it advances the card.)
- **Aware → interested.**
  They've heard of it.
  Action: follow up to land a yes - answer questions, surface the benefit, ask what they need to move forward.
- **Interested → committed.**
  They want in.
  Action: nail down the concrete next step, and if the work needs scheduling, get it on a calendar.
- **Committed / scheduled → in progress.**
  A plan or date exists.
  Action: confirm it still holds and that they've started; nudge to re-confirm if it has slipped.
- **In progress → done.**
  They're working on it.
  Action: check for blockers, offer help, push toward completion.
- **Done.**
  Terminal - close the loop and thank them.
  (Usually a `skip_lists` list.)
- **Abandoned.**
  Terminal - not pursuing; no action.

Some boards insert their own steps between these (e.g. a reconciliation or review stage); treat such a column as the nearest generic phase and let the initiative doc say what that step requires.

Always situation-check before acting: if they've already replied or the step already happened, the move is usually to **advance** the card (CLEAR) rather than send again; if it's simply not yet time to follow up, silently bump the Start date (CLEAR) and surface nothing to Russell.
That silent bump belongs to outreach alone - waiting on the other person's reply - and never reaches a task card whose next step is Russell's (see DUE-TASK below).

## DUE-TASK (a card whose next step is Russell's gets worked, never bumped)
A card that surfaces has come due, and a due card exists to get worked on.
Finding it unfinished is expected - that is why the card exists - so "not done yet" is never a reason to wait.
The only way the task gets done is you and Russell doing it, and Russell acts on what you put in front of him.

Classify the card by **who holds the next step**:
- **An outside party holds it** - an outreach contact who hasn't replied (inside the nudge cadence), a ⏳ Waiting card, or a business still inside its own stated turnaround.
  This is the one case a silent bump fits.
- **Russell or you hold it** - everything else: a PR to review or merge, an institution login or MFA only he can do, a decision, a purchase, a call to make, or the task itself.
  This is a **needs-you** item: do every part you can do alone first (worker-core step 3), then present what Russell needs to do now, in order ("review and merge PR #417, then let's do the bank logins together").
  Never bump its Start, and never self-close it through worker-core §6a.

A card's own prose never overrides an arrived Start.
Description text like "deferred until after the job" was a hold-back, and a hold-back lives in the Start date alone (STARTABLE-TASK MODEL); a Start that has arrived means the hold is over.
When the text names a condition that still seems unmet, surface the card and let Russell choose: do it now, or give a new Start date.
Either way, the decision is his, so the date stays put until he makes it.

A **`Recurs:`** card follows the same rule (see Recurring tasks below for when it bumps).
When the recurring task has fallen behind - its own records show the last completed occurrence is well past one cadence ago - say so in the result, with how far behind it is, so the backlog is visible instead of rolling forward one occurrence at a time.

## CLEAR (advance the card)
Only **after** the user confirms they sent/handled the message, advance the card via the `trello` skill:
- **nudge** - bump the Start date out N days (they haven't replied; follow up later).
  Use the cadence below.
- **advance** - move to a later stage + set the next Start date (it progressed).
- **stop** - move to Abandoned + clear the Start date (not pursuing).
  If a message draft was staged for this card and never sent, discard it too (e.g. `node gmail.js --delete-draft=<draft-id>` for a Gmail draft) - an abandoned card means the draft is dead weight, not a reminder to revisit.
  Each advance also posts a dated comment recording what happened, so the board reflects reality.

**The CLEAR op is the item's digest disposition.**
When a worker auto-handles a card and self-closes it (worker-core §6a), the CLEAR it performed is what the digest reports: **stop → `abandoned`**, **advance → `advanced`**, **nudge → `nudged`**.
Stamp that value as `disposition` on `items/<id>.json`, with the dated comment's one-liner as `dispositionReason`, before the digest `queue-add` (worker-core §6a).
The digest then lists an abandoned or advanced card under its Auto-handled "state changed" group with that reason, and folds a nudge into the "checked, no change" count, so a closed-req abandon reads as a real signal, not a deferral.

A card reaches the digest as `nudged` only on the **silent-bump** path: the situational check found recent activity since the Start date was last set - the contact replied, or Russell already answered on the thread - so a follow-up right now would be premature, and the card's Start is bumped out with nothing sent.
A follow-up Russell actually sends is a needs-you item he already saw, never an auto-handled nudge.
So a `nudged` card in the digest is always "checked, activity in flight, too early to act," never an unanswered card pushed without a follow-up going out.
A task card waiting on Russell himself is never `nudged`; it is a needs-you tab per DUE-TASK.

A ⏳ Waiting card nudges the same way - bump its **Start** (ping-back date) out.
Whenever a card is **finished** (moved to a terminal/skip list), fire `trello_utils.cascade_unblock(board_id, finished_card_id, session)` so any ⛔ Blocked cards waiting on it are freed on the spot, as the STARTABLE-TASK MODEL's unblock section describes.

### Recurring tasks - one card, bumped forward, never archived
A card whose description carries a **`Recurs: <cadence>`** line (e.g. `Recurs: weekly`, `Recurs: monthly`, `Recurs: every Thursday`) is a standing task, not a one-off - it resurfaces under a fresh Start date each cadence rather than ever reaching a terminal list.
This applies to any board, not just outreach: a personal to-do that repeats ("call the bank every Thursday," "back up photos monthly") gets exactly the same treatment.

On CLEAR, once the work for this occurrence is done - or launched and running on its own, as a spawned session that IS the occurrence's work - **do not** move a `Recurs:` card to Abandoned or Finished.
Instead **bump its Start date forward by the stated cadence** (same mechanism as the ordinary **nudge** op above, just driven by the card's own marker instead of a reply-cadence tier) and post the usual dated comment.
The card goes quiet until that new Start arrives, then resurfaces as a fresh drainable item - the id scheme already stamps the Start into the card's id for exactly this reason (see `trello-queue-model.md`), so this occurrence and the next one are never confused in seen-state.

A `Recurs:` card is never `stop`ped for being "done" - completing one occurrence isn't the end of the task, only this cycle of it.
`stop` still applies if Russell explicitly says to abandon the recurring task altogether (clear the `Recurs:` line too, so it can't come back by mistake).

### Nudge cadence

**A stated timeframe beats the fixed tiers below.**
When the counterparty already gave a concrete expected response time, follow up relative to that time instead of picking a tier:
- **Personal contact named their own timeline** ("I'll get back to you by Friday") - bump to a little *after* that time, giving them some grace past their own estimate.
- **Business or process gave an upper-bound estimate** ("may take up to 10 business days") - bump to a little *before* that bound, so the check-in lands inside their stated window instead of waiting for it to expire.

Otherwise, when no such timeframe was given, pick the tier based on how closely the user works with the contact:

**Frequent collaborator** - someone the user works with regularly who would treat this as a normal part of their day:
- After sending → bump **3 business days**
- After 1st follow-up (no reply) → bump **1 week**

**Infrequent contact** - someone outside the user's regular workflow, or where this ask isn't part of their day job:
- After sending → bump **1 week + 1 day**
- After 1st follow-up (no reply) → bump **2 weeks**

When unsure, default to infrequent.
When the ask requires real commitment or internal approval from the contact (e.g. sponsorship money, a formal agreement), start at **2 weeks** instead of 1 - regardless of how closely the user works with them.

If the situational check on an outreach card finds **nothing to do right now**, silently bump the Start date and finish - surface nothing to Russell.
"Nothing to do" means only that an outside party holds the next step; a step waiting on Russell is something to do, per DUE-TASK.
"Not yet time to follow up" is decided by the **nudge cadence above**: it's nothing-to-do only while that interval hasn't elapsed since the last outbound message (or they replied and the user already answered).
Once the card's Start has arrived, they still haven't replied, **and** the cadence interval has elapsed, it *is* time to follow up - **draft the nudge** (needs-you), don't bump the date again.
(A started card whose cadence has run out is not "nothing to do" - that misread is what turns a card into one that gets bumped forever without a follow-up ever going out.)

## JUNK-LEARNING
N/A - outreach cards are curated, not inbound noise.

## DRAFT-MODE
`message-draft` skill, in the mode matching the card's channel (`outlook` / `teams` / `slack`).
