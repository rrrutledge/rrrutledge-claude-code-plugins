// Read a Google Calendar's events over a time range, or remove one event's Google Meet link, via the
// official `googleapis` Calendar v3 client (OAuth path - see google-calendar-oauth.js).
//
// List a range:  node google-calendar.js --start=<date or RFC 3339> --end=<date or RFC 3339> [--calendar=<id>] [--json]
//                (--calendar defaults to `primary`. A plain YYYY-MM-DD date is local midnight; a timestamp
//                needs its offset, e.g. 2026-10-01T00:00:00-05:00. --end is
//                exclusive, so --start=2026-10-01 --end=2026-11-01 is all of October. --json prints the raw
//                Event resources; without it, one line per event.)
// Remove Meet:   node google-calendar.js --strip-meet --event=<id> [--calendar=<id>] [--send]
//                (Drops the event's conference when it is Google Meet, leaving every other field as is, and
//                refuses when it is another provider's, such as a Zoom add-on. Guests get no update email
//                unless --send is passed. An occurrence id changes that one occurrence; the series id
//                changes the whole series.)
//
// Reusable from other scripts (require.main-guarded, so requiring this file does not run the CLI):
//   const { listEvents, stripMeet } = require('<google-calendar>/scripts/google-calendar.js');
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
// Refuses an event whose conference is another provider's, so a Zoom add-on link is never dropped by mistake.
// conferenceDataVersion=1 is what makes the API apply a conferenceData change at all.
async function stripMeet({ calendar = 'primary', event, send = false } = {}) {
  if (typeof event !== 'string' || !event) throw new Error('stripMeet requires { event: <id> }');
  const auth = getAuthedClient();
  await assertAccountEmail(auth);
  const api = google.calendar({ version: 'v3', auth });
  const current = (await api.events.get({ calendarId: calendar, eventId: event })).data;
  const title = current.summary || '(no title)';
  if (!current.organizer?.self) throw new Error(`${title}: organized by someone else - only the organizer can remove Meet`);
  const conf = current.conferenceData;
  if (!conf) {
    if (current.hangoutLink) throw new Error(`${title}: legacy Meet link with no conference data - remove it in the Calendar UI`);
    return null;
  }
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
  if (res.data.conferenceData || res.data.hangoutLink) throw new Error(`${title}: conference still present after the patch`);
  return res.data;
}

module.exports = { listEvents, stripMeet };

if (require.main === module) {
  const args = Object.fromEntries(
    process.argv.slice(2).map(a => {
      const m = a.match(/^--([^=]+)(?:=(.*))?$/);
      return m ? [m[1], m[2] ?? true] : [a, true];
    })
  );
  (async () => {
    if (args['strip-meet']) {
      const ev = await stripMeet({ calendar: args.calendar, event: args.event, send: args.send === true });
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
