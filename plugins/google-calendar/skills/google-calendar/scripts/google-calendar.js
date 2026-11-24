// Read a Google Calendar's events over a time range, create an event with no Google Meet link, or remove one
// event's Google Meet link, via the
// official `googleapis` Calendar v3 client (OAuth path - see google-calendar-oauth.js).
//
// List a range:  node google-calendar.js --start=<date or RFC 3339> --end=<date or RFC 3339> [--calendar=<id>] [--json]
//                (--calendar defaults to `primary`. A plain YYYY-MM-DD date is local midnight; a timestamp
//                needs its offset, e.g. 2026-10-01T00:00:00-05:00. --end is
//                exclusive, so --start=2026-10-01 --end=2026-11-01 is all of October. --json prints the raw
//                Event resources; without it, one line per event.)
// Remove Meet:   node google-calendar.js --strip-meet --event=<id> [--calendar=<id>] [--send]
//                (Drops the event's conference when it is Google Meet, leaving every other field as is, and
//                refuses when it is another provider's, such as a Zoom add-on.)
// Create:        node google-calendar.js --create --summary=<title> --start=<YYYY-MM-DDTHH:MM> --end=<...>
//                  --join=<zoom url> [--guests=a@x.com,b@y.com] [--description=<text>] [--tz=<IANA>]
//                  [--reminders=15,10,0] [--calendar=<id>] [--send]
//                (Leaves Google Meet off. Times are local wall clock in --tz, default America/Chicago.
//                Guests get the invite email with --send and are added silently without it.)
//
// Reusable from other scripts (require.main-guarded, so requiring this file does not run the CLI):
//   const { listEvents, stripMeet, createEvent } = require('<google-calendar>/scripts/google-calendar.js');
//   const events = await listEvents({ start: '2026-10-01', end: '2026-11-01' });
//   const patched = await stripMeet({ event: '<id>' });

const { google } = require('googleapis');
const { getAuthedClient, assertAccountEmail } = require('./google-calendar-oauth');

function toRfc3339(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`).toISOString() : value;
}

// Every event on `calendar` that overlaps [start, end), as the raw Google Calendar `Event` resources, with
// every page fetched. Recurring events are expanded into their single instances (singleEvents=true), ordered
// by start time. Cancelled events are left out (Google's default without showDeleted).
async function listEvents({ calendar = 'primary', start, end } = {}) {
  if (!start || !end) throw new Error('listEvents requires { start, end }');
  const auth = getAuthedClient();
  await assertAccountEmail(auth);
  const api = google.calendar({ version: 'v3', auth });
  const out = [];
  let pageToken;
  do {
    const res = await api.events.list({
      calendarId: calendar,
      timeMin: toRfc3339(start),
      timeMax: toRfc3339(end),
      singleEvents: true,
      orderBy: 'startTime',
      maxResults: 2500,
      pageToken,
    });
    out.push(...(res.data.items || []));
    pageToken = res.data.nextPageToken;
  } while (pageToken);
  return out;
}

// Remove the Google Meet conference from one event the signed-in account organizes. Resolves to the patched
// Event resource, or to null when the event has no conference (nothing is written and no guest is emailed).
// Refuses an event whose conference is another provider's, so a Zoom add-on link stays in place.
// conferenceDataVersion=1 is what makes the API apply a conferenceData change at all.
async function stripMeet({ calendar = 'primary', event, send = false } = {}) {
  if (typeof event !== 'string' || !event) throw new Error('stripMeet requires { event: <id> }');
  const auth = getAuthedClient();
  await assertAccountEmail(auth);
  const api = google.calendar({ version: 'v3', auth });
  const current = (await api.events.get({ calendarId: calendar, eventId: event })).data;
  const title = current.summary || '(no title)';
  const conf = current.conferenceData;
  if (!conf && !current.hangoutLink) return null;
  if (!current.organizer?.self) throw new Error(`${title}: organized by someone else - only the organizer can remove Meet`);
  if (!conf) throw new Error(`${title}: legacy Meet link with no conference data - remove it in the Calendar UI`);
  // A Meet link still being provisioned carries only a createRequest, not a conferenceSolution yet.
  const type = conf.conferenceSolution?.key?.type ?? conf.createRequest?.conferenceSolutionKey?.type;
  if (type !== 'hangoutsMeet') throw new Error(`${title}: conference is ${type || 'of unknown type'}, not Google Meet - left unchanged`);
  const res = await api.events.patch({
    calendarId: calendar,
    eventId: event,
    conferenceDataVersion: 1,
    sendUpdates: send ? 'all' : 'none',
    requestBody: { conferenceData: null },
  });
  if (res.data.conferenceData || res.data.hangoutLink) {
    throw new Error(`${title}: the patch was applied, but the response still shows a conference - check the event before retrying`);
  }
  return res.data;
}

const isMeet = ev => !!ev.hangoutLink ||
  (ev.conferenceData?.conferenceSolution?.key?.type ?? ev.conferenceData?.createRequest?.conferenceSolutionKey?.type) === 'hangoutsMeet';

// Create an event whose join link is `join` (Zoom or any other non-Meet URL, set as the location and appended
// to the description), with Google Meet left off. The API attaches a conference only when a call asks for one,
// and this call asks for none. The event is inserted guest-free and checked, so a Meet link from a
// calendar-level auto-add comes off before any guest is added; a non-Meet add-on conference (a Zoom add-on) is
// kept. Guests are added next, emailed when `send` is true, and the event is checked once more; that last check
// runs after the invites go out, so a Meet link it finds is reported for --strip-meet. `start`/`end` are local
// wall-clock times in `timeZone`. `reminders` is a list of minutes-before for popup reminders; left out, the
// calendar's defaults apply. Resolves to the final Event resource.
async function createEvent({
  calendar = 'primary', summary, start, end, join, guests = [], description = '',
  timeZone = 'America/Chicago', reminders, send = false,
} = {}) {
  for (const [name, value] of Object.entries({ summary, start, end, join })) {
    if (typeof value !== 'string' || !value) throw new Error(`createEvent requires a string ${name}`);
  }
  if (!Array.isArray(guests)) throw new Error('createEvent requires guests as an array of email addresses');
  if (reminders !== undefined && !(Array.isArray(reminders) && reminders.every(Number.isInteger))) {
    throw new Error('createEvent requires reminders as an array of whole minutes');
  }
  const withSeconds = t => (/T\d{2}:\d{2}$/.test(t) ? `${t}:00` : t);
  const auth = getAuthedClient();
  await assertAccountEmail(auth);
  const api = google.calendar({ version: 'v3', auth });
  let ev = (await api.events.insert({
    calendarId: calendar,
    sendUpdates: 'none',
    requestBody: {
      summary,
      location: join,
      description: description ? `${description}\n\n${join}` : join,
      start: { dateTime: withSeconds(start), timeZone },
      end: { dateTime: withSeconds(end), timeZone },
      ...(reminders && {
        reminders: { useDefault: false, overrides: reminders.map(minutes => ({ method: 'popup', minutes })) },
      }),
    },
  })).data;
  if (isMeet(ev)) {
    try {
      ev = await stripMeet({ calendar, event: ev.id });
    } catch (e) {
      throw new Error(`event ${ev.id} was created with a Meet link and no guests, and removing it failed: ${e.message}`);
    }
  }
  if (guests.length) {
    ev = (await api.events.patch({
      calendarId: calendar,
      eventId: ev.id,
      sendUpdates: send ? 'all' : 'none',
      requestBody: { attendees: guests.map(email => ({ email })) },
    })).data;
  }
  if (isMeet(ev)) {
    throw new Error(`${summary}: event ${ev.id} gained a Meet link when guests were added - run --strip-meet --event=${ev.id}`);
  }
  return ev;
}

module.exports = { listEvents, stripMeet, createEvent };

if (require.main === module) {
  const args = Object.fromEntries(
    process.argv.slice(2).map(a => {
      const m = a.match(/^--([^=]+)(?:=(.*))?$/);
      return m ? [m[1], m[2] ?? true] : [a, true];
    })
  );
  (async () => {
    if (args.create) {
      const send = args.send === true || args.send === 'true';
      const ev = await createEvent({
        calendar: args.calendar, summary: args.summary, start: args.start, end: args.end, join: args.join,
        guests: typeof args.guests === 'string' ? args.guests.split(',').map(s => s.trim()).filter(Boolean) : [],
        description: typeof args.description === 'string' ? args.description : '',
        timeZone: args.tz, send,
        reminders: typeof args.reminders === 'string' ? args.reminders.split(',').map(Number) : undefined,
      });
      const count = (ev.attendees || []).length;
      console.log(`${ev.summary}: created ${ev.id}, no Meet link, ${count} guest(s)` +
        `${send && count ? ', invites sent' : ', no invites sent'}\n${ev.htmlLink}`);
      return;
    }
    if (args['strip-meet']) {
      const ev = await stripMeet({ calendar: args.calendar, event: args.event, send: args.send === true || args.send === 'true' });
      console.log(ev ? `${ev.summary || '(no title)'}: Meet link removed` : 'No conference on that event - nothing changed');
      return;
    }
    const events = await listEvents({ calendar: args.calendar, start: args.start, end: args.end });
    if (args.json) { console.log(JSON.stringify(events, null, 2)); return; }
    console.log(`${events.length} event(s) on ${args.calendar || 'primary'} ${args.start}..${args.end}:`);
    for (const e of events) {
      const when = (e.start && (e.start.dateTime || e.start.date)) || '';
      console.log(`  ${when}  ${e.status}  ${e.summary || '(no title)'}`);
    }
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
