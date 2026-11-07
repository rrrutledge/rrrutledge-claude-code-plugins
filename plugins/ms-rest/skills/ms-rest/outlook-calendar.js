#!/usr/bin/env node
// outlook-calendar.js - Outlook calendar operations: read, create, move, retime, delete.
//
// Owned by the `ms-rest` skill. Calls the Outlook REST API v2.0 (https://outlook.office.com/api/v2.0)
// directly, reusing the shared token + apiCall plumbing in outlook-core.js (the sniffed bearer token's
// scopes include Calendars.ReadWrite). Every calendar call sends
// `Prefer: outlook.timezone="America/Chicago"` so Start/End come back in wall-clock, not UTC.
//
// Two ways to use it:
//   1. As a library - `require` it and call the exported functions.
//   2. As a CLI - the verbs below.
//
// Usage:
//   node outlook-calendar.js calendar-view --start <iso> --end <iso>   -> JSON array of events
//   node outlook-calendar.js calendar-getschedule --schedules a,b --start <iso> --end <iso> [--interval 30]
//                                                                      -> per-attendee free/busy
//   node outlook-calendar.js event-get <id>                            -> full event JSON
//   node outlook-calendar.js event-create --json <path>                -> new event; prints {id, webLink}
//                                                  (set "isDraft": true in the JSON to stage without sending)
//   node outlook-calendar.js event-move <id> --date <YYYY-MM-DD>       -> change date, keep time-of-day
//   node outlook-calendar.js event-set-time <id> --start <iso> --end <iso>  -> reschedule to new times
//   node outlook-calendar.js event-delete <id>                         -> delete (204/404 = ok)
//   node outlook-calendar.js token                                     -> print token status (no API call)
//
// `event-move` reports {status: "moved" | "boundary-blocked"}; boundary-blocked is the Outlook rule
// that forbids relocating a recurring occurrence across its own neighbors
// (400 ErrorOccurrenceCrossingBoundary), surfaced so a caller can decide whether to leave it in place.
// JSON goes to stdout; errors go to stderr with a non-zero exit.

const fs = require('fs');
const { apiCall, enc, getToken } = require('./outlook-core');

const TZ = 'America/Chicago';
const PREFER = { Prefer: `outlook.timezone="${TZ}"` };
const PAGE_SIZE = 200;
// A sensible default set of commonly-needed fields (PascalCase in v2.0). Callers that need more or
// fewer can pass their own $select string.
const DEFAULT_SELECT = 'Id,Subject,Start,End,IsAllDay,Type,SeriesMasterId,IsCancelled,Attendees,IsOnlineMeeting,Organizer,Location';

// ---- date/time helpers (wall-clock in TZ; offsets cancel under subtraction) --
function naiveMs(dt) { return Date.parse(dt.replace(/\.\d+$/, '').slice(0, 19) + 'Z'); }
function timeOf(dt) { return dt.slice(11, 19); }
function naiveFromMs(ms) { return new Date(ms).toISOString().slice(0, 19); }

function toAttendee(a) {
  if (typeof a === 'string') return { EmailAddress: { Address: a }, Type: 'Required' };
  return {
    EmailAddress: { Address: a.address || a.Address, Name: a.name || a.Name },
    Type: a.type || a.Type || 'Required',
  };
}

// ---- library functions ------------------------------------------------------
// Paged calendarView over [startIso, endIso). Re-sends the timezone Prefer header on EVERY page -
// otherwise pages 2+ come back in UTC, silently shifting times. calendarView expands recurring
// series into dated instances. `select` overrides the default field set.
async function calendarView(startIso, endIso, select) {
  const sel = select || DEFAULT_SELECT;
  const out = [];
  let urlPath = `/me/calendarview?startDateTime=${enc(startIso)}&endDateTime=${enc(endIso)}`
    + `&$select=${sel}&$orderby=${enc('Start/DateTime')}&$top=${PAGE_SIZE}`;
  while (urlPath) {
    const res = await apiCall('GET', urlPath, undefined, PREFER);
    if (res.status !== 200) throw new Error(`calendar-view HTTP ${res.status}: ${res.body.slice(0, 400)}`);
    const json = JSON.parse(res.body);
    out.push(...(json.value || []));
    urlPath = json['@odata.nextLink'] || null;
  }
  return out;
}

// Free/busy for a set of mailboxes via POST /me/calendar/getschedule. Works for self AND other
// tenant attendees. `schedules` is an array of SMTP addresses; start/end are naive wall-clock strings
// interpreted in TZ; interval is the AvailabilityView granularity in minutes (default 30).
// Returns the raw per-schedule array; each entry has:
//   ScheduleId - the mailbox address
//   AvailabilityView - per-interval string ('0'=Free, '1'=Tentative, '2'=Busy, '3'=OOF, '4'=WorkingElsewhere)
//   ScheduleItems[] - {Status, Start, End, Subject?} (subjects hidden for other mailboxes - fine)
//   WorkingHours - {StartTime, EndTime, TimeZone:{Name}, DaysOfWeek[]}
//   Error - present (with ResponseCode/Message) when the mailbox can't be resolved
// An unresolved address comes back with an Error entry - callers use that to validate addresses.
async function getSchedule(schedules, startNaive, endNaive, interval) {
  const body = {
    Schedules: schedules,
    StartTime: { DateTime: startNaive, TimeZone: TZ },
    EndTime: { DateTime: endNaive, TimeZone: TZ },
    AvailabilityViewInterval: interval || 30,
  };
  const res = await apiCall('POST', '/me/calendar/getschedule', body, PREFER);
  if (res.status !== 200) throw new Error(`getschedule HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  return JSON.parse(res.body).value || [];
}

// Read a single event by id (also reads a recurring series master via its SeriesMasterId).
async function eventGet(id, select) {
  const sel = select ? `?$select=${select}` : '';
  const res = await apiCall('GET', `/me/events/${enc(id)}${sel}`, undefined, PREFER);
  if (res.status !== 200) throw new Error(`event-get HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  return JSON.parse(res.body);
}

// Create a calendar event. `spec` is a friendly object; missing fields fall back to Outlook defaults.
//   { subject, start, end, timeZone?, isAllDay?, body?, bodyType?, location?,
//     attendees?: [<address> | {address, name, type}], isOnlineMeeting?, reminderMinutesBeforeStart?,
//     categories?, isDraft? }
// start/end are naive wall-clock strings ("2026-06-18T12:00:00") interpreted in timeZone (default TZ).
async function eventCreate(spec) {
  if (!spec || !spec.subject) throw new Error('event-create needs a "subject"');
  if (!spec.start || !spec.end) throw new Error('event-create needs "start" and "end"');
  const tz = spec.timeZone || TZ;
  const ev = {
    Subject: spec.subject,
    Start: { DateTime: spec.start, TimeZone: tz },
    End: { DateTime: spec.end, TimeZone: tz },
  };
  if (spec.isAllDay !== undefined) ev.IsAllDay = !!spec.isAllDay;
  if (spec.body !== undefined) ev.Body = { ContentType: spec.bodyType || 'HTML', Content: spec.body };
  if (spec.location !== undefined) ev.Location = { DisplayName: spec.location };
  if (spec.attendees) ev.Attendees = spec.attendees.map(toAttendee);
  if (spec.isOnlineMeeting) { ev.IsOnlineMeeting = true; ev.OnlineMeetingProvider = 'teamsForBusiness'; }
  if (spec.reminderMinutesBeforeStart !== undefined) ev.ReminderMinutesBeforeStart = spec.reminderMinutesBeforeStart;
  if (spec.categories) ev.Categories = spec.categories;
  // Draft-only staging: IsDraft:true lands the meeting on the calendar but dispatches NO invitation.
  // Opened in Outlook web it shows the full meeting editor with the primary button = "Send", so a human
  // reviews then sends. Teams nuance: with isOnlineMeeting set on a DRAFT, the API read-back reports
  // IsOnlineMeeting:false / no join URL - expected, not a bug; the OWA editor still shows the Teams toggle
  // ON and provisions the join link at Send. Don't "fix" the false read-back.
  if (spec.isDraft) ev.IsDraft = true;
  const res = await apiCall('POST', '/me/events', ev, PREFER);
  if (res.status !== 201 && res.status !== 200) throw new Error(`event-create HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  const m = JSON.parse(res.body);
  return { id: m.Id, webLink: m.WebLink, subject: m.Subject, start: (m.Start || {}).DateTime };
}

// Move an event to a target date, keeping its time-of-day and duration. For a recurring occurrence
// Outlook returns 400 ErrorOccurrenceCrossingBoundary if the move would cross a neighbor instance -
// reported as status "boundary-blocked" so the caller can leave it in place.
async function eventMove(id, dateYYYYMMDD) {
  const ev = await eventGet(id, 'Id,Start,End');
  const durMs = naiveMs(ev.End.DateTime) - naiveMs(ev.Start.DateTime);
  const newStart = dateYYYYMMDD + 'T' + timeOf(ev.Start.DateTime);
  const newEnd = naiveFromMs(naiveMs(newStart) + durMs);
  const res = await apiCall('PATCH', `/me/events/${enc(id)}`, {
    Start: { DateTime: newStart, TimeZone: TZ },
    End: { DateTime: newEnd, TimeZone: TZ },
  }, PREFER);
  if (res.status === 200) return { status: 'moved', id, start: newStart };
  if (res.status === 400 && /CrossingBoundary|Overlap/i.test(res.body)) {
    return { status: 'boundary-blocked', id };
  }
  throw new Error(`event-move HTTP ${res.status}: ${res.body.slice(0, 400)}`);
}

// Reschedule an event to explicit naive wall-clock start/end times (same date or different).
async function eventSetTime(id, startNaive, endNaive) {
  const res = await apiCall('PATCH', `/me/events/${enc(id)}`, {
    Start: { DateTime: startNaive, TimeZone: TZ },
    End: { DateTime: endNaive, TimeZone: TZ },
  }, PREFER);
  if (res.status !== 200) throw new Error(`event-set-time HTTP ${res.status}: ${res.body.slice(0, 400)}`);
  return { status: 'set', id, start: startNaive };
}

// Delete an event. 204 = deleted, 404 = already gone (idempotent re-run safely no-ops).
async function eventDelete(id) {
  const res = await apiCall('DELETE', `/me/events/${enc(id)}`, undefined, PREFER);
  if (res.status === 204 || res.status === 200) return { status: 'deleted', id };
  if (res.status === 404) return { status: 'already-gone', id };
  throw new Error(`event-delete HTTP ${res.status}: ${res.body.slice(0, 400)}`);
}

module.exports = { TZ, calendarView, getSchedule, eventGet, eventCreate, eventMove, eventSetTime, eventDelete };

// ---- CLI --------------------------------------------------------------------
if (require.main === module) {
  const [cmd, ...rest] = process.argv.slice(2);
  const flagVal = (name) => { const i = rest.indexOf(name); return i >= 0 ? rest[i + 1] : undefined; };
  const flagVals = new Set();
  rest.forEach((a, i) => { if (a.startsWith('--')) flagVals.add(rest[i + 1]); });
  const pos = rest.filter(a => !a.startsWith('--') && !flagVals.has(a));
  (async () => {
    try {
      let out;
      switch (cmd) {
        case 'calendar-view':
          out = await calendarView(flagVal('--start'), flagVal('--end')); break;
        case 'calendar-getschedule':
          out = await getSchedule(
            (flagVal('--schedules') || '').split(',').map(s => s.trim()).filter(Boolean),
            flagVal('--start'), flagVal('--end'),
            flagVal('--interval') ? parseInt(flagVal('--interval'), 10) : 30); break;
        case 'token': {
          const meta = await getToken(false);
          out = { status: 'Token OK ✅', expISO: meta.expISO, aud: meta.aud };
          break;
        }
        case 'event-get':
          out = await eventGet(pos[0]); break;
        case 'event-create':
          out = await eventCreate(JSON.parse(fs.readFileSync(flagVal('--json'), 'utf8'))); break;
        case 'event-move':
          out = await eventMove(pos[0], flagVal('--date')); break;
        case 'event-set-time':
          out = await eventSetTime(pos[0], flagVal('--start'), flagVal('--end')); break;
        case 'event-delete':
          out = await eventDelete(pos[0]); break;
        default:
          process.stderr.write('Usage: outlook-calendar.js <calendar-view --start <iso> --end <iso>|'
            + 'calendar-getschedule --schedules a,b --start <iso> --end <iso> [--interval 30]|'
            + 'event-get <id>|event-create --json <p>|event-move <id> --date <YYYY-MM-DD>|'
            + 'event-set-time <id> --start <iso> --end <iso>|event-delete <id>|token>\n');
          process.exit(1);
      }
      process.stdout.write(JSON.stringify(out, null, 2) + '\n');
    } catch (e) {
      process.stderr.write('ERROR: ' + (e && e.message || e) + '\n');
      process.exit(2);
    }
  })();
}
