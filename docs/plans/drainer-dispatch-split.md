# Drainer: split dispatch from discovery

Status: draft for Russell's review, revised 2026-11-11 after his first round of feedback.

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
  The scheduled task is registered to ignore a new start while the previous one is still running, so an overrunning cycle makes the next one skip, which produces the long quiet gaps.
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
2. **One dispatch loop that never touches a source.**
   About once a minute it reads the pending list, counts waiting and live sessions, and opens workers for the top items by the existing ordering, up to the free slots.
   It keeps the existing hold on a correspondent that already has an open worker (an open worker from the same sender, or on the same card, makes a second item wait so one worker reads both), and it checks that an item is still pending, meaning its source object is not already archived or handled, before opening a worker.
   It is the only part that opens workers, so two paths can never open two workers for one item.
3. **Discovery loops that only add to the pending list.**
   New items are judged once and the result is written to the pending list.

### Two discovery categories

- **Fast: Slack, Gmail, Outlook inbox, orphan sessions.**
  Each runs back to back, starting the next poll as soon as the last one finishes, with a short floor of 15 to 30 seconds between polls.
  A poll costs one 3 to 6 second script run, so the floor keeps the machine and the sources from being hammered while a new message still reaches the pending list within seconds.
- **Slow: everything else, in one batch every 15 minutes at most.**
  This is Trello, the Outlook junk folder, the sponsorship sheet, FINOS Slack, Zoom, the community call, and physical tasks.
  One batch is simpler than several cadences, and 15 minutes is fine for all of them: physical tasks need a calendar gap that lasts far longer than 15 minutes anyway, and a Trello card with a Start time of 9:00 AM surfaces by 9:15 AM, which covers cards such as a thank-you note after an interview.
  Zoom and the community call keep their own internal throttles (20 and 60 minutes), which are unaffected.

### The safety net is the slow batch

Every 15 minutes the slow batch also does the full scan of every source, including the fast ones, and compares it with the pending list and the seen record.
It catches anything a fast loop missed, mail moved back into the inbox, and work whose worker died (what the current reconcile step does).
There is no separate safety-net schedule.
Because judging moves into discovery, a full scan with nothing new should take about a minute, so a 15-minute interval has wide margin.

### Triage once per item

Each item is judged when it is first discovered, and the verdicts are stored with it in the pending list.
The 24-hour verdict expiry no longer applies to a pending item, so an item held for a day is not judged again.
The cost saving is smaller than "once instead of every cycle", because the existing cache already avoids most repeat calls within 24 hours.
The larger win is that judging leaves the dispatch path, so a slow AI call can never delay opening workers.

### Failure behavior kept as is

- The usage-cap backoff still holds items that have no verdict.
- The failed-session-scan case still opens nothing (fails closed).
- Source health bookkeeping and the daily digest of fyi and junk items are unchanged.

### Running continuously on Windows

The existing `DrainerKeeper` scheduled task already repeats every 5 minutes and is registered with "ignore a new start while one is running".
That is a watchdog already: make the long-running process the thing the task starts, and every 5 minutes the task either finds it running and does nothing, or restarts it if it died.
No new Windows mechanism is needed.
To verify when building: the process also takes its own single-instance lock, so a second start from any path exits at once.

## Rate limits checked so far

- **Gmail API:** a list call costs 5 quota units against a per-user limit of several thousand units per minute, so polling every 15 to 30 seconds is negligible.
- **Microsoft Graph (Outlook):** the documented limit is about 10,000 requests per 10 minutes per mailbox per app, so the same cadence is negligible.
- **Slack is the one to check.**
  Slack's 2025 rule limits `conversations.history` to 1 request per minute for apps created after 2025-05-29 that are not in the Marketplace.
  The drainer reads Slack with a user web token, not an app, so the rule may not apply, and the adapter lists unread conversations rather than reading history.
  What its calls are, and what limits each one carries, is to be confirmed before choosing the Slack floor.
- **Gmail through IMAP:** the drainer's Gmail listing runs through IMAP rather than the REST API, so the REST figures above do not directly apply; the IMAP connection and bandwidth limits need a check too.

## Open questions

1. **One process or one per category?**
   One supervisor with a thread per source, or separate processes, trades simplicity against isolation when a source hangs.
2. **Pending list format.**
   One file per item (like the existing `items/` directory) avoids two loops writing the same file, but needs a cheap way to list and order them.
3. **What is the missing four minutes?**
   Time the steps after listing before building, so the plan targets the real cost.
4. **Slack and IMAP limits** as described above.

## Rollout

1. Measure the post-listing phases of one cycle (reconcile, judge, screen, session scans, capture).
2. Land the pending list and the dispatch loop, with the current full cycle still feeding it, so dispatch speed improves first.
3. Move Slack, Gmail, Outlook inbox and orphan sessions to their own fast loops.
4. Move the slow sources to the 15-minute batch and make the full cycle the safety-net scan.
5. Run the old and new paths side by side in shadow mode: the new path records what it would have opened and the old path still opens the workers, then compare the two logs over a few days.
   Only one path ever opens workers.
   Item ids are deterministic and an item is recorded as seen when its worker opens, so an item found by both paths is recognized as the same one, but two paths racing at the same instant could still both open a worker, which is why they do not both spawn.
6. Switch dispatch to the new path and remove the old one.
