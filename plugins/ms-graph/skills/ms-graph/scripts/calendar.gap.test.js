// Tests for calendar.js's getGapUntilNextCommitment — a mocked Graph client, no network calls.
// Run: node --test calendar.gap.test.js
const { test } = require('node:test');
const assert = require('node:assert');

const { getGapUntilNextCommitment } = require('./calendar');

// A minimal chainable stand-in for the `@microsoft/microsoft-graph-client` Client: every builder
// method returns the same chain object, and `get()` resolves based on the requested path — good
// enough for getGapUntilNextCommitment, which never inspects the query params it sends.
function makeClient({ calendars, eventsByCalId }) {
  return {
    api(path) {
      const chain = {
        select: () => chain,
        top: () => chain,
        query: () => chain,
        header: () => chain,
        orderby: () => chain,
        async get() {
          if (path === '/me/calendars') return { value: calendars };
          const m = path.match(/^\/me\/calendars\/([^/]+)\/calendarView$/);
          if (m) return { value: eventsByCalId[m[1]] || [] };
          return { value: [] };
        },
      };
      return chain;
    },
  };
}

function isoIn(minutes) {
  return new Date(Date.now() + minutes * 60000).toISOString();
}

test('a solo self-organized event with real duration now blocks the gap', async () => {
  const client = makeClient({
    calendars: [{ id: 'cal1', name: 'Calendar', canEdit: true }],
    eventsByCalId: {
      cal1: [{
        isAllDay: false,
        isOrganizer: true,
        attendees: [{ emailAddress: { address: 'russell.rutledge@outlook.com' } }],
        start: { dateTime: isoIn(10) },
        end: { dateTime: isoIn(30) },
      }],
    },
  });
  const minutes = await getGapUntilNextCommitment({ lookaheadHours: 2, client });
  assert.ok(minutes >= 8 && minutes <= 11, `expected ~10, got ${minutes}`);
});

test('an event on an excluded calendar (e.g. Physical Tasks) never blocks', async () => {
  const client = makeClient({
    calendars: [
      { id: 'cal-real', name: 'Calendar', canEdit: true },
      { id: 'cal-tasks', name: 'Physical Tasks', canEdit: true },
    ],
    eventsByCalId: {
      'cal-real': [],
      'cal-tasks': [{
        isAllDay: false,
        isOrganizer: true,
        attendees: [{ emailAddress: { address: 'russell.rutledge@outlook.com' } }],
        start: { dateTime: isoIn(5) },
        end: { dateTime: isoIn(25) },
      }],
    },
  });
  const minutes = await getGapUntilNextCommitment({ lookaheadHours: 2, exclude: ['Physical Tasks'], client });
  assert.strictEqual(minutes, 120);
});

test('an all-day event never blocks', async () => {
  const client = makeClient({
    calendars: [{ id: 'cal1', name: 'Calendar', canEdit: true }],
    eventsByCalId: {
      cal1: [{
        isAllDay: true,
        isOrganizer: true,
        attendees: [],
        start: { dateTime: isoIn(1) },
        end: { dateTime: isoIn(24 * 60) },
      }],
    },
  });
  const minutes = await getGapUntilNextCommitment({ lookaheadHours: 2, client });
  assert.strictEqual(minutes, 120);
});
