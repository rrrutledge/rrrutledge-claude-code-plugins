# physical-task provider - a dedicated calendar of physical-world to-dos (Microsoft Graph API)

A provider for the one class of work nobody but Russell can do: something in the physical world.
Everything else he needs to get done - however long it takes, digital or not - is drained through Trello or the other sources; **this provider exists only for the timing problem physical action has and nothing else does**: it needs to surface right when there's actually enough free time before his next real commitment, not just whenever the day arrives.

Read and cleared entirely through the **Microsoft Graph API** via the **`ms-graph`** skill's `calendar.js` - no browser.
Implements `../engine/provider.md`; classify by `../engine/triage.md` (though see "Why this item bypassed the usual triage judgment" below - this source's triage bucket is decided deterministically, not by the AI triage call). id prefix: `physical-task-`.

> Two-file provider: the **reading/gap mechanics** (enumerate, the due+gap filter, the id scheme) live in the sibling **`physical-task-adapter.py`** that the poller drives.
> This doc is the **worker-facing** prose - AUTH-GLANCE, the captured item shape, CLEAR, and the one thing every other provider's prose doesn't need to say: what "do the item" even means when the work is physical.

## The model
A task is **queued** while it sits on the dedicated calendar (default name **"Physical Tasks"**, configurable) with a start date today-or-earlier AND a start time landing EXACTLY on the overnight parking grid - `:00` at midnight, `:00`/`:15`/`:30`/`:45` at 1 AM, `:00`/`:30` at 2 AM.
The grid's only job is distinguishing queued from started, not encoding task size.

**Exact alignment, not just "sometime in that hour," is what makes this safe.**
"Started" (see WORKER/CLEAR) moves a task's start to the moment its worker session launched - and if Russell is up working past midnight, that launch time can itself fall inside 00:00-02:59.
A real timestamp essentially never lands exactly on a grid slot (down to zero seconds), so exact-slot matching is what actually tells "still sitting untouched since it was placed" from "just started a moment ago" - a same-hour-only check would wrongly read a task he just began at 12:04 AM as still queued.

Nothing here ever moves a queued task to keep it visible; an undone one simply keeps coming back every cycle until it's moved off-grid (started - see WORKER/CLEAR), for as long as it stays within the scan's lookback window (`lookback_days`, default a year - see Config).
That window reaches back well past a month on purpose, so a task waiting through a long stretch keeps surfacing rather than silently aging out of view.
**The same event persists indefinitely, with no separate archive** - it just keeps living on Physical Tasks, eventually parked at the real time it was actually worked, as an ordinary calendar record.

The event's own duration (end minus start, while still queued) is Russell's own estimate of how long the task takes.

**A recurring series is one task, not one-per-missed-occurrence.**
Outlook's recurrence engine expands every date the pattern ever produced between its start and today, so a daily or weekly task left unstarted for a while genuinely has several individually-queued occurrences sitting in Graph - but Russell only ever sees ONE drainable item for it (`enumerate` collapses them to the most recent queued occurrence, see the adapter), and starting it catches the backlog up rather than requiring one CLEAR per missed occurrence (see CLEAR).

**The second gate, unique to this source:** even once a task is queued, the adapter's `enumerate` only returns it when a live free gap of at least that duration **plus a buffer** currently exists before Russell's next REAL commitment (any non-all-day event on any of his editable calendars, whether solo or not, organized by him or not - except an event on the Physical Tasks calendar itself, which the gap check always excludes, since that calendar is the to-do queue, not a commitment).
The buffer covers the lag between the gap being detected and Russell actually opening the session - waiting on worker budget, or just being deep in a different one when this one pops - so a task's own duration alone isn't the bar; duration + buffer is.
That gap is recomputed fresh every poll cycle, so a task becomes eligible the moment enough room opens up and simply waits, unenumerated, until then.

**An opt-in third gate: the time of day.**
A task restricted to a time-of-day window only enumerates while local now falls inside it, however big the gap - see TIME-OF-DAY-WINDOW below.

**An opt-in fourth gate: the day of the week.**
A task restricted to particular weekdays only enumerates on one of them, however big the gap and whatever the time of day - see DAY-OF-WEEK-GATE below.

**Prefer the longest task that fits.**
When several tasks are eligible at once, a long gap shouldn't get spent on a short task while a longer one could have used it - `enumerate` returns eligible tasks sorted longest-duration-first, so the cross-source dispatch order (which otherwise ties on priority band and falls back to arrival order) picks the task that best uses the room available.

**Only one physical task is ever open at a time.**
Russell can only be doing one physical-world thing at once, so even when several tasks are simultaneously eligible, the adapter's `correspondent` returns the same constant identity for every one of them - the poller's ordinary same-correspondent hold (built for "don't dispatch two items from the same person at once") then keeps every task but the first-picked out of dispatch until that one's worker session closes.
A held task simply re-enumerates next cycle, same as one still waiting on its gap.

Every task is estimated at about an hour, even one that might genuinely take two or three, since Russell can always make an hour of progress on it and doesn't need to wait for a rarer multi-hour gap.
That's *why* `lookahead_hours` defaults short (see Config): the gap check never needs to see further ahead than the longest task plus its buffer.

## Config (`.claude/drainer.local.md` → `providers.physical-task`)
- `calendar` - the dedicated calendar's name (default `Physical Tasks`).
- `lookahead_hours` - how far ahead the gap check looks for the next real commitment (default `2`).
  Kept short on purpose (see "The model" above) - raise it only if a task genuinely needs more than about an hour plus its buffer, which shouldn't normally happen.
- `buffer_minutes` - minutes added on top of a task's own duration before it counts as eligible (default `20`), covering the gap-detection-to-session-opened lag described above.
- `lookback_days` - how far back the queued scan reaches (default `365`, a year).
  A task keeps re-surfacing only while it falls inside this window (see "The model" above); raise it toward Graph's ceiling of `1825` (five years) to reach back further.
- `default_window` - an `HH:MM-HH:MM` local time-of-day window applied to every task without a `Window:` marker of its own (unset by default, which leaves such tasks unrestricted - see TIME-OF-DAY-WINDOW).
- `exclude` - calendar names to leave out of the gap check.
  No credentials here - sign in once via `ms-graph`; the MSAL token cache is machine-local.

## AUTH-GLANCE
Run `node calendar.js --list-calendars`.
If it prints calendars, you're signed in.
If it errors with "Not signed in" or an auth error, do the `ms-graph` one-time sign-in (`node scripts/auth.js` via browser-chauffeur), then retry - never surface the token error to Russell.

## CAPTURE
`items/<id>.json`:
`{ "id","source":"physical-task","triage":"needs-you","kind":"work","subject","date","minutes",`
`"isRecurring","calendar","eventId","seriesMasterId","repeatAfter","window","days","url","ts":"<ISO now>" }`
- `subject` - the task itself, in Russell's own words (however he named the calendar event).
- `date` - the day it became queued (YYYY-MM-DD); may be well in the past for something that's sat unstarted.
- `minutes` - the duration Russell estimated (the event's own length while queued) - also the size of the free gap that made this item eligible to dispatch right now.
- `isRecurring` - whether this is a recurring series rather than a one-off.
  When true, `date` is the most recent queued occurrence, not necessarily today - a series left unstarted can accumulate several missed occurrences (see CLEAR's catch-up behavior).
- `eventId` - the raw Graph id `startTaskNow`/`finishTaskNow` act on directly: the event's own id for a one-off, or the **most recent queued occurrence's own instance id** for a recurring one (never the series master - starting one occurrence must never touch the others).
- `seriesMasterId` - present only when `isRecurring` is true; the series master's id, needed only for the backlog cleanup (`--catch-up-series`) after `eventId` has been started.
- `repeatAfter` - the human interval (`"7 days"`, `"1 month"`) when this one-off carries a `Repeat: ... after completion` body marker, else null.
  It's a heads-up for the worker to tell Russell when the task will come back; the actual re-queue is done by CLEAR's Finished step (see REPEAT-AFTER-COMPLETION below), which reads the marker off the event itself, not this field.
- `window` - the `HH:MM-HH:MM` time-of-day window this task was gated by (its own `Window:` marker, else `default_window`), or null when unrestricted.
  Informational only: enumerate already confirmed now falls inside it (see TIME-OF-DAY-WINDOW).
- `days` - the weekday codes this task was gated by (its own `Days:` marker), or null when unrestricted.
  Informational only: enumerate already confirmed today is one of them (see DAY-OF-WEEK-GATE).
- `url` - the event's Outlook `webLink`, so Russell can open the actual calendar item if he wants to.

## Why this item bypassed the usual triage judgment
`run-poller.py` routes every `physical-task` item straight to `needs-you`/`work`, the same deterministic shortcut it uses for a started Trello card or an orphaned session - never through the AI triage call.
There's nothing to judge: the adapter's `enumerate` already established the task is queued AND a real gap for it exists right now, and that combination is definitionally "go do this."
A physical task is never `fyi` or `junk` - there's no inbound noise to distinguish it from, only Russell's own to-dos.

## WORKER - what "do the item" means for physical work
Every other source's step 3 ("do the action") means Claude does the work.
Here it doesn't - the task is physical, so **Russell** does it, and your job is the surrounding logistics:
1. **Lead with the task and the window** - restate the subject, and say plainly that this is the moment for it: "you've got about `<minutes>` minutes before your next real commitment, and this needs about that long."
   Link the event (`url`) so he can glance at the calendar item itself if useful.
2. **Do any prep work that IS digital** before handing it over.
   Anything you can genuinely do to make the physical step faster, do it now, the same as step 3 in the generic worker flow.
3. **When he confirms he's actually starting now, CLEAR the "started" step right away** (see CLEAR) - don't wait for completion to do this part.
4. **Wait for him to actually do it, then ask.**
   Stay with him rather than firing a reminder and moving on - this is interactive.
   When he confirms done (or it's already handled), CLEAR the "finished" step.
   If the item has a `repeatAfter`, say so as you close out ("this'll come back in about a week") - the Finished step queues that next one automatically (see REPEAT-AFTER-COMPLETION).
   If he says now isn't actually a good moment after all before step 3 ever ran (interrupted, the gap turned out to be needed for something else), leave the event exactly as it is, still queued, so it naturally comes back the next time a real gap opens.
   If step 3 already ran and then something interrupted him, still finish it (CLEAR "finished" now) rather than leaving a started-but-never-finished record sitting on the calendar.
5. **Close out per the standard rules** (`../engine/worker-core.md` §6's close conditions) - this session stays open exactly as long as any other needs-you session would: until his part is genuinely done.

## CLEAR
Two ordinary-flow steps plus a recurring-only backlog sweep - everything acts on `eventId` from CAPTURE, and **nothing here ever deletes or archives the task itself**:

- **Started** (step 3 above, the moment he confirms he's beginning): `node calendar.js --start-now=<eventId> --at=<ts from items/<id>.json>`.
  Pass `--at` the item's `ts` - the instant this worker session launched, which the poller stamps moments before spawning the session.
  That makes the recorded start the moment the task surfaced to Russell, not the moment he typed his "I'm starting" reply, so a task he glances at and finishes in one sitting still records a real span (start = session launch, end = when he says done) instead of collapsing start and end onto that single reply.
  Moves the event's start to that launch time and keeps its own duration - this is what takes it off the parking grid, so it stops being queued, permanently, with no further action needed.
  A recurring occurrence detaches from its series here (expected, same as the Outlook UI) - only this one instance was started, every future occurrence is untouched.
- **Finished** (step 4, once he confirms done): `node calendar.js --finish-now=<eventId> --calendar=<calendar from the item>`.
  Stamps just the end time to now, leaving start exactly where "started" put it - so the event's real elapsed span (start = when the session launched, end = when he actually finished) sits on the calendar afterward as an accurate record.
  Always pass `--calendar` (the item's `calendar`): it costs nothing on an ordinary task and is what lets a repeat-after-completion task (see below) place its successor - `--finish-now` on its own no-ops the repeat when the event has no marker.
- **Recurring backlog cleanup** (only after Finished, only when `isRecurring` is true and the item's `date` in CAPTURE was well in the past): `node calendar.js --catch-up-series=<seriesMasterId> --except-id=<eventId>`.
  A series left unstarted for a while can rack up several individually-queued occurrences (Graph expands every date the pattern ever produced, not just the next one) - those older ones are just recurrence-expansion noise, not independently meaningful records, so this deletes all of them through today EXCEPT the one `eventId` just turned into a real started/finished record.
  Never touches the series master, so every future occurrence keeps arriving on its own schedule.

**Never run Started before Russell has actually said he's beginning, and never run Finished before he's confirmed done** - unlike a source whose scope is knowable up front (see `../engine/worker-core.md` §2d), a physical task's progress can only be observed by asking him.
If he defers before Started ever ran, do nothing at all: the event is untouched and simply stays queued.

## REPEAT-AFTER-COMPLETION
A one-off can recur a fixed interval **after Russell last did it**, rather than on a fixed calendar schedule - the one timing Outlook's own recurrence can't express.
It opts in with one line in its event body: `Repeat: 7 days after completion` (also `1 week`, `1 month`).
The Finished step handles the rest automatically because it passes `--calendar` (see CLEAR): on finish, `calendar.js` reads the marker and queues the next one-off that interval out, carrying the marker forward.
`repeatAfter` in CAPTURE is the parsed heads-up to mention as you close out.

Put a marker on a **one-off only** - a recurring series already spawns its own occurrences, so a marker on one would double up.
The mechanism and its edge cases live in `calendar-repeat.js` and `finishTaskNow` (calendar.js), not here.

## TIME-OF-DAY-WINDOW
A task opts into a time-of-day gate with one line in its event body: `Window: 09:00-20:00` (24-hour local wall-clock, start inclusive, end exclusive, wraps past midnight when the start is later than the end).
`default_window` in Config applies the same restriction to every task with no marker of its own.
Outside the window, enumerate skips the task for that poll cycle only - it stays queued and re-checks next cycle.
An open worker session runs to completion regardless, and a repeat-after-completion successor carries the `Window:` line forward.
Parsing and clock math live in `calendar-window.js`.

## DAY-OF-WEEK-GATE
A task opts into a day-of-week gate with one line in its event body: `Days: SA` (comma list for more than one: `Days: SA,SU`), using the same two-letter weekday codes as `--create-recurring`'s `--days` (`MO,TU,WE,TH,FR,SA,SU`).
Outside those days, enumerate skips the task for that poll cycle only - it stays queued and re-checks next cycle.
An open worker session runs to completion regardless, and a repeat-after-completion successor carries the `Days:` line forward.
Parsing lives in `calendar-days.js`.

## JUNK-LEARNING
N/A - every item here is a task Russell put on his own calendar, never inbound noise.

## DRAFT-MODE
N/A by default - most physical tasks need no outbound message.
When one genuinely does, that's ordinary work done in step 2 above through whatever channel it actually needs.
