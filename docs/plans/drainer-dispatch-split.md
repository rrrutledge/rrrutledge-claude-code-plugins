# Drainer: split dispatch from discovery

Status: draft for Russell's review, 2026-11-11.

## The problem

Russell wants 10 sessions waiting for his review at all times (`DRAINER_TARGET_REVIEWABLE=10`).
The drainer does not hold that number: it opens a worker or two, then goes quiet for 10 to 110 minutes while he has a large pile of items that need him.

## What the investigation found

- **The setting and the slot math are fine.**
  The target is 10, the live-session ceiling is the default 18, and at the time of the check 4 sessions were waiting with 9 live, so 6 slots were open.
- **Supply is not the limit.**
  A read-only dry-run on 2026-11-11 saw 1,796 needs-you items, 1,683 of them Trello cards.
- **Cause 1: every Trello card without a contact label shares one identity per board.**
  The poller holds a second item whose correspondent already has an open worker.
  A Trello card's correspondent is its contact labels, or the board name when it has none, so most cards on a board hold each other and the board gets one worker at a time.
  The fix is separate and small: each card is its own identity.
  It is already with its own session and draft PR, so this plan does not cover it.
- **Cause 2: one cycle takes longer than the cycle interval.**
  The keeper starts a cycle every 5 minutes, and the full dry-run took 5 minutes 14 seconds.
- **Where the time goes is only partly known.**
  Listing all 11 sources takes about 60 seconds in total (Trello 18s, junk folder 15s, physical tasks 8s, Slack 6s, Gmail 6s, Outlook 3s, the rest under 2s).
  The other four minutes happen after listing.
  The likely cost is the two sequential AI calls that judge new items (triage, then the security screen), but this is unmeasured.
- **Every cycle re-lists everything and nothing remembers a pending item.**
  An item the poller cannot open a worker for stays unrecorded and is found again by the next full listing.
  Verdicts are cached for 24 hours, so an item held longer than that is judged again.

## Design

### Three parts

1. **A pending list on disk.**
   Every item that needs a worker is stored there once, with its triage and screen verdicts, until a worker has been opened for it.
2. **A dispatch loop that never touches a source.**
   About once a minute it reads the pending list, counts waiting and live sessions, and opens workers for the top items by the existing ordering, up to the free slots.
   It checks that an item is still pending (its source object not already archived or handled) before opening a worker.
3. **Discovery loops that only add to the pending list.**
   Each source is polled on its own cadence, new items are judged once, and the result is written to the pending list.

### Two discovery categories

- **Fast, polled every few seconds to a minute:** Slack, Gmail, Outlook inbox, and (to confirm) orphan sessions and physical tasks.
  A new Slack message should reach the pending list within seconds.
- **Slow, polled about every 15 to 60 minutes:** Trello, the Outlook junk folder, the sponsorship sheet, Zoom, the community call, FINOS Slack.
  Polling these more often is cheap in cost but gains nothing, because their items are not time-critical.

Fast sources need a cheap "anything new since X" call.
Slack and the two mail sources have one; each adapter's call needs confirming.
Trello cannot use a newest-item marker, because a card becomes eligible when its Start date passes, so it keeps asking "which cards are eligible now".

### Safety net

One full scan of every source, on its own slow schedule, compares sources against the pending list and the seen record.
It catches anything a fast loop missed, mail moved back into the inbox, and work whose worker died (what the current reconcile step does).
It no longer sets how quickly a new message is noticed.

### Triage once per item

Each item is judged when it is first discovered, and the verdicts are stored with it in the pending list.
The 24-hour verdict expiry no longer applies to a pending item, so an item held for a day is not judged again.
The cost saving is smaller than "once instead of every cycle", because the existing cache already avoids most repeat calls within 24 hours.
The larger win is that judging leaves the dispatch path, so a slow AI call can never delay opening workers.

### Failure behavior kept as is

- The usage-cap backoff still holds items that have no verdict.
- The failed-session-scan case still opens nothing (fails closed).
- Healthy-source bookkeeping and the daily digest of fyi and junk items are unchanged.

## Open questions

1. **How do continuous loops run on Windows?**
   Today `DrainerKeeper` is a scheduled task that starts one cycle every 5 minutes.
   A continuous loop is a long-running process, so the scheduled task becomes a watchdog that starts it if it is not running.
2. **One process or one per category?**
   One supervisor with a thread per source, or separate processes, trades simplicity against isolation when a source hangs.
3. **Pending list format.**
   One file per item (like the existing `items/` directory) avoids two loops writing the same file, but needs a cheap way to list and order them.
4. **Which of orphan sessions and physical tasks are fast?**
   Physical tasks depend on a free calendar gap, which can open at any minute.
5. **What is the missing four minutes?**
   Time the steps after listing before building, so the plan targets the real cost.

## Rollout

1. Measure the post-listing phases of one cycle (reconcile, judge, screen, session scans, capture).
2. Land the pending list and the dispatch loop, with the current full cycle still feeding it, so dispatch speed improves first.
3. Move Slack, Gmail and Outlook inbox to their own fast loops.
4. Move the slow sources to their own loop and turn the full cycle into the safety-net scan.
5. Remove the old per-cycle path once the new one has run alongside it for a few days.
