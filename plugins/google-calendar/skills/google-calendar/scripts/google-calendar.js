// Read a Google Calendar's events over a time range, via the official `googleapis` Calendar v3 client
// (OAuth path - see google-calendar-oauth.js).
//
// List a range:  node google-calendar.js --start=<date or RFC 3339> --end=<date or RFC 3339> [--calendar=<id>] [--json]
//                (--calendar defaults to `primary`. A plain YYYY-MM-DD date is local midnight. --end is
//                exclusive, so --start=2026-10-01 --end=2026-11-01 is all of October. --json prints the raw
//                Event resources; without it, one line per event.)
//
// Reusable from other scripts (require.main-guarded, so requiring this file does not run the CLI):
//   const { listEvents } = require('<google-calendar>/scripts/google-calendar.js');
//   const events = await listEvents({ start: '2026-10-01', end: '2026-11-01' });

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

module.exports = { listEvents };

if (require.main === module) {
  const args = Object.fromEntries(
    process.argv.slice(2).map(a => {
      const m = a.match(/^--([^=]+)(?:=(.*))?$/);
      return m ? [m[1], m[2] ?? true] : [a, true];
    })
  );
  (async () => {
    const events = await listEvents({ calendar: args.calendar, start: args.start, end: args.end });
    if (args.json) { console.log(JSON.stringify(events, null, 2)); return; }
    console.log(`${events.length} event(s) on ${args.calendar || 'primary'} ${args.start}..${args.end}:`);
    for (const e of events) {
      const when = (e.start && (e.start.dateTime || e.start.date)) || '';
      console.log(`  ${when}  ${e.status}  ${e.summary || '(no title)'}`);
    }
  })().catch(e => { console.error('Error:', e.message); process.exit(1); });
}
