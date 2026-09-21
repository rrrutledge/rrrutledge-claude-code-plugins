// From-completion repeat markers for physical tasks — pure date/marker math, no Graph dependency, so
// it's unit-testable offline (see calendar.repeat.test.js) and cheap to reuse. calendar.js's
// finishTaskNow requires these to read a `Repeat:` marker off a just-finished event and place the
// next one-off.
//
// A one-off physical task opts into "come back a fixed interval after I actually FINISH it" with a
// single line in its event body:  `Repeat: 7 days after completion`  (also `1 week`, `1 month`).
// That's the one recurrence Outlook's own engine can't express — its recurrence is calendar-fixed,
// running from a set start date, never from when a task was last done. Markers belong on ONE-OFFS
// only: a recurring series already spawns its own occurrences, so a marker on one would double up.

const REPEAT_MARKER_RE = /Repeat:\s*(\d+)\s*(day|days|week|weeks|month|months)\s+after\s+completion/i;

// Reads the marker out of an event body. Returns { n, unit } with unit normalized to the singular
// day|week|month, or null when there's no marker.
function parseRepeatMarker(body) {
  const m = REPEAT_MARKER_RE.exec(body || '');
  if (!m) return null;
  return { n: parseInt(m[1], 10), unit: m[2].toLowerCase().replace(/s$/, '') };
}

// Adds a parsed interval to a YYYY-MM-DD date, returning YYYY-MM-DD. `day`/`week` are exact day
// counts. `month` steps the month field and keeps the day-of-month, CLAMPED to the target month's
// last day — so a task finished Jan 31 with a `1 month` marker lands on Feb 28/29, never spilling
// into March. All arithmetic is on the calendar date alone (no time-of-day, no timezone), so it's
// DST-proof.
function addRepeatInterval(dateStr, n, unit) {
  const [y, mo, d] = dateStr.split('-').map(Number);
  const pad = x => String(x).padStart(2, '0');
  if (unit === 'month') {
    const t = mo - 1 + n;
    const yy = y + Math.floor(t / 12);
    const mm = ((t % 12) + 12) % 12;
    const dd = Math.min(d, new Date(yy, mm + 1, 0).getDate());
    return `${yy}-${pad(mm + 1)}-${pad(dd)}`;
  }
  const days = unit === 'week' ? n * 7 : n;
  const dt = new Date(Date.UTC(y, mo - 1, d) + days * 86400000);
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

// Canonical one-line marker for a successor's body, normalizing singular/plural (`1 month`, not
// `1 months`; `7 days`, not `7 day`) regardless of how the source phrased it.
function repeatMarkerLine({ n, unit }) {
  return `Repeat: ${n} ${unit}${n === 1 ? '' : 's'} after completion`;
}

module.exports = { parseRepeatMarker, addRepeatInterval, repeatMarkerLine };
