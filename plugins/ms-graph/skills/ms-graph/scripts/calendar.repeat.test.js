// Tests for calendar.js's from-completion repeat helpers (marker parse + interval date math).
// These are pure functions — no Graph calls — so they run offline. Run: node --test calendar.repeat.test.js
const { test } = require('node:test');
const assert = require('node:assert');

const { parseRepeatMarker, addRepeatInterval, repeatMarkerLine } = require('./calendar-repeat');

test('parses a days marker', () => {
  assert.deepStrictEqual(parseRepeatMarker('Repeat: 7 days after completion'), { n: 7, unit: 'day' });
});

test('parses a week marker (singular)', () => {
  assert.deepStrictEqual(parseRepeatMarker('Repeat: 1 week after completion'), { n: 1, unit: 'week' });
});

test('parses a month marker and ignores surrounding body text', () => {
  const body = 'Check the salt tank.\nRepeat: 1 month after completion\nLast done 2026-09-21.';
  assert.deepStrictEqual(parseRepeatMarker(body), { n: 1, unit: 'month' });
});

test('is case-insensitive and tolerant of extra spacing', () => {
  assert.deepStrictEqual(parseRepeatMarker('repeat:  14   days   after   completion'), { n: 14, unit: 'day' });
});

test('returns null when there is no marker', () => {
  assert.strictEqual(parseRepeatMarker('Just a normal note.'), null);
  assert.strictEqual(parseRepeatMarker(''), null);
  assert.strictEqual(parseRepeatMarker(undefined), null);
});

test('does not match a bare "repeats every week" phrase (needs "after completion")', () => {
  assert.strictEqual(parseRepeatMarker('Repeat: 1 week (every Monday)'), null);
});

test('adds days across a month boundary', () => {
  assert.strictEqual(addRepeatInterval('2026-09-21', 7, 'day'), '2026-09-28');
  assert.strictEqual(addRepeatInterval('2026-09-28', 7, 'day'), '2026-10-05');
});

test('adds weeks as 7-day multiples', () => {
  assert.strictEqual(addRepeatInterval('2026-09-21', 1, 'week'), '2026-09-28');
  assert.strictEqual(addRepeatInterval('2026-09-21', 2, 'week'), '2026-10-05');
});

test('adds a month keeping day-of-month', () => {
  assert.strictEqual(addRepeatInterval('2026-09-21', 1, 'month'), '2026-10-21');
});

test('a month add clamps to the target month\'s last day', () => {
  assert.strictEqual(addRepeatInterval('2026-01-31', 1, 'month'), '2026-02-28'); // no spill into March
  assert.strictEqual(addRepeatInterval('2028-01-31', 1, 'month'), '2028-02-29'); // leap year
});

test('a month add rolls the year over', () => {
  assert.strictEqual(addRepeatInterval('2026-12-15', 1, 'month'), '2027-01-15');
  assert.strictEqual(addRepeatInterval('2026-06-15', 12, 'month'), '2027-06-15');
});

test('day arithmetic is DST-proof (spring forward)', () => {
  // US DST began 2026-03-08; a 7-day add across it must still land on the same calendar day count.
  assert.strictEqual(addRepeatInterval('2026-03-05', 7, 'day'), '2026-03-12');
});

test('repeatMarkerLine normalizes singular and plural', () => {
  assert.strictEqual(repeatMarkerLine({ n: 1, unit: 'month' }), 'Repeat: 1 month after completion');
  assert.strictEqual(repeatMarkerLine({ n: 7, unit: 'day' }), 'Repeat: 7 days after completion');
  assert.strictEqual(repeatMarkerLine({ n: 2, unit: 'week' }), 'Repeat: 2 weeks after completion');
});

test('parse → line round-trips a plural input to canonical form', () => {
  assert.strictEqual(repeatMarkerLine(parseRepeatMarker('Repeat: 3 weeks after completion')),
    'Repeat: 3 weeks after completion');
});
