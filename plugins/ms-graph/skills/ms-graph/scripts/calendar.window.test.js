// Tests for calendar.js's time-of-day window helpers (marker parse + in-window clock math).
// These are pure functions (no Graph calls), so they run offline. Run: node --test calendar.window.test.js
const { test } = require('node:test');
const assert = require('node:assert');

const { parseWindowMarker, parseWindow, isInWindow, localHHMM, windowMarkerLine } = require('./calendar-window');

test('parses a window marker to canonical HH:MM-HH:MM', () => {
  assert.strictEqual(parseWindowMarker('Window: 09:00-20:00'), '09:00-20:00');
});

test('pads single-digit hours and tolerates spacing and case', () => {
  assert.strictEqual(parseWindowMarker('window:  9:30 - 20:00'), '09:30-20:00');
});

test('finds the marker among other body lines', () => {
  const body = 'Repeat: 2 days after completion\nWindow: 10:00-19:30\nLast done 2026-09-19.';
  assert.strictEqual(parseWindowMarker(body), '10:00-19:30');
});

test('returns null when there is no marker or it is out of range', () => {
  assert.strictEqual(parseWindowMarker('Just a normal note.'), null);
  assert.strictEqual(parseWindowMarker(''), null);
  assert.strictEqual(parseWindowMarker(undefined), null);
  assert.strictEqual(parseWindowMarker('Window: 25:00-26:00'), null);
  assert.strictEqual(parseWindowMarker('Window: 09:75-20:00'), null);
});

test('allows 24:00 as an end-of-day bound', () => {
  assert.strictEqual(parseWindowMarker('Window: 08:00-24:00'), '08:00-24:00');
});

test('parseWindow reads a bare config value and rejects junk', () => {
  assert.strictEqual(parseWindow('08:00-21:00'), '08:00-21:00');
  assert.strictEqual(parseWindow(' 8:00-21:00 '), '08:00-21:00');
  assert.strictEqual(parseWindow('daytime'), null);
  assert.strictEqual(parseWindow(''), null);
  assert.strictEqual(parseWindow(undefined), null);
});

test('a null window is unrestricted', () => {
  assert.strictEqual(isInWindow(null, '23:36'), true);
  assert.strictEqual(isInWindow(null, '03:00'), true);
});

test('start is inclusive, end is exclusive', () => {
  assert.strictEqual(isInWindow('09:00-20:00', '09:00'), true);
  assert.strictEqual(isInWindow('09:00-20:00', '19:59'), true);
  assert.strictEqual(isInWindow('09:00-20:00', '20:00'), false);
  assert.strictEqual(isInWindow('09:00-20:00', '08:59'), false);
});

test('the late-night Call Mom case falls outside a daytime window', () => {
  assert.strictEqual(isInWindow('09:00-20:00', '23:36'), false);
});

test('a window wrapping midnight covers both sides of it', () => {
  assert.strictEqual(isInWindow('20:00-02:00', '23:00'), true);
  assert.strictEqual(isInWindow('20:00-02:00', '01:30'), true);
  assert.strictEqual(isInWindow('20:00-02:00', '02:00'), false);
  assert.strictEqual(isInWindow('20:00-02:00', '12:00'), false);
});

test('a zero-width window is treated as unrestricted', () => {
  assert.strictEqual(isInWindow('09:00-09:00', '03:00'), true);
});

test('localHHMM renders wall-clock time in the given timezone', () => {
  // 04:36 UTC on 2026-09-22 is 23:36 the evening before in Central (CDT, UTC-5).
  const now = new Date('2026-09-22T04:36:00Z');
  assert.strictEqual(localHHMM('America/Chicago', now), '23:36');
  assert.strictEqual(localHHMM('UTC', now), '04:36');
});

test('localHHMM renders midnight as 00, not 24', () => {
  assert.strictEqual(localHHMM('UTC', new Date('2026-09-22T00:05:00Z')), '00:05');
});

test('windowMarkerLine round-trips through the parser', () => {
  assert.strictEqual(windowMarkerLine('09:00-20:00'), 'Window: 09:00-20:00');
  assert.strictEqual(parseWindowMarker(windowMarkerLine('09:00-20:00')), '09:00-20:00');
});
