# Drainer queue prioritization - design

Card: https://trello.com/c/YnIyEj0V
Source: Russell's 2026-11-09 self-email "Re: Getting behind".

This changes the order of the queue and nothing about how the queue is drained.
Dispatch stays as it is: while sessions waiting on Russell are under `target_reviewable` and total sessions are under `max_concurrent`, the next item in queue order is dispatched.

## What the queue does today

Every drained item, from every source, sorts by one key: `(priority band, level band, referral band)` and then its date, all descending.
The tuple lives in `provider_base.band_rank`, and the date is `Start` for cards and received time for mail and Slack.
Recent orphan sessions jump the line before any of that.

Two behaviors matter for the redesign.

- Within a band the newest date wins, so a card whose Start passed weeks ago sinks behind today's mail.
  The oldest overdue work is the first to be dropped in a crunch.
- Only job-search cards carry a non-neutral band.
  The band rises for a `👤 Contact` card, a card on an in-progress list, a P1 Director-or-above role, and a P1 manager at or above $250K.
  A plain P1 role stays neutral and interleaves with email.
  Everything else (bills, invoices, sponsorship, every other board) is neutral and orders by date alone.

Trello's native Due field is never read.

## Proposed model: four lanes, then date

An item takes the highest lane it qualifies for.
Inside a lane the existing tiebreakers apply, with one change to the date direction described below.

| Lane | What is in it | Within the lane |
|---|---|---|
| 1. Money | Job search (`👤 Contact` cards, cards on an in-progress list, every P1 role) and any card labelled `💰 Money` (bills, the ISC invoice, the monthly budget, sponsorship follow-up) | Existing level then referral order, then oldest Start first |
| 2. Due | Any card with a Due date that is not marked complete and whose Start has arrived | Soonest Due first |
| 3. Neutral | Email, Slack, and every ordinary card | Oldest Start first |
| 4. Below the line | P2/P3 roles, and `⏸ Big` cards while crunch mode is on | Existing P2 then P3 order |

Recent orphan sessions still come ahead of lane 1, as they do today.

### Decisions this makes

**Job search and money are one lane.**
Both are about money coming in or going out, so a P1 role, a contact follow-up, a bill, and an invoice all rank together.
A plain P1 role moves up out of the inbox, since P1 roles now sit in this lane too.
Level and referral still order job-search cards against each other.

**Due-dated work is read from Trello's Due field.**
A Start date keeps meaning "unblocked, do it when you can".
A card with a Due date and a Start that has arrived has been started on purpose and has a deadline, so it enters lane 2 and sorts by how soon it is due.
There is no time window: once Start has arrived, the Due date counts.
A bill carries both the money label and a Due date and lands in lane 1.

**Overdue work stops sinking.**
Today the newest date sorts first, so stale cards are worked last.
Inside lanes 1 and 3 the order flips to oldest Start first, so work that has been waiting longest comes up before work that just arrived.
This is the change that stops items dropping silently.

**Money is a label, not a guess.**
The pod's own money skills (budget, ISC invoice, sponsorship follow-up, bill cards) apply `💰 Money` when they create or recur their card, and the adapter reads the label like it reads `🤝 Referral`.
Money-related email is not re-ranked in the first version.

**Big pieces of work wait out a crunch.**
A `⏸ Big` label marks a card as a large piece of work, the kind you would rather not start while behind.
While crunch mode is on, those cards drop to lane 4.
Crunch mode is a single file in the drainer config repo, `drainer-local/crunch.yaml`, holding an end date (`until: 2026-11-14`).
It switches itself off when the date passes, so a forgotten flag cannot defer work forever.
Turning it on is editing that one line, and the poller reads it every cycle.

## Recurring cards that get missed

There is one card per recurring task, and its Start moves forward only when an occurrence is finished.
A missed occurrence therefore leaves the card with an old Start, and no "next occurrence" exists to arrive on its own.
A weekly task missed for two weeks shows up as one old card instead of this week's turn.

A plain-code sweep, run each poller cycle next to the unblock sweep, brings every `Recurs:` card forward once its next occurrence is reached.
No model tokens are involved, and nothing is surfaced in the digest.
The new occurrence simply comes up in the queue.

- The card's Start is the date this occurrence became due, and the `Recurs:` line gives the period.
- When today is at or past Start plus one period and the card was not completed, the occurrence is missed.
  A completed card would already have had its Start moved forward, so an old Start means the work was not done.
- The sweep moves Start to the most recent date on the card's original schedule that is not in the future.
  The date comes from the card's Start and period, so a Thursday card stays on Thursdays and a missed week does not shift the schedule.
- The new occurrence is startable right away.
- A task missed several times in a row ends up with one current occurrence, not several.
- The sweep leaves one dated line on the card ("occurrence of <date> missed, moved to <date>") so the card's history is honest, and tells no one.

A card whose work should stay owed when missed (the monthly budget is the likely one) can opt out with one added word on its `Recurs:` line, `Recurs: monthly, keep`.
A kept card is not moved by the sweep.

## Where the change lands

All in the drainer plugin, in four places.

1. `provider_base.band_rank` becomes lane-aware (a single `lane` value first), and stays the one definition both sort sites read.
2. The Trello adapter reads `due` and `dueComplete`, the `💰 Money` and `⏸ Big` labels, and the crunch file, and stamps the lane on each item.
3. The poller's cross-source sort reads the same lane value, which keeps mail and Slack in lane 3, and the poller runs the recurring-card sweep.
4. `trello-queue-model.md` and `trello-provider.md` change to match: the "Due is informational" paragraph is rewritten, since Due becomes a ranking input, and the `Recurs:` section gains the sweep and the `keep` word.

In this repo the pod's money skills and recurring cards gain the `💰 Money` label.
The existing band-ranking tests extend to cover lane order, the date-direction flip, and the sweep's date math.

## Rollout

Each step is its own PR and can ship alone.

1. Date direction flip plus lifting every P1 (smallest change, immediate relief).
2. Due-date lane.
3. `💰 Money` and `⏸ Big` labels and the crunch file.
4. Recurring-card sweep.

## Questions for Russell

1. Should lane 1 (money and job search) come before lane 2 (due), as drawn, or should a card with a deadline come first?
   Recommendation: as drawn, since a bill with a deadline is already in lane 1 and the remaining due-dated cards are lower stakes.
2. Oldest-Start-first for mail as well as cards: is that what you want, or should only cards flip?
3. Is the `keep` opt-out worth having for the monthly budget, or should every recurring card roll forward?
