"""Date math for the recurring-card sweep: read a card's `Recurs:` line and, when a full period has
passed since its Start, say where the Start should move. Pure functions, no network.

A `Recurs:` card keeps one card for the whole task and moves its Start forward only when an occurrence is
finished, so a missed occurrence leaves an old Start. The sweep (trello-adapter) moves that Start to the
latest date on the card's original schedule that is not in the future.
"""
import calendar
import re
from datetime import datetime, timedelta

_RECURS_RE = re.compile(r"^Recurs:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_EVERY_N_RE = re.compile(r"^every\s+(\d+)\s+(day|week|month|year)s?$", re.IGNORECASE)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Named cadences as (unit, count); unit is "days" or "months".
_NAMED = {
    "daily": ("days", 1), "weekly": ("days", 7), "biweekly": ("days", 14), "fortnightly": ("days", 14),
    "every other week": ("days", 14), "monthly": ("months", 1), "quarterly": ("months", 3),
    "yearly": ("months", 12), "annually": ("months", 12),
}
_UNIT = {"day": ("days", 1), "week": ("days", 7), "month": ("months", 1), "year": ("months", 12)}


def parse_period(desc):
    """Return the card's period as `(unit, count)` - unit "days" or "months" - or None when the desc
    has no `Recurs:` line or a cadence this module does not recognize. Handles daily, weekly, biweekly,
    monthly, quarterly, yearly, "every other week", "every N days/weeks/months/years", and "every
    <weekday>" (a weekly period)."""
    m = _RECURS_RE.search(desc or "")
    if not m:
        return None
    text = m.group(1).strip().lower().rstrip(".")
    if text in _NAMED:
        return _NAMED[text]
    n = _EVERY_N_RE.match(text)
    if n:
        unit, size = _UNIT[n.group(2).lower()]
        return (unit, int(n.group(1)) * size) if int(n.group(1)) > 0 else None
    if text.startswith("every ") and text[6:] in _WEEKDAYS:
        return ("days", 7)
    return None


def _add_months(dt, months):
    index = dt.year * 12 + (dt.month - 1) + months
    year, month = divmod(index, 12)
    day = min(dt.day, calendar.monthrange(year, month + 1)[1])
    return dt.replace(year=year, month=month + 1, day=day)


def _shift(start, period, k):
    """`start` moved forward by k periods, always counted from the original Start so a clamped month end
    (the 31st in a short month) never drifts the schedule."""
    unit, count = period
    return start + timedelta(days=count * k) if unit == "days" else _add_months(start, count * k)


def moved_start(start, period, now):
    """The Start a missed card should move to, or None when it is not missed.
    The occurrence is missed once `now` is at or past Start plus one period; the new Start is the latest
    date on the original schedule (Start plus a whole number of periods) that is not after `now`, so a
    task missed several times in a row ends on one current occurrence and keeps its weekday."""
    if _shift(start, period, 1) > now:
        return None
    k = 1
    while _shift(start, period, k + 1) <= now:
        k += 1
    return _shift(start, period, k)
