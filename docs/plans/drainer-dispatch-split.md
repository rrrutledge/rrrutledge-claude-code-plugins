# Drainer: split dispatch from discovery

Status: approved direction; revised 2026-11-11 with the phase timings, the rate limits, and the decisions for steps 1 and 2.

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
- **Where the time goes, measured on 2026-11-11.**
  A timed dry-run took 4 minutes 4 seconds:

  | Phase | Time |
  |---|---|
  | Config worktree refresh and session scan | 4s |
  | Reconcile (it also lists the email inboxes, which the listing step then reuses) | 15s |
  | Listing all sources (Trello 17s, Slack 7s, orphan sessions 1s, the rest under 1s) | 25s |
  | Re-reading the body of every held Gmail message before judging | 162s |
  | Triage calls | 12s |
  | Security screen calls | 19s |
  | Session counts, correspondent and conversation holds | 6s |

  The AI calls are not the cost.
  Before judging, the poller fingerprints each held item's text to look it up in the verdict cache, and Gmail's text is a body fetch over a fresh IMAP login.
  34 Gmail items were held, so every cycle made 34 logins at about 5 seconds each just to find out it already had their verdicts.
  The pending list removes this: an item already on the list, whose listing fields are unchanged, keeps its stored verdicts and is not re-read.
  That should bring a full cycle with nothing new to about 80 seconds.
  The dry-run reported a target of 1 waiting session because the shell it ran in had `DRAINER_TARGET_REVIEWABLE=1`; the User-scope value the scheduled task reads is now 15, not 10.
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

## Rate limits and poll floors

- **Outlook inbox: 15 seconds.**
  A poll is one Graph list call.
  Graph allows about 10,000 requests per 10 minutes per mailbox, and a 15-second poll uses about 40 of them.
- **Slack: 30 seconds.**
  The drainer reads Slack with Russell's browser session token, not an app, so the 2025 rule that limits `conversations.history` for new non-Marketplace apps most likely does not apply; Slack's page does not say how session tokens are treated.
  A quiet poll makes 4 calls (`auth.test`, the mute list, `client.counts`, the thread view).
  Each unread conversation adds about 3 more (`conversations.history` twice or `conversations.replies`, and `conversations.info`), plus one `users.info` per sender.
  `conversations.history` is Tier 3, about 50 a minute; the client-only methods have no published tier.
  So a quiet poll is far under every limit, but a burst of unreads is not, which is why the floor is 30 seconds and the fast loop must honor a 429's `Retry-After`.
  Caching `users.info` and the known-contact check across polls, which the fast loop makes possible, cuts the burst cost.
- **Gmail: 30 seconds now, 15 with a persistent connection.**
  The adapter lists the newest 500 inbox messages over IMAP, envelopes only, as one fresh login per poll.
  Gmail allows 15 simultaneous IMAP connections and about 2,500 MB of IMAP download a day; a 15-second poll is 5,760 polls a day, which at a few hundred KB each approaches the cap.
  Google publishes no login-rate limit, but frequent fresh logins can trip its suspicious-sign-in check.
  For a 15-second floor the fast loop should hold one IMAP connection open and fetch only messages newer than the last one it saw.
  The per-message logins the current cycle makes (the 162 seconds above) are the bigger login risk today, and the pending list removes them.
- **Orphan sessions: 15 seconds.**
  A poll reads local session files only.

## Decisions for steps 1 and 2

- **One keeper process with two threads.**
  The scheduled task starts one long-running keeper.
  Its main thread is the dispatch loop.
  A second thread runs the existing full cycle as a child process, in a new feed-only mode that writes to the pending list and opens no workers.
  A child process gives the isolation that matters: a feed cycle that hangs is killed after 30 minutes and the next one starts, and the memory a cycle uses is returned when it exits.
  The dispatch loop itself makes only bounded calls (the session scan, worker launches, the one-call hand-over to a live worker), so it does not need a process of its own.
  When steps 3 and 4 add the fast loops, each becomes another feed thread in the same keeper.
- **The feed runs every 5 minutes without skipping.**
  The next cycle starts 5 minutes after the previous one started, or at once when that one ran longer, so an overrun delays the next cycle instead of dropping it.
  Each cycle that finishes wakes the dispatch loop, which otherwise runs once a minute.
- **One file per item for the pending list.**
  Each entry is `<runtime_dir>/pending/<digest of source and id>.json`, holding the item with its verdicts, and is written to a temp file and then renamed over the target.
  The feed only writes and prunes entries, and the dispatch loop only deletes the entry of an item it just recorded seen, so the two never rewrite a shared file.
  Listing about 1,700 small files each minute costs well under a second, and the dispatch loop orders them in memory by the existing ordering.
  The feed removes an entry once a source that listed cleanly no longer offers its item as new, which covers an item dispatched, one Russell handled by hand, and one re-judged as not needing him.
- **Seen-state writes take a lock.**
  The seen record is one JSON file rewritten whole on every write, and until now it had no lock.
  With the feed and the dispatch loop running at the same time, a lost write could drop a "dispatched" mark and open a second worker for the same item, so every write to it now holds a short file lock.
- **The scheduled form of the poller becomes the keeper.**
  The task runs `run-poller.py --repo <repo>`, and that form now starts the keeper instead of one cycle, so merging changes nothing in the task's registration.
  `--once` runs one feed cycle and one dispatch pass, for a manual run.
  `--dry-run` and the on-demand `--channel` run keep their current behavior; the on-demand run dispatches under the same dispatch lock the keeper uses, so the two never dispatch at once.
- **Recycling.**
  The keeper exits cleanly after 4 hours, when its memory passes 600 MB, or when a newer drainer version has been installed (it checks every 15 minutes), and the task starts a fresh one within 5 minutes.
  A running feed cycle is allowed to finish first, and the keeper keeps dispatching while it waits.
  It records each start, feed cycle, failure, and recycle in `<runtime_dir>/keeper.log`.

## Rollout

1. Measure the post-listing phases of one cycle (reconcile, judge, screen, session scans, capture).
   Done; see the timing table above.
2. Land the pending list and the dispatch loop, with the current full cycle still feeding it, so dispatch speed improves first.
3. Move Slack, Gmail, Outlook inbox and orphan sessions to their own fast loops.
4. Move the slow sources to the 15-minute batch and make the full cycle the safety-net scan.
5. Run the old and new paths side by side in shadow mode: the new path records what it would have opened and the old path still opens the workers, then compare the two logs over a few days.
   Only one path ever opens workers.
   Item ids are deterministic and an item is recorded as seen when its worker opens, so an item found by both paths is recognized as the same one, but two paths racing at the same instant could still both open a worker, which is why they do not both spawn.
6. Switch dispatch to the new path and remove the old one.
