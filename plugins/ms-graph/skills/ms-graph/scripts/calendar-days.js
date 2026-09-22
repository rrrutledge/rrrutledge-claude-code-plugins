// Day-of-week markers for physical tasks - pure marker parsing, no Graph dependency, so it's
// unit-testable offline (see calendar.days.test.js). calendar.js's listDueTasks requires these to
// report whether each queued task may dispatch today, and finishTaskNow requires them to carry a
// marker forward onto a repeat-after-completion successor.
//
// A task opts into "only surface me on these days" with a single line in its event body:
// `Days: SA` (comma list for more than one: `Days: SA,SU`). The marker's semantics live in the
// drainer's physical-task-provider.md (DAY-OF-WEEK-GATE); this file is the parsing behind them.
// Codes are the same MO,TU,WE,TH,FR,SA,SU vocabulary calendar.js's own WEEKDAY_CODES map uses for
// --create-recurring's --days (that map's values are full lowercase day names for Graph - only the
// codes are shared here).

const DAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
const DAYS_MARKER_RE = /Days:\s*([A-Za-z]{2}(?:\s*,\s*[A-Za-z]{2})*)/i;
const DOW_TO_CODE = { Mon: 'MO', Tue: 'TU', Wed: 'WE', Thu: 'TH', Fri: 'FR', Sat: 'SA', Sun: 'SU' };

function toDays(raw) {
  const codes = raw.split(',').map(c => c.trim().toUpperCase());
  if (codes.some(c => !DAY_CODES.includes(c))) return null;
  return [...new Set(codes)].sort((a, b) => DAY_CODES.indexOf(a) - DAY_CODES.indexOf(b));
}

// Reads the marker out of an event body. Returns a canonical sorted/deduped array of day codes,
// or null when there's no (valid) marker.
function parseDaysMarker(body) {
  const m = DAYS_MARKER_RE.exec(body || '');
  return m ? toDays(m[1]) : null;
}

// Parses a bare comma list (the adapter's `default_days` config knob) to canonical form, or null
// when it's empty or malformed.
function parseDays(value) {
  if (!value) return null;
  return toDays(String(value));
}

// Whether today's day code is allowed by `days`. A null/empty `days` is unrestricted.
function isInDays(days, code) {
  if (!days || !days.length) return true;
  return days.includes(code);
}

// Today's two-letter day code for `now` in `tz`, the same timezone listDueTasks asks Graph to
// render start times in, so the day gate and the parking grid never disagree about what day it is.
function localDOW(tz, now = new Date()) {
  const weekday = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short' }).format(now);
  return DOW_TO_CODE[weekday];
}

function daysMarkerLine(days) {
  return `Days: ${days.join(',')}`;
}

module.exports = { parseDaysMarker, parseDays, isInDays, localDOW, daysMarkerLine };
