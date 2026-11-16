# Drainer queue prioritization - design

Card: https://trello.com/c/YnIyEj0V
Source: Russell's 2026-11-09 self-email "Re: Getting behind".

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

Intake is already throttled.
The poller opens a new worker only while the sessions waiting on Russell are below `target_reviewable` and total live sessions are below `max_concurrent`.
That throttle counts sessions and ignores what they are about, so an urgent item and a low-value one wait on the same cap.

## Proposed model: five lanes, then date

The queue becomes an ordered list of lanes.
An item takes the highest lane it qualifies for.
Inside a lane the existing tiebreakers apply, with one change to the date direction described below.

| Lane | What is in it | Within the lane |
|---|---|---|
| 0. Pinned | Anything Russell marks `🔝 Now`, plus recent orphan sessions (unchanged) | Most recently pinned first |
| 1. Job search live | `👤 Contact` cards, cards on an in-progress list, every P1 role (today only Director-or-above and well-paid managers are lifted) | Existing level then referral order, then oldest Start first |
| 2. Due | Any card with a Due date that is not marked complete, due within 7 days or already overdue | Soonest Due first |
| 3. Money | Cards labelled `💰 Money`, and sponsorship-sheet rows whose check-in has arrived | Soonest Due first when it has one, else oldest Start first |
| 4. Neutral | Email, Slack, and every ordinary card | Oldest Start first |
| 5. Below the line | P2/P3 roles, and `⏸ Big` cards while crunch mode is on | Existing P2 then P3 order |

Lanes 1 to 3 follow the order Russell stated: job search first, then due dates, then money.

### Decisions this makes

**A plain P1 role moves up out of the inbox.**
Russell said P1 roles come before everything including email, so the P1 band lifts for every P1.
Level and referral still order P1 roles against each other.

**Due-dated work is read from Trello's Due field.**
A Start date keeps meaning "unblocked, do it when you can".
A Due date means "this has a deadline", and the adapter reads it for the first time.
A card due within 7 days or overdue enters lane 2 and sorts by how soon it is due.
A card due later than 7 days ranks as if it had no Due until it comes inside the window, so a far-off deadline does not crowd out the lanes above.

**Overdue work stops sinking.**
Today the newest date sorts first, so stale cards are worked last.
Inside lanes 1, 3, and 4 the order flips to oldest Start first, so work that has been waiting longest comes up before work that just arrived.
This is the change that stops items dropping silently, and it applies to mail as well.

**Money is a label, not a guess.**
The pod's own money skills (budget, ISC invoice, sponsorship follow-up, bill cards) apply `💰 Money` when they create or recur their card, and the adapter reads the label like it reads `🤝 Referral`.
Bills carry both the label and a Due date, so they enter lane 2 as their deadline nears and fall back to lane 3 otherwise.
Money-related email is not re-ranked in the first version.

**Big pieces of work defer in a crunch.**
A `⏸ Big` label marks a card as a large piece of work.
While crunch mode is on, those cards drop to lane 5.
Crunch mode is a single file in the drainer config repo, `drainer-local/crunch.yaml`, holding an end date (`until: 2026-11-14`).
It switches itself off when the date passes, so a forgotten flag cannot defer work forever.
Russell edits one line to start a crunch, and the poller reads it every cycle.

### The override

`🔝 Now` is the manual pin.
It puts a card in lane 0 until Russell removes it.
Everything else is automatic, so the stated rule above is what runs, and the pin is the one escape hatch.

## Review load and intake

Russell's point that a finished item still costs review time is real, and the existing throttle does not see it.

Proposal: count only sessions that wait on Russell (blocked or idle, which `worker_counts` already separates) and give each lane its own ceiling.
Lanes 0 to 3 dispatch whenever the overall `max_concurrent` has headroom.
Lane 4 dispatches only while waiting sessions are under `target_reviewable`.
Lane 5 dispatches only when waiting sessions are under half of `target_reviewable`.

This means a full review pile holds back new mail and ordinary cards but never holds back a P1 contact, a deadline, or a bill.
It also means the throttle finally trades off by value instead of by arrival.

Letting agents finish more on their own is separate from ranking and not part of this change.
The existing `auto-handle` path already carries it, and widening that is better done from the triage rules once this ordering is live.

## Where the change lands

All in the drainer plugin, in four places.

1. `provider_base.band_rank` becomes lane-aware (a single `lane` value first), and stays the one definition both sort sites read.
2. The Trello adapter reads `due` and `dueComplete`, the `💰 Money`, `⏸ Big`, and `🔝 Now` labels, and the crunch file, and stamps the lane on each item.
3. The poller's cross-source sort reads the same lane value for mail and Slack, which keeps them in lane 4, and applies the per-lane dispatch ceilings.
4. `trello-queue-model.md` and `trello-provider.md` change to match: the "Due is informational" paragraph is rewritten, since Due becomes a ranking input.

In this repo the pod's money skills and recurring cards gain the `💰 Money` label.
The existing band-ranking tests extend to cover lane order, the date-direction flip, and a due-window boundary.

## Rollout

Each step is its own PR and can ship alone.

1. Date direction flip plus lifting every P1 (smallest change, immediate relief).
2. Due-date lane.
3. `💰 Money`, `⏸ Big`, `🔝 Now` labels and the crunch file.
4. Per-lane dispatch ceilings.

## Questions for Russell

1. Should a card due within 48 hours outrank a live job-search contact, or does job search always lead as you stated?
   Recommendation: job search always leads, since a 48-hour deadline you can see coming is usually already a card you are working.
2. Is the 7-day due window right, or do you want a shorter or longer one?
3. Oldest-Start-first for mail as well as cards: is that what you want, or should only cards flip?
