# Drainer queue prioritization - design

Card: https://trello.com/c/YnIyEj0V
Source: Russell's 2026-11-09 self-email "Re: Getting behind".

This changes the order of the queue and nothing about how the queue is drained.
Dispatch stays as it is: while sessions waiting on Russell are under `target_reviewable` and total sessions are under `max_concurrent`, the next item in queue order is dispatched.
Mail and cards both stay newest first within a lane, as today, so working backwards through the queue still reaches the previous day's items.

## What the queue does today

Every drained item, from every source, sorts by one key: `(priority band, level band, referral band)` and then its date, newest first.
The tuple lives in `provider_base.band_rank`, and the date is `Start` for cards and received time for mail and Slack.
Recent orphan sessions jump the line before any of that.

Only job-search cards carry a non-neutral band.
The band rises for a `👤 Contact` card, a card on an in-progress list, a P1 Director-or-above role, and a P1 manager at or above $250K.
A plain P1 role stays neutral and interleaves with email.
Everything else (bills, invoices, sponsorship, every other board) is neutral and orders by date alone.

Trello's native Due field is never read.

## Proposed model: lanes, then date

An item takes the highest lane it qualifies for.
Inside a lane the existing tiebreakers apply, then newest first, except the due lane, which orders by soonest Due.

| Lane | What is in it | Within the lane |
|---|---|---|
| 0. Orphan sessions | Recent orphan sessions, unchanged | As today |
| 1. Money | Job search (`👤 Contact` cards, cards on an in-progress list, every P1 role) and any card labelled `💰 Money` (bills, the ISC invoice, the monthly budget, sponsorship follow-up) | Existing level then referral order, then newest first |
| 2. Due | Any card with a Due date that is not marked complete and whose Start has arrived | Soonest Due first |
| 3. Neutral | Email, Slack, and every ordinary card | Newest first |
| 4. Below the line | P2/P3 roles, as today | Existing P2 then P3 order |

### Decisions this makes

**Job search and money are one lane, ahead of the due lane.**
Both are about money coming in or going out, so a P1 role, a contact follow-up, a bill, and an invoice all rank together.
A plain P1 role moves up out of the inbox, since every P1 role now sits in this lane.
Level and referral still order job-search cards against each other.

**Due-dated work is read from Trello's Due field.**
A Start date keeps meaning "unblocked, do it when you can".
A card with a Due date and a Start that has arrived has been started on purpose and has a deadline, so it enters lane 2 and sorts by how soon it is due.
There is no time window: once Start has arrived, the Due date counts.
A bill carries both the money label and a Due date and lands in lane 1.

**Money is a label, not a guess.**
The pod's own money skills (budget, ISC invoice, sponsorship follow-up, bill cards) apply `💰 Money` when they create or recur their card, and the adapter reads the label like it reads `🤝 Referral`.
Money-related email is not re-ranked in the first version.

## Recurring cards that get missed

There is one card per recurring task, and its Start moves forward only when an occurrence is finished.
A missed occurrence therefore leaves the card with an old Start, and no "next occurrence" exists to arrive on its own.

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

A card that is a day late is not touched: the sweep acts only once a full period has passed.
The monthly budget, which starts on the first of the month for the previous month, stays on its date for a month of grace, so working backwards through the queue still reaches it.
Every recurring card follows the same rule, with no opt-out word.

## Where the change lands

All in the drainer plugin, in four places.

1. `provider_base.band_rank` becomes lane-aware (a single `lane` value first), and stays the one definition both sort sites read.
2. The Trello adapter reads `due` and `dueComplete` and the `💰 Money` label, and stamps the lane on each item.
3. The poller's cross-source sort reads the same lane value, which keeps mail and Slack in lane 3, and the poller runs the recurring-card sweep.
4. `trello-queue-model.md` and `trello-provider.md` change to match: the "Due is informational" paragraph is rewritten, since Due becomes a ranking input, and the `Recurs:` section gains the sweep.

In this repo the pod's money skills and recurring cards gain the `💰 Money` label.
The existing band-ranking tests extend to cover lane order and the sweep's date math.

## Left out for now

A manual "now" pin, a `⏸ Big` label with a crunch-mode flag, per-lane dispatch limits, and flipping the date order to oldest first are not part of this plan.
Each can come back as its own change if a real case turns up.

## Rollout

Each step is its own PR and can ship alone.

1. Lift every P1 role into the top lane (smallest change).
2. Due-date lane.
3. `💰 Money` label.
4. Recurring-card sweep.
