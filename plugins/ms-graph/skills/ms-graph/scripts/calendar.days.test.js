// Tests for calendar.js's day-of-week gate helpers (marker parse + today's-day check).
// These are pure functions (no Graph calls), so they run offline. Run: node --test calendar.days.test.js
const { test } = require('node:test');
const assert = require('node:assert');

const { parseDaysMarker, parseDays, isInDays, localDOW, daysMarkerLine } = require('./calendar-days');

test('parses a single-day marker', () => {
  assert.deepStrictEqual(parseDaysMarker('Days: SA'), ['SA']);
});

test('parses a comma list, sorted to canonical MO..SU order regardless of input order', () => {
  assert.deepStrictEqual(parseDaysMarker('Days: SU,SA'), ['SA', 'SU']);
});

test('dedupes repeated codes', () => {
  assert.deepStrictEqual(parseDaysMarker('Days: SA,SA,SU'), ['SA', 'SU']);
});

test('tolerates spacing and case', () => {
  assert.deepStrictEqual(parseDaysMarker('days:  sa , su '), ['SA', 'SU']);
});

test('finds the marker among other body lines without slurping the next line', () => {
  const body = 'Repeat: 2 days after completion\nDays: SA\nLast done 2026-09-19.';
  assert.deepStrictEqual(parseDaysMarker(body), ['SA']);
});

test('returns null when there is no marker or every code is invalid', () => {
  assert.strictEqual(parseDaysMarker('Just a normal note.'), null);
  assert.strictEqual(parseDaysMarker(''), null);
  assert.strictEqual(parseDaysMarker(undefined), null);
  assert.strictEqual(parseDaysMarker('Days: XX'), null);
});

test('parseDays reads a bare config value and rejects junk', () => {
  assert.deepStrictEqual(parseDays('SA,SU'), ['SA', 'SU']);
  assert.deepStrictEqual(parseDays(' sa , su '), ['SA', 'SU']);
  assert.strictEqual(parseDays('weekends'), null);
  assert.strictEqual(parseDays(''), null);
  assert.strictEqual(parseDays(undefined), null);
});

test('a null/empty days list is unrestricted', () => {
  assert.strictEqual(isInDays(null, 'SA'), true);
  assert.strictEqual(isInDays([], 'SU'), true);
});

test('membership test', () => {
  assert.strictEqual(isInDays(['SA'], 'SA'), true);
  assert.strictEqual(isInDays(['SA'], 'SU'), false);
  assert.strictEqual(isInDays(['SA', 'SU'], 'SU'), true);
});

test('localDOW renders the day-of-week code for the given timezone', () => {
  // 04:36 UTC on 2026-09-22 (a Tuesday) is 23:36 Central the evening before, still Monday.
  const now = new Date('2026-09-22T04:36:00Z');
  assert.strictEqual(localDOW('America/Chicago', now), 'MO');
  assert.strictEqual(localDOW('UTC', now), 'TU');
});

test('localDOW covers all seven codes', () => {
  assert.strictEqual(localDOW('UTC', new Date('2026-09-19T12:00:00Z')), 'SA');
  assert.strictEqual(localDOW('UTC', new Date('2026-09-20T12:00:00Z')), 'SU');
});

test('daysMarkerLine round-trips through the parser', () => {
  assert.strictEqual(daysMarkerLine(['SA']), 'Days: SA');
  assert.deepStrictEqual(parseDaysMarker(daysMarkerLine(['SA'])), ['SA']);
  assert.strictEqual(daysMarkerLine(['SA', 'SU']), 'Days: SA,SU');
  assert.deepStrictEqual(parseDaysMarker(daysMarkerLine(['SA', 'SU'])), ['SA', 'SU']);
});
