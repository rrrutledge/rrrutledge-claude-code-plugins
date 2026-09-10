// List / create / update events on a personal Outlook calendar via Microsoft Graph.
//
// List upcoming:   node calendar.js --days=14
// List calendars:  node calendar.js --list-calendars            (prints "name<TAB>id")
// List a range:    node calendar.js --list --calendar="InnerSource Commons" \
//                    --start=2026-05-01 --end=2026-05-31 [--tz=America/Chicago] [--json]
//                    (a NAMED calendar over an explicit date range; calendarView expands
//                     recurrences and paginates; --json emits a structured array for scripts)
// Create event:    node calendar.js --create --subject="Dentist" \
//                    --start="2026-06-20T15:00:00" --end="2026-06-20T16:00:00" \
//                    [--location="..."] [--body="..."] [--attendees=a@x,b@y] [--reminder=N]
//                    [--calendar="..."]  (default: primary calendar)
// Create recurring: node calendar.js --create-recurring --subject="Drop off Ryan" \
//                    --start=06:35 --end=06:55 --days=MO,TU,TH \
//                    --range-start=2026-08-31 --range-end=2026-10-31 \
//                    [--location="..."] [--reminder=N] [--calendar="..."]
//                    (weekly recurrence; --days from MO,TU,WE,TH,FR,SA,SU; --range-end is inclusive;
//                     --calendar defaults to the primary calendar)
// Delete one occurrence of a recurring series:
//                   node calendar.js --delete-occurrence --subject="Drop off Ryan" --date=2026-09-14
// Reschedule one occurrence of a recurring series (same day, different time):
//                   node calendar.js --reschedule-occurrence --subject="Drive Ryan" \
//                     --date=2026-08-31 --start=06:35 --end=06:55
// Update one occurrence's subject/body (recurring series, same day and time):
//                   node calendar.js --update-occurrence --subject="Youth Activities" \
//                     --date=2026-09-09 --new-subject="Youth Activities - Fun & Fitness: Line Dancing" \
//                     [--body="..."] [--calendar="Family Commitments"]
// --delete-occurrence / --reschedule-occurrence / --update-occurrence all take an optional
// --calendar=<name> to scope the series lookup to a secondary calendar (default: primary
// "Calendar") - needed when the recurring series lives somewhere other than the default calendar.
// Delete a plain (non-recurring) event by subject + date:
//                   node calendar.js --delete-event --subject="Dentist" --date=2026-06-20
// Update reminder: node calendar.js --update --subject="Dentist" --reminder=off
//                    [--date=YYYY-MM-DD]  (narrows to one day when the subject isn't unique)
// Create calendar: node calendar.js --create-calendar="Physical Tasks"
//                    (idempotent — reports the existing id if a calendar with that name is already there)
// List due tasks:  node calendar.js --list-due-tasks --calendar="Physical Tasks" [--json] [--tz=]
//                    [--lookback-days=30]
//                    (every non-all-day event on that calendar that's still QUEUED — start date
//                     within the last `lookback-days` (default 30) through today, AND start time
//                     EXACTLY on the overnight parking grid: :00 at
//                     midnight, :00/:15/:30/:45 at 1 AM, :00/:30 at 2 AM (not just somewhere in that
//                     hour — a started task's real timestamp carries seconds and essentially never
//                     lands exactly on a slot, so exact alignment tells "still sitting untouched"
//                     from "moved") — with
//                     its duration in minutes; nothing here ever moves a task, so an undone one just
//                     keeps showing up until it's moved off-grid (see --start-now). A recurring
//                     series collapses to ONE row — id = its most recent queued occurrence,
//                     seriesMasterId alongside it — even when several occurrences are overdue; see
//                     --catch-up-series to clean up the rest once one's been started)
// Start a task now:  node calendar.js --start-now=<eventId> [--at=<ISO>] [--tz=]
//                    (moves start to `--at` when given, else right now, keeping the task's own
//                     duration — pulls it off the parking grid, which is what "no longer due" means
//                     here; no delete, no second calendar. Pass --at with the moment the work
//                     surfaced — the physical-task worker passes its terminal tab's launch time — so a
//                     quick task's start reflects when it appeared, not when the "starting" reply was
//                     typed. A recurring occurrence detaches from its series, same as dragging it in
//                     the Outlook UI)
// Finish a task now: node calendar.js --finish-now=<eventId> [--tz=]
//                    (stamps just the end time to now, leaving start where --start-now put it, so the
//                     event's real elapsed span sits on the calendar afterward)
// Catch up a series: node calendar.js --catch-up-series=<seriesMasterId> [--except-id=<eventId>] [--tz=]
//                    (deletes every OTHER overdue occurrence of that series through today — the
//                     backlog is just recurrence-expansion noise once one occurrence has actually
//                     been started via --start-now, which is what --except-id preserves — WITHOUT
//                     touching the series master or any future occurrence)
// Gap to next commitment: node calendar.js --gap-minutes [--json] [--tz=] [--lookahead-hours=2]
//                    [--exclude="Name,Name"]
//                    (minutes from now until the next REAL commitment — a non-all-day event with
//                     someone other than Russell on it, or one he didn't organize — across every
//                     writable calendar; 0 if one is in progress right now; caps at the lookahead
//                     window when nothing is found that soon — the default is short because no
//                     physical task needs to see further than its own longest possible size + buffer)
// Delete event by id (works for a one-off OR a single recurring occurrence's instance id):
//                   node calendar.js --delete-event-id=<id>
// Move event by id to a different day, same time-of-day and duration (works for a one-off OR a single
// recurring occurrence's instance id — the occurrence detaches from its series, same as the Outlook UI):
//                   node calendar.js --move-event-id=<id> --date=YYYY-MM-DD
//
// Times are interpreted in --tz (default America/Chicago). Events have NO reminder by
// default; --reminder=N turns on a pop-up N minutes before start (0 = at start), --reminder=off
// turns it back off.
//
// Reusable from other scripts (require.main-guarded, so requiring this file does not run the CLI):
//   const { getEvents, getCalendars, listDueTasks, getGapUntilNextCommitment } = require('<ms-graph>/scripts/calendar.js');
//   const events = await getEvents({ calendar: 'InnerSource Commons', start: '2026-05-01', end: '2026-05-31' });

const { getGraphClient } = require('./graph-client');

const args = Object.fromEntries(
  process.argv.slice(2).map(a => {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    return m ? [m[1], m[2] ?? true] : [a, true];
  })
);
const TZ = args.tz || 'America/Chicago';

async function listUpcoming(client) {
  const days = parseInt(args.days || '14', 10);
  const now = new Date();
  const end = new Date(now.getTime() + days * 86400000);
  const data = await client.api('/me/calendarView')
    .query({ startDateTime: now.toISOString(), endDateTime: end.toISOString() })
    .header('Prefer', `outlook.timezone="${TZ}"`)
    .top(100).orderby('start/dateTime')
    .select('subject,start,end,location,isReminderOn,reminderMinutesBeforeStart')
    .get();
  const events = data.value || [];
  if (!events.length) { console.log(`No events in the next ${days} days.`); return; }
  console.log(`${events.length} event(s) in the next ${days} days:\n`);
  for (const e of events) {
    const start = (e.start?.dateTime || '?').replace('T', ' ').slice(0, 16);
    const loc = e.location?.displayName ? ` @ ${e.location.displayName}` : '';
    const rem = e.isReminderOn ? `  🔔${e.reminderMinutesBeforeStart}m` : '';
    console.log(`  ${start}  ${e.subject}${loc}${rem}`);
  }
}

async function createEvent(client) {
  if (!args.subject || !args.start || !args.end) {
    throw new Error('--create requires --subject, --start, --end (ISO local, e.g. 2026-06-20T15:00:00)');
  }
  const event = {
    subject: args.subject,
    start: { dateTime: args.start, timeZone: TZ },
    end: { dateTime: args.end, timeZone: TZ },
    isReminderOn: false,
  };
  if (args.location) event.location = { displayName: args.location };
  if (args.body) event.body = { contentType: 'text', content: args.body };
  if (args.attendees) {
    event.attendees = String(args.attendees).split(',').map(a => ({
      emailAddress: { address: a.trim() }, type: 'required',
    }));
  }
  if (args.reminder !== undefined && args.reminder !== 'off') {
    event.isReminderOn = true;
    event.reminderMinutesBeforeStart = parseInt(args.reminder, 10) || 0;
  }
  const calendarId = args.calendar ? await resolveCalendarId(client, args.calendar) : null;
  const base = calendarId ? `/me/calendars/${calendarId}/events` : '/me/events';
  const created = await client.api(base).post(event);
  console.log(`Created: "${created.subject}" on ${created.start.dateTime} (${created.id.slice(0, 16)}...)`);
}

const WEEKDAY_CODES = { MO: 'monday', TU: 'tuesday', WE: 'wednesday', TH: 'thursday', FR: 'friday', SA: 'saturday', SU: 'sunday' };

async function createRecurringEvent(client) {
  if (!args.subject || !args.start || !args.end || !args.days || !args['range-start'] || !args['range-end']) {
    throw new Error('--create-recurring requires --subject, --start=HH:MM, --end=HH:MM, --days=MO,TU,..., --range-start=YYYY-MM-DD, --range-end=YYYY-MM-DD');
  }
  const daysOfWeek = String(args.days).split(',').map(d => WEEKDAY_CODES[d.trim().toUpperCase()]);
  if (daysOfWeek.some(d => !d)) {
    throw new Error(`--days must be a comma list from ${Object.keys(WEEKDAY_CODES).join(',')}`);
  }
  const rangeStart = args['range-start'];
  const event = {
    subject: args.subject,
    start: { dateTime: `${rangeStart}T${args.start}:00`, timeZone: TZ },
    end: { dateTime: `${rangeStart}T${args.end}:00`, timeZone: TZ },
    isReminderOn: false,
    recurrence: {
      pattern: { type: 'weekly', interval: 1, daysOfWeek, firstDayOfWeek: 'sunday' },
      range: { type: 'endDate', startDate: rangeStart, endDate: args['range-end'], recurrenceTimeZone: TZ },
    },
  };
  if (args.location) event.location = { displayName: args.location };
  if (args.reminder !== undefined && args.reminder !== 'off') {
    event.isReminderOn = true;
    event.reminderMinutesBeforeStart = parseInt(args.reminder, 10) || 0;
  }
  const calendarId = args.calendar ? await resolveCalendarId(client, args.calendar) : null;
  const base = calendarId ? `/me/calendars/${calendarId}/events` : '/me/events';
  const created = await client.api(base).post(event);
  console.log(`Created recurring: "${created.subject}" ${args.days} ${args.start}-${args.end} ${rangeStart}..${args['range-end']} (${created.id.slice(0, 16)}...)`);
}

// Resolves which series (of possibly several sharing a subject - an old expired series left in
// place, a duplicate created by mistake) actually has an occurrence on `date`. Checking every
// candidate's /instances rather than taking the first match is what makes --delete-occurrence and
// --reschedule-occurrence safe to point at a subject without knowing in advance which series (if
// any) is the live one.
async function resolveOccurrenceForDate(client, subject, date, calendarId) {
  const escaped = String(subject).replace(/'/g, "''");
  const base = calendarId ? `/me/calendars/${calendarId}/events` : '/me/events';
  const found = await client.api(base)
    .filter(`subject eq '${escaped}' and type eq 'seriesMaster'`)
    .select('id,subject').top(10).get();
  const candidates = found.value || [];
  for (const master of candidates) {
    const instances = await client.api(`/me/events/${master.id}/instances`)
      .query({ startDateTime: `${date}T00:00:00`, endDateTime: `${date}T23:59:59` })
      .header('Prefer', `outlook.timezone="${TZ}"`)
      .select('id,subject,start,end')
      .get();
    const occurrences = instances.value || [];
    if (occurrences.length) return { master, occurrences, candidateCount: candidates.length };
  }
  return { master: null, occurrences: [], candidateCount: candidates.length };
}

// Deletes a single occurrence of a recurring series (e.g. a day the athlete has no practice)
// without touching the rest of the series. Graph requires resolving the series master first,
// then asking it for the specific day's occurrence id via /instances - a calendarView-expanded
// occurrence id is not itself deletable.
async function deleteOccurrence(client) {
  if (!args.subject || !args.date) {
    throw new Error('--delete-occurrence requires --subject and --date=YYYY-MM-DD [--calendar=<name>]');
  }
  const calendarId = args.calendar ? await resolveCalendarId(client, args.calendar) : null;
  const { master, occurrences, candidateCount } = await resolveOccurrenceForDate(client, args.subject, args.date, calendarId);
  if (!candidateCount) {
    throw new Error(`No recurring series found with subject "${args.subject}"${args.calendar ? ` on calendar "${args.calendar}"` : ''} (a non-recurring event with that exact subject doesn't count)`);
  }
  if (!master) {
    console.log(`No occurrence of "${args.subject}" on ${args.date} across ${candidateCount} matching series (already removed, or none scheduled that day).`);
    return;
  }
  for (const occ of occurrences) {
    await client.api(`/me/events/${occ.id}`).delete();
    console.log(`Deleted occurrence of "${master.subject}" on ${args.date}`);
  }
}

// Moves a single occurrence of a recurring series to a different time on the same day, without
// touching the rest of the series - e.g. an existing "drive to school" reminder becomes that day's
// earlier "drive to practice" time instead of leaving both a stale normal-time reminder and a
// separately-created event competing for the same drive.
async function rescheduleOccurrence(client) {
  if (!args.subject || !args.date || !args.start || !args.end) {
    throw new Error('--reschedule-occurrence requires --subject, --date=YYYY-MM-DD, --start=HH:MM, --end=HH:MM [--calendar=<name>]');
  }
  const calendarId = args.calendar ? await resolveCalendarId(client, args.calendar) : null;
  const { master, occurrences, candidateCount } = await resolveOccurrenceForDate(client, args.subject, args.date, calendarId);
  if (!candidateCount) {
    throw new Error(`No recurring series found with subject "${args.subject}"${args.calendar ? ` on calendar "${args.calendar}"` : ''} (a non-recurring event with that exact subject doesn't count)`);
  }
  if (!master) {
    throw new Error(`No occurrence of "${args.subject}" on ${args.date} across ${candidateCount} matching series to reschedule`);
  }
  for (const occ of occurrences) {
    await client.api(`/me/events/${occ.id}`).patch({
      start: { dateTime: `${args.date}T${args.start}:00`, timeZone: TZ },
      end: { dateTime: `${args.date}T${args.end}:00`, timeZone: TZ },
    });
    console.log(`Rescheduled "${master.subject}" on ${args.date} to ${args.start}-${args.end}`);
  }
}

// Updates one occurrence's subject and/or body without touching its time - e.g. tagging a
// recurring "Youth Activities" placeholder with that week's actual activity once it's known,
// without creating a competing one-off event or renaming the whole series.
async function updateOccurrence(client) {
  if (!args.subject || !args.date || (!args['new-subject'] && !args.body)) {
    throw new Error('--update-occurrence requires --subject, --date=YYYY-MM-DD, and --new-subject and/or --body [--calendar=<name>]');
  }
  const calendarId = args.calendar ? await resolveCalendarId(client, args.calendar) : null;
  const { master, occurrences, candidateCount } = await resolveOccurrenceForDate(client, args.subject, args.date, calendarId);
  if (!candidateCount) {
    throw new Error(`No recurring series found with subject "${args.subject}"${args.calendar ? ` on calendar "${args.calendar}"` : ''} (a non-recurring event with that exact subject doesn't count)`);
  }
  if (!master) {
    throw new Error(`No occurrence of "${args.subject}" on ${args.date} across ${candidateCount} matching series to update`);
  }
  const patch = {};
  if (args['new-subject']) patch.subject = args['new-subject'];
  if (args.body) patch.body = { contentType: 'text', content: args.body };
  for (const occ of occurrences) {
    await client.api(`/me/events/${occ.id}`).patch(patch);
    console.log(`Updated occurrence of "${master.subject}" on ${args.date}${args['new-subject'] ? ` -> "${args['new-subject']}"` : ''}`);
  }
}

// Deletes a plain (non-recurring) event by subject + date - e.g. a one-off event created in
// error, or one being replaced by a differently-timed one-off.
async function deleteEvent(client) {
  if (!args.subject || !args.date) {
    throw new Error('--delete-event requires --subject and --date=YYYY-MM-DD');
  }
  const data = await client.api('/me/calendarView')
    .query({ startDateTime: `${args.date}T00:00:00`, endDateTime: `${args.date}T23:59:59` })
    .header('Prefer', `outlook.timezone="${TZ}"`)
    .select('id,subject,start')
    .get();
  const matches = (data.value || []).filter(e => e.subject === args.subject);
  if (!matches.length) {
    console.log(`No event "${args.subject}" on ${args.date} (already removed).`);
    return;
  }
  for (const m of matches) {
    await client.api(`/me/events/${m.id}`).delete();
    console.log(`Deleted "${m.subject}" on ${args.date}`);
  }
}

// --date narrows the search to one day - needed when the subject isn't unique across the year
// (e.g. several one-off events sharing a subject on different dates). Without --date, keeps the
// original behavior: first upcoming match within the next 365 days.
async function updateReminder(client) {
  if (!args.subject || args.reminder === undefined) {
    throw new Error('--update requires --subject and --reminder=N|off [--date=YYYY-MM-DD]');
  }
  let data;
  if (args.date) {
    data = await client.api('/me/calendarView')
      .query({ startDateTime: `${args.date}T00:00:00`, endDateTime: `${args.date}T23:59:59` })
      .header('Prefer', `outlook.timezone="${TZ}"`)
      .select('subject,id,start')
      .get();
  } else {
    const now = new Date();
    const end = new Date(now.getTime() + 365 * 86400000);
    data = await client.api('/me/calendarView')
      .query({ startDateTime: now.toISOString(), endDateTime: end.toISOString() })
      .top(100).orderby('start/dateTime').select('subject,id,start').get();
  }
  const matches = (data.value || []).filter(e => e.subject === args.subject);
  if (!matches.length) {
    throw new Error(`No ${args.date ? `event on ${args.date}` : 'upcoming event'} with subject "${args.subject}"`);
  }
  const patch = args.reminder === 'off'
    ? { isReminderOn: false }
    : { isReminderOn: true, reminderMinutesBeforeStart: parseInt(args.reminder, 10) || 0 };
  for (const match of matches) {
    await client.api(`/me/events/${match.id}`).patch(patch);
    console.log(`Updated reminder on "${match.subject}"${args.date ? ` (${args.date})` : ''} -> ${args.reminder}`);
  }
}

// Creates a calendar if none by that name exists yet (idempotent — safe to call every time a
// script wants to make sure e.g. "Physical Tasks" is there).
async function createCalendar(client) {
  if (!args['create-calendar']) throw new Error('--create-calendar requires a name');
  const name = args['create-calendar'];
  const existing = (await getCalendars(client)).find(c => c.name.toLowerCase() === name.toLowerCase());
  if (existing) { console.log(`Calendar "${name}" already exists (${existing.id.slice(0, 16)}...)`); return; }
  const created = await client.api('/me/calendars').post({ name });
  console.log(`Created calendar "${created.name}" (${created.id.slice(0, 16)}...)`);
}

// Deletes one event by its raw Graph id — a one-off event's own id, or a single recurring
// occurrence's instance id (from calendarView/--list-due-tasks). Either way this removes just
// that one event/occurrence; a recurring series' other occurrences are untouched.
async function deleteEventById(client) {
  if (!args['delete-event-id']) throw new Error('--delete-event-id requires an event id');
  await client.api(`/me/events/${args['delete-event-id']}`).delete();
  console.log(`Deleted event ${args['delete-event-id']}`);
}

// Moves one event to a different day, keeping the same time-of-day and duration — the "not today,
// try again later" move for a due task that didn't get done. Works on a one-off id or a single
// occurrence's instance id (Graph detaches the occurrence from its series, same as dragging it in
// the Outlook UI).
async function moveEventById(client) {
  if (!args['move-event-id'] || !args.date) {
    throw new Error('--move-event-id requires an event id and --date=YYYY-MM-DD');
  }
  const id = args['move-event-id'];
  const current = await client.api(`/me/events/${id}`)
    .header('Prefer', `outlook.timezone="${TZ}"`).select('start,end').get();
  const oldStart = current.start.dateTime, oldEnd = current.end.dateTime;
  const timePart = oldStart.slice(11, 19);
  const durMs = new Date(oldEnd.replace(' ', 'T')) - new Date(oldStart.replace(' ', 'T'));
  const newStart = `${args.date}T${timePart}`;
  const newEnd = new Date(new Date(`${newStart}`).getTime() + durMs);
  const pad = n => String(n).padStart(2, '0');
  const newEndStr = `${newEnd.getFullYear()}-${pad(newEnd.getMonth() + 1)}-${pad(newEnd.getDate())}T`
    + `${pad(newEnd.getHours())}:${pad(newEnd.getMinutes())}:${pad(newEnd.getSeconds())}`;
  await client.api(`/me/events/${id}`).patch({
    start: { dateTime: newStart, timeZone: TZ }, end: { dateTime: newEndStr, timeZone: TZ },
  });
  console.log(`Moved event ${id} -> ${args.date} ${timePart.slice(0, 5)}`);
}

// --- Reusable library functions (param-driven; each builds its own client) ---

// All calendars on the account, as [{ id, name }].
async function getCalendars(client) {
  client = client || await getGraphClient();
  const data = await client.api('/me/calendars').select('id,name').top(100).get();
  return (data.value || []).map(c => ({ id: c.id, name: c.name }));
}

// Resolve a calendar name (case-insensitive) or id to its id.
async function resolveCalendarId(client, nameOrId) {
  const cals = await getCalendars(client);
  const byName = cals.find(c => c.name.toLowerCase() === String(nameOrId).toLowerCase());
  if (byName) return byName.id;
  if (cals.find(c => c.id === nameOrId)) return nameOrId;
  throw new Error(`Calendar not found: "${nameOrId}". Available: ${cals.map(c => c.name).join(', ')}`);
}

// Events on a NAMED calendar over an explicit date range, as
// [{ date, subject, minutes, start, end, allDay }]. calendarView expands recurrences;
// --end is inclusive of the whole day. Paginates through @odata.nextLink.
async function getEvents({ calendar, start, end, tz = 'America/Chicago', client } = {}) {
  if (!calendar || !start || !end) throw new Error('getEvents requires { calendar, start, end }');
  client = client || await getGraphClient();
  const calId = await resolveCalendarId(client, calendar);
  const startISO = new Date(`${start}T00:00:00`).toISOString();
  const endISO = new Date(`${end}T23:59:59`).toISOString();
  const out = [];
  let page = await client.api(`/me/calendars/${calId}/calendarView`)
    .query({ startDateTime: startISO, endDateTime: endISO })
    .header('Prefer', `outlook.timezone="${tz}"`)
    .top(200).orderby('start/dateTime')
    .select('subject,start,end,isAllDay')
    .get();
  while (page) {
    for (const e of page.value || []) {
      const minutes = Math.round((new Date(e.end.dateTime) - new Date(e.start.dateTime)) / 60000);
      out.push({
        date: (e.start.dateTime || '').slice(0, 10),
        subject: e.subject || '(no title)',
        minutes: e.isAllDay ? 0 : minutes,
        start: e.start.dateTime,
        end: e.end.dateTime,
        allDay: !!e.isAllDay,
      });
    }
    const next = page['@odata.nextLink'];
    page = next ? await client.api(next).get() : null;
  }
  return out;
}

// Every non-all-day event on a dedicated task calendar (e.g. "Physical Tasks") that is still
// QUEUED — start date today-or-earlier AND its start time sits EXACTLY on one of the overnight
// parking grid's slots (`isQueuedSlot` below), not just somewhere in the 00:00-02:59 hour range. A
// task's own placement on the calendar IS its due date; nothing here ever moves it while it's still
// queued, so an undone one just keeps coming back until something explicitly takes it out of the
// window (see startTaskNow/finishTaskNow below — moving an event's start out of grid alignment IS
// what "cleared"/"started" means here, mirroring the pre-drainer habit of dragging a to-do out of its
// staging hour once you actually picked it up).
//
// The exact-slot requirement (not just "any time in that hour") matters because startTaskNow sets
// start to the real wall-clock now — if Russell is up working past midnight, "now" can itself land
// inside 00:00-02:59, and a same-hour-only check would wrongly still read a just-started task as
// queued. A real "now" essentially never lands exactly on a grid slot (down to zero seconds), so
// exact alignment is what actually distinguishes "still sitting where it was placed" from "moved."
// Every event on this calendar counts as a task (it's a dedicated calendar — no organizer/attendee
// heuristic needed, unlike a real commitment calendar).
//
// A recurring series can rack up MANY queued-but-undone occurrences (Graph expands every date the
// pattern ever produced between its start and today, not just "the next one") — one per missed
// day/week/month, not one per series. Those collapse to a SINGLE row per series here: `id` is the
// MOST RECENT queued occurrence's own instance id (what startTaskNow/finishTaskNow act on directly),
// with `seriesMasterId` alongside it so the older, uninteresting backlog occurrences can be swept
// separately (see catchUpSeries) without touching the one actually being worked.
//
// Returns [{ id, seriesMasterId (recurring only), subject, date, minutes, isRecurring, webLink }],
// oldest-due first.
// The old staging grid, kept verbatim: :00 only at midnight, quarter-hours at 1 AM, half-hours at
// 2 AM. Nothing here still ties a slot to a task's SIZE (duration is just the event's own length
// now) — the grid is purely a "still sitting untouched" position check.
const QUEUED_SLOTS = { 0: [0], 1: [0, 15, 30, 45], 2: [0, 30] };
function isQueuedSlot(dateTimeStr) {
  const s = dateTimeStr || '';
  const h = parseInt(s.slice(11, 13), 10);
  const m = parseInt(s.slice(14, 16), 10);
  const sec = parseInt(s.slice(17, 19), 10) || 0;
  const slots = QUEUED_SLOTS[h];
  return !!slots && slots.includes(m) && sec === 0;
}
async function listDueTasks({ calendar, tz = 'America/Chicago', lookbackDays = 30, today, client } = {}) {
  if (!calendar) throw new Error('listDueTasks requires { calendar }');
  client = client || await getGraphClient();
  const calId = await resolveCalendarId(client, calendar);
  const todayStr = today || new Date().toLocaleDateString('en-CA', { timeZone: tz });
  const rangeStart = new Date(new Date(`${todayStr}T00:00:00`).getTime() - lookbackDays * 86400000);
  const pad = n => String(n).padStart(2, '0');
  const rangeStartStr = `${rangeStart.getFullYear()}-${pad(rangeStart.getMonth() + 1)}-${pad(rangeStart.getDate())}`;
  const oneOffs = [];
  const bySeries = new Map(); // seriesMasterId -> { id (latest occurrence), subject, date (latest), minutes, webLink }
  let page = await client.api(`/me/calendars/${calId}/calendarView`)
    .query({ startDateTime: `${rangeStartStr}T00:00:00`, endDateTime: `${todayStr}T23:59:59` })
    .header('Prefer', `outlook.timezone="${tz}"`)
    .top(200).orderby('start/dateTime')
    .select('subject,start,end,isAllDay,type,seriesMasterId,webLink,id')
    .get();
  while (page) {
    for (const e of page.value || []) {
      if (e.isAllDay || !isQueuedSlot(e.start.dateTime)) continue;
      const date = (e.start.dateTime || '').slice(0, 10);
      if (date > todayStr) continue; // calendarView can hand back a same-day-boundary next occurrence
      const minutes = Math.round((new Date(e.end.dateTime) - new Date(e.start.dateTime)) / 60000);
      if (e.type === 'occurrence' || e.type === 'exception') {
        const existing = bySeries.get(e.seriesMasterId);
        if (!existing || date > existing.date) {
          bySeries.set(e.seriesMasterId, {
            id: e.id, subject: e.subject || '(no title)', date, minutes, webLink: e.webLink,
          });
        }
      } else {
        oneOffs.push({
          id: e.id, subject: e.subject || '(no title)', date, minutes,
          isRecurring: false, webLink: e.webLink,
        });
      }
    }
    const next = page['@odata.nextLink'];
    page = next ? await client.api(next).get() : null;
  }
  const recurring = [...bySeries.entries()].map(([seriesMasterId, v]) => ({
    id: v.id, seriesMasterId, subject: v.subject, date: v.date, minutes: v.minutes,
    isRecurring: true, webLink: v.webLink,
  }));
  const out = [...oneOffs, ...recurring];
  out.sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

// The "started" move: pulls one event out of the queued window by setting its start to the moment the
// work actually surfaced — by default right now, or an explicit `at` time when the caller has a truer
// one (the physical-task worker passes the instant its terminal tab launched, so a task glanced at and
// done in one sitting still records the real span from when it appeared, not the zero-length span
// between the "starting" and "done" replies typed a moment apart). Keeps the event's original duration
// (so end = start + that duration). A started task's real timestamp carries seconds and essentially
// never lands exactly on a grid slot (see isQueuedSlot), so it stops matching immediately and stops
// being due, permanently, with no delete and no second calendar. A recurring occurrence detaches from
// its series here (same as the Outlook UI), which is correct — only this one instance was started, the
// series' future occurrences are untouched. Returns the duration (minutes) preserved, for the caller
// to sanity-check.
async function startTaskNow({ eventId, at, tz = 'America/Chicago', client } = {}) {
  if (!eventId) throw new Error('startTaskNow requires { eventId }');
  client = client || await getGraphClient();
  const current = await client.api(`/me/events/${eventId}`)
    .header('Prefer', `outlook.timezone="${tz}"`).select('start,end').get();
  const durMs = new Date(current.end.dateTime.replace(' ', 'T')) - new Date(current.start.dateTime.replace(' ', 'T'));
  const start = at ? new Date(at) : new Date();
  if (isNaN(start)) throw new Error(`startTaskNow: unparseable start time ${JSON.stringify(at)}`);
  const pad = n => String(n).padStart(2, '0');
  const startStr = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}:${pad(start.getSeconds())}`;
  const end = new Date(start.getTime() + durMs);
  const endStr = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}T${pad(end.getHours())}:${pad(end.getMinutes())}:${pad(end.getSeconds())}`;
  await client.api(`/me/events/${eventId}`).patch({
    start: { dateTime: startStr, timeZone: tz }, end: { dateTime: endStr, timeZone: tz },
  });
  return Math.round(durMs / 60000);
}

// The "finished" stamp: patches ONLY the end time to right now, leaving start exactly where
// startTaskNow put it — so the event's real elapsed span (start = when picked up, end = when
// actually finished) sits on the calendar afterward, the same record Russell used to leave by hand.
async function finishTaskNow({ eventId, tz = 'America/Chicago', client } = {}) {
  if (!eventId) throw new Error('finishTaskNow requires { eventId }');
  client = client || await getGraphClient();
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const nowStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  await client.api(`/me/events/${eventId}`).patch({ end: { dateTime: nowStr, timeZone: tz } });
}

// Deletes every QUEUED occurrence of a recurring series that is due-or-earlier (date <= today)
// EXCEPT `exceptId` — the backlog cleanup for a series that racked up several missed occurrences
// while nobody cleared it. Those older occurrences are just Graph's recurrence-expansion noise, not
// independently meaningful (nobody actually worked "Call Mom" on every missed day), so they're
// removed outright; `exceptId` is the one occurrence startTaskNow/finishTaskNow already turned into
// a real record and must be left alone. NEVER deletes the series master itself (that would kill
// every future occurrence too) — only the individual overdue instances.
async function catchUpSeries({ seriesMasterId, exceptId, tz = 'America/Chicago', today, client } = {}) {
  if (!seriesMasterId) throw new Error('catchUpSeries requires { seriesMasterId }');
  client = client || await getGraphClient();
  const todayStr = today || new Date().toLocaleDateString('en-CA', { timeZone: tz });
  const master = await client.api(`/me/events/${seriesMasterId}`)
    .select('recurrence').get();
  const rangeStart = master.recurrence?.range?.startDate || '2000-01-01';
  const instances = await client.api(`/me/events/${seriesMasterId}/instances`)
    .query({ startDateTime: `${rangeStart}T00:00:00`, endDateTime: `${todayStr}T23:59:59` })
    .header('Prefer', `outlook.timezone="${tz}"`)
    .select('id,start').top(500).get();
  let deleted = 0;
  for (const occ of instances.value || []) {
    if (occ.id === exceptId) continue;
    if ((occ.start.dateTime || '').slice(0, 10) > todayStr) continue;
    await client.api(`/me/events/${occ.id}`).delete();
    deleted++;
  }
  return deleted;
}

// Minutes from now until the next REAL commitment across every writable calendar — a non-all-day
// event with an attendee other than Russell, or one he didn't organize. Excludes solo self-owned
// events (task placeholders, on the Physical Tasks calendar or anywhere else) and all-day events,
// since neither blocks him from starting something. Returns 0 when a real commitment is in
// progress right now; caps at `lookaheadHours` (default 48) when nothing real is found that soon —
// there's no practical ceiling on "how much free time," so the cap just bounds the query.
async function getGapUntilNextCommitment({ tz = 'America/Chicago', lookaheadHours = 2, exclude = [], client } = {}) {
  client = client || await getGraphClient();
  const ME = 'russell.rutledge@outlook.com';
  const now = new Date();
  const end = new Date(now.getTime() + lookaheadHours * 3600000);
  const excludeSet = new Set(exclude.map(s => s.trim().toLowerCase()));
  const cals = (await client.api('/me/calendars').select('id,name,canEdit').top(100).get()).value || [];
  const targets = cals.filter(c => c.canEdit && !excludeSet.has(c.name.toLowerCase()));
  const isSoloTask = e => {
    if (e.isAllDay || !e.isOrganizer) return false;
    return (e.attendees || []).every(a => (a.emailAddress?.address || '').toLowerCase() === ME);
  };
  let earliest = null;
  for (const cal of targets) {
    let page = await client.api(`/me/calendars/${cal.id}/calendarView`)
      .query({ startDateTime: now.toISOString(), endDateTime: end.toISOString() })
      .header('Prefer', `outlook.timezone="${tz}"`)
      .top(200).orderby('start/dateTime')
      .select('start,end,isAllDay,isOrganizer,attendees')
      .get();
    while (page) {
      for (const e of page.value || []) {
        if (isSoloTask(e)) continue;
        const start = new Date(e.start.dateTime);
        const finish = new Date(e.end.dateTime);
        if (finish <= now) continue; // already over
        if (start <= now) return 0;  // in progress right now
        if (!earliest || start < earliest) earliest = start;
      }
      const next = page['@odata.nextLink'];
      page = next ? await client.api(next).get() : null;
    }
  }
  if (!earliest) return lookaheadHours * 60;
  return Math.max(0, Math.round((earliest - now) / 60000));
}

module.exports = {
  getCalendars, resolveCalendarId, getEvents, listDueTasks, getGapUntilNextCommitment,
  startTaskNow, finishTaskNow, catchUpSeries,
};

// --- CLI (only when run directly, so `require` of this file is side-effect-free) ---
if (require.main === module) {
  (async () => {
    if (args['list-calendars']) {
      for (const c of await getCalendars()) console.log(`${c.name}\t${c.id}`);
      return;
    }
    if (args.list) {
      const events = await getEvents({ calendar: args.calendar, start: args.start, end: args.end, tz: TZ });
      if (args.json) { console.log(JSON.stringify(events, null, 2)); return; }
      console.log(`${events.length} event(s) on "${args.calendar}" ${args.start}..${args.end}:`);
      for (const e of events) console.log(`  ${e.date}  ${String(e.minutes).padStart(4)}m  ${e.subject}`);
      return;
    }
    if (args['list-due-tasks']) {
      if (!args.calendar) throw new Error('--list-due-tasks requires --calendar=<name>');
      const lookbackDays = args['lookback-days'] ? parseInt(args['lookback-days'], 10) : 30;
      const tasks = await listDueTasks({ calendar: args.calendar, tz: TZ, lookbackDays });
      if (args.json) { console.log(JSON.stringify(tasks, null, 2)); return; }
      if (!tasks.length) { console.log(`No due tasks on "${args.calendar}".`); return; }
      console.log(`${tasks.length} due task(s) on "${args.calendar}":`);
      for (const t of tasks) console.log(`  ${t.date}  ${String(t.minutes).padStart(4)}m  ${t.subject}  (${t.id.slice(0, 16)}...)`);
      return;
    }
    if (args['gap-minutes']) {
      const exclude = args.exclude ? String(args.exclude).split(',') : [];
      const lookaheadHours = args['lookahead-hours'] ? parseInt(args['lookahead-hours'], 10) : 2;
      const minutes = await getGapUntilNextCommitment({ tz: TZ, lookaheadHours, exclude });
      if (args.json) { console.log(JSON.stringify({ minutes })); return; }
      console.log(`${minutes} minute(s) until the next real commitment.`);
      return;
    }
    const client = await getGraphClient();
    if (args['create-calendar']) return createCalendar(client);
    if (args['delete-event-id']) return deleteEventById(client);
    if (args['move-event-id']) return moveEventById(client);
    if (args['catch-up-series']) {
      const deleted = await catchUpSeries({
        seriesMasterId: args['catch-up-series'], exceptId: args['except-id'], tz: TZ,
      });
      console.log(`Caught up: deleted ${deleted} overdue occurrence(s) of series ${args['catch-up-series']}${args['except-id'] ? ` (kept ${args['except-id']})` : ''}.`);
      return;
    }
    if (args['start-now']) {
      const at = args.at === true ? undefined : args.at;
      const minutes = await startTaskNow({ eventId: args['start-now'], at, tz: TZ });
      console.log(`Started "${args['start-now']}" at ${at || 'now'}, keeping its ${minutes}m duration.`);
      return;
    }
    if (args['finish-now']) {
      await finishTaskNow({ eventId: args['finish-now'], tz: TZ });
      console.log(`Finished "${args['finish-now']}" — end stamped to now.`);
      return;
    }
    if (args['create-recurring']) return createRecurringEvent(client);
    if (args['delete-occurrence']) return deleteOccurrence(client);
    if (args['reschedule-occurrence']) return rescheduleOccurrence(client);
    if (args['update-occurrence']) return updateOccurrence(client);
    if (args['delete-event']) return deleteEvent(client);
    if (args.create) return createEvent(client);
    if (args.update) return updateReminder(client);
    return listUpcoming(client);
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
