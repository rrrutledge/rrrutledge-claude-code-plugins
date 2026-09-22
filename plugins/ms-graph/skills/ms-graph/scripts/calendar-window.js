// Time-of-day window markers for physical tasks - pure marker/clock math, no Graph dependency, so
// it's unit-testable offline (see calendar.window.test.js). calendar.js's listDueTasks requires these
// to report whether each queued task may dispatch right now, and finishTaskNow requires them to carry
// a marker forward onto a repeat-after-completion successor.
//
// A task opts into "only surface me during these hours" with a single line in its event body:
// `Window: 09:00-20:00`. The marker's semantics live in the drainer's physical-task-provider.md
// (TIME-OF-DAY-WINDOW); this file is the parsing and clock math behind them.

const WINDOW_MARKER_RE = /Window:\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/i;
const WINDOW_RE = /^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$/;

const pad = n => String(n).padStart(2, '0');

function toWindow(m) {
  const [sh, sm, eh, em] = m.slice(1, 5).map(Number);
  if (sh > 23 || eh > 24 || sm > 59 || em > 59 || (eh === 24 && em !== 0)) return null;
  return `${pad(sh)}:${pad(sm)}-${pad(eh)}:${pad(em)}`;
}

// Reads the marker out of an event body. Returns the canonical `HH:MM-HH:MM` string, or null when
// there's no (valid) marker.
function parseWindowMarker(body) {
  const m = WINDOW_MARKER_RE.exec(body || '');
  return m ? toWindow(m) : null;
}

// Parses a bare `HH:MM-HH:MM` value (the adapter's `default_window` config knob) to canonical form,
// or null when it's empty or malformed.
function parseWindow(value) {
  const m = WINDOW_RE.exec(value || '');
  return m ? toWindow(m) : null;
}

// Whether local wall-clock `hhmm` ("HH:MM") falls inside a canonical window. A null window is
// unrestricted. Equal start and end also means unrestricted (a zero-width window would never open).
function isInWindow(window, hhmm) {
  if (!window) return true;
  const [start, end] = window.split('-');
  if (start === end) return true;
  if (start < end) return hhmm >= start && hhmm < end;
  return hhmm >= start || hhmm < end;
}

// Local wall-clock "HH:MM" for `now` in `tz`, the same timezone listDueTasks asks Graph to render
// start times in, so the window and the parking grid never disagree about what time it is.
function localHHMM(tz, now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(now);
  const get = t => parts.find(p => p.type === t).value;
  return `${get('hour')}:${get('minute')}`;
}

function windowMarkerLine(window) {
  return `Window: ${window}`;
}

module.exports = { parseWindowMarker, parseWindow, isInWindow, localHHMM, windowMarkerLine };
