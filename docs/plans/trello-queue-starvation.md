# Plan: startable work that never reaches Russell

Status: for review.
Nothing in code, cards, or calendars changes until Russell approves a direction.

## The short answer

Yes, startable work is getting stuck, and it is not hidden by seen-state.
Of 1,536 startable Trello cards on 2026-10-15, only 7 carry an id the drainer has already marked seen.
The rest are simply never reached: the drainer dispatches strictly newest-first, and it opens new workers only as fast as Russell closes old ones.
Anything that is not near the top of that order on the day it becomes startable sinks under fresher items and stays there.

The Portland card is a second, sharper case of the same thing, not a wrong archive.

## Finding 1: the Portland card was archived on purpose, then starved on the calendar

What happened, in order (all times Central):

- **Oct 12, 5:00 AM** - a Slack-reply worker (session `8f639a2c`) created the card [mdRmJiqL](https://trello.com/c/mdRmJiqL) after Russell asked for the chore to be "a physical task."
  It did not know the term, so it also put a 9:00 AM block on the main calendar.
- **Oct 12, 5:07 AM** - Russell explained that "physical task" means the drainer's Physical Tasks calendar and asked for a handoff to teach that.
- **Oct 12, 5:11 AM** - the worker handed off to session `5416fb49` with a brief that said, in step 2, to put the chore on the Physical Tasks calendar, delete the main-calendar block, and archive the Trello card as redundant.
- **Oct 12, 5:12 AM** - `5416fb49` did all three, then shipped the new `physical-task` skill (personal-ai-pod#496).

So the archive was correct: the work moved from Trello to the Physical Tasks calendar.
The failure came after the move.
The chore has sat on Physical Tasks as due since Oct 12 and has never been dispatched.
It is not alone: 21 physical tasks are due right now, some since Sep 26, and the drainer has not dispatched a single physical task since **Oct 8, 2:56 PM**.

**Why physical tasks never dispatch.**
The poller orders every needs-you item by priority band, then by its `received` date, newest first.
Every other source stamps `received`; the physical-task source does not.
An item with no `received` sorts *below every dated item in its band*, so a physical task only gets a worker once every email, Slack message, and neutral-band Trello card ahead of it has one.
With one worker slot opening at a time, that never happens.
The last burst of physical-task dispatches, 13 of them on Oct 1 and 2, most likely came on days the rest of the queue ran dry; I found no log that records the queue depth to confirm it.

**Current state to resolve by hand.**
Russell restored the card with Start = today, so the chore now exists in two places: the Trello card and the Physical Tasks event.
One of them should go; I have left both alone.

## Finding 2: the queue is a newest-first stack fed faster than it drains

**Throughput is Russell's review rate.**
The drainer opens a new needs-you worker only while fewer than `target_reviewable` sessions (10 on this machine) are waiting for him.
On Oct 15 there were 13 live sessions, 9 of them waiting, so about one slot opens each time he closes a session.
Trello dispatches were 25 to 50 a day through Oct 12, then 1, 4, and 14 on Oct 13, 14, and 15.

**Fresh items always win.**
In the last 30 days, 575 of 646 Trello dispatches (89%) were cards whose Start was 0 to 1 days old.
Recurring cards keep themselves fresh: a worker's CLEAR bumps the Start forward, so the card comes back next time ranked as brand new.
New mail, Slack, and newly-dated cards join them at the top.
A card whose Start is already a week old when it would be next in line never gets there.

**What is waiting, by board** (startable, in an active list, not Blocked, assigned to Russell or no one):

| Board | Startable | Never dispatched | Start older than 14 days |
|---|---|---|---|
| Personal Follow-Up | 19 | 7 | 1 |
| Executive Director Outreach | 27 | 15 | 0 |
| Finos Working Group Outreach | 1 | 0 | 0 |
| Job Search Outreach | 1,489 | about 1,400 | about 1,330 |

Personal and Executive Director work is not badly starved yet: the oldest three non-job cards (FAFSA for Rebekah and Reanna, the ISC AI assistant design, MIL Medicaid prep) sit at queue positions 50 to 52 with Starts from Oct 1 to Oct 8.
That is the early stage of the same pattern, and it worsens every slow review day.

**Finos is not stuck.**
9 of the 10 cards the audit flagged (Bank of Ireland, Deutsche Bank, Red Hat, GitLab, AXA, and others) are assigned to Pooi Ling Cheong.
The drainer skips cards assigned to someone else by design, so those are hers to move.

**Job Search is mostly dead weight.**
1,477 of the 1,489 cards sit in Identified, and 1,169 of those have a Start or creation date more than 30 days old and were never worked.
The "223 P1 cards" from the audit are almost all IC-level P1s (224 cards, 109 of them dated Aug 24), which rank below every leadership-level item by design.
Only 46 of the 1,169 carry a 🤝 Referral label.

## Proposed fixes

Ordered by value for effort.
Each is a separate PR so Russell can take any subset.

### Fix A: give physical tasks a real date (bug fix, small)

The physical-task source stamps `received` with the task's queued date and slot time, the same way Trello stamps a card's Start.
Physical tasks then interleave with the rest of the neutral band by date instead of always sorting last.
The one-task-at-a-time hold and the free-gap check stay as they are.
A test pins that a physical task with an older date still sorts above an undated item and below a newer one.

### Fix B: an oldest-first lane in dispatch (the starvation fix)

Every third needs-you dispatch takes the **oldest** eligible item instead of the newest.
The lane draws only from items at or above the neutral band and neutral level, so it serves Personal, Executive Director, physical tasks, mail, Slack, and leadership-level P1 roles, but never pulls an IC-level or P2/P3 job card ahead of real work.
A small counter in the runtime directory remembers where in the 2-newest-then-1-oldest rotation the poller is, because slots open one at a time across cycles.

Alternatives considered, and why this one:

- **Aging** (an item's rank rises with time waiting) is harder to reason about and reorders the whole queue every cycle; the rotation is predictable and leaves today's newest-first behavior intact for two of every three slots.
- **Flipping Trello to oldest-first** would bury today's fresh replies behind months-old cards, the opposite failure.
- **A periodic sweep that re-dates old cards to today** works, but silently rewrites Starts Russell set on purpose, and a re-dated card then jumps ahead of genuinely new work.

Open choice for Russell: the ratio.
Recommendation is one oldest per three dispatches; one per two drains the backlog faster at the cost of fresher items waiting a little longer.

### Fix C: a "waiting too long" section in the digest (visibility)

The EOD digest lists every startable item, in the bands Fix B serves, whose date is more than 7 days old and that has no live worker, grouped by board, with links.
This turns starvation from something Russell discovers by accident into a number he sees every evening, and it catches any future cause, not just this one.
The logic is the audit script from this investigation, moved into the digest as plain code.

### Fix D: retire stale Identified job cards (cleanup, needs Russell's call)

A job posting that nobody has acted on in 30 days is almost always closed or filled.
Proposal: a sweep moves an Identified application card to **Abandoned**, with a comment saying why, once its Start (or creation date, if undated) is more than 30 days old and it has never been worked.
It runs once now (about 1,123 cards) and then daily as a rule.
Cards wearing 🤝 Referral or 👤 Contact are always left alone.
Moving a card to Abandoned is reversible: it is one drag back to Identified, and its history stays on the card.

The sweep belongs beside `job-board-poll.js` in personal-ai-pod, since that poller already owns these cards' lifecycle.

Open choices for Russell:

- the age threshold (30 days recommended),
- whether IC-level P1s get the same rule or a longer one,
- whether to run the one-time bulk move at all, or only apply the rule going forward.

### Fix E: retire cards by moving them, never by archiving (guard)

The Portland archive was right this time, but an archived card is invisible everywhere: the drainer, the board, and the audit all skip it, so a wrong archive is silent.
A list move is just as clean and stays visible.

- The `trello` skill gains a rule: to retire a card, move it to Finished or Abandoned with a comment naming why and, if the work moved elsewhere, where it went (for example "moved to the Physical Tasks calendar as <subject>").
  Archiving is only for cards Russell explicitly asks to archive.
- `trello_utils` gains a `retire_card` helper that does the move plus comment, so the right way is also the easy way.
- A hook-level block on archiving is possible but not recommended: the rule plus helper covers the realistic path, and a hard block would also stop Russell's own explicit archive requests.

### Fix F: doc drift (tiny)

`poller-core.md` says the reconcile step covers email only, but Trello and physical tasks implement it too.
The doc catches up to the code in the PR for Fix A.

## Suggested order

1. Fix A, since it is a clear bug with an outsized effect (21 physical tasks waiting).
2. Fix C, so the effect of everything after it is visible.
3. Fix B.
4. Fix D, once Russell picks the threshold.
5. Fix E and F alongside whichever PR is nearest.

## Evidence

All counts are from read-only runs on 2026-10-15 against live Trello, the drainer's seen-state and captured items in `personal-ai-pod/.tmp/drainer`, the Physical Tasks calendar, and `claude agents --json`.
The session transcripts behind Finding 1 are `8f639a2c-2e42-49cc-8002-b9f3f7c4f78d` and `5416fb49-2b3f-40a2-af73-095df5726116` under `~/.claude/projects/`.
