"""Recognise a `claude -p` call the background account refused because it is out of usage (or its login
lapsed), and work out when a retry can succeed.

When the account can't serve a call, the CLI still returns a normal `--output-format json` envelope: the
`result` text is a fixed refusal message (`You've hit your weekly limit · resets Sep 17, 5pm
(America/Chicago)`, or `Failed to authenticate: OAuth session expired and could not be refreshed`) with
`is_error` set, instead of a model reply. Pure functions only - no state, no I/O - so the poller's
circuit breaker (run-poller.py) and its tests share one detector.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

USAGE_LIMIT = "usage-limit"
AUTH = "auth"

USAGE_LIMIT_DEFAULT_BACKOFF = timedelta(minutes=60)  # the reset time is unknown or unparseable
AUTH_BACKOFF = timedelta(minutes=30)                  # a lapsed login is usually re-established within a session
RESET_GRACE = timedelta(minutes=1)                    # first retry lands just after the stated reset, not on it
MAX_RESET_HORIZON = timedelta(days=8)                 # a parsed reset further out than a weekly window is a misparse

_LIMIT = re.compile(
    r"(?:you['’]ve|you have) (?:hit|reached) your\b[^\n]{0,40}?\blimit\b"
    r"|(?:claude (?:ai )?)?usage limit reached\b|\d+-hour limit reached\b", re.I)
_AUTH = re.compile(
    r"failed to authenticate|oauth (?:session|token)\b[^\n]{0,30}\bexpired|please run /login", re.I)
_RESET = re.compile(
    r"resets?\s+(?:(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ampm>[ap]m)\b(?:\s*\((?P<tz>[^)]+)\))?", re.I)


def _classify(text, anchored):
    """(kind, message line) when `text` is an account refusal, else None. A refusal is the whole reply, so
    for model output (`anchored`) the message must open the text - a triage verdict whose `reason` merely
    quotes a limit notice in an email never matches. Error text (stderr, an `is_error` result) is searched
    anywhere, since it can't be a model verdict."""
    for kind, pattern in ((USAGE_LIMIT, _LIMIT), (AUTH, _AUTH)):
        find = pattern.match if anchored else pattern.search
        if find(text.strip() if anchored else text):
            line = next((ln for ln in text.splitlines() if pattern.search(ln)), text)
            return kind, line.strip()[:300]
    return None


def detect(stdout, stderr=""):
    """Classify one finished call: (kind, message) when the account refused it, else None."""
    result, is_error = stdout or "", False
    try:
        envelope = json.loads(stdout)
    except (TypeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        result, is_error = str(envelope.get("result") or ""), bool(envelope.get("is_error"))
    return _classify(result, anchored=not is_error) or _classify(stderr or "", anchored=False)


def _zone(name, now):
    """The zone a reset time is stated in. The CLI prints resets in the machine's own zone, so when the
    named zone can't be loaded (Windows Python ships no tz database) the local zone of `now` is the answer."""
    if name:
        try:
            return ZoneInfo(name.strip())
        except (KeyError, ValueError, OSError):
            pass
    return now.tzinfo or timezone.utc


def parse_reset(message, now):
    """The aware datetime a `resets [Mon D, ]H[:MM]am/pm [(Zone)]` message names, or None. Without a date
    it is the next occurrence of that time of day; with one, this year's (next year's if that would be
    long past)."""
    m = _RESET.search(message or "")
    if not m:
        return None
    local_now = now.astimezone(_zone(m.group("tz"), now))
    hour = int(m.group("hour"))
    if not 1 <= hour <= 12:
        return None
    hour = hour % 12 + (12 if m.group("ampm").lower() == "pm" else 0)
    try:
        at = local_now.replace(hour=hour, minute=int(m.group("min") or 0), second=0, microsecond=0)
        if m.group("mon"):
            month = datetime.strptime(m.group("mon")[:3], "%b").month
            at = at.replace(month=month, day=int(m.group("day")))
            if at < local_now - timedelta(days=1):
                at = at.replace(year=at.year + 1)
        elif at <= local_now:
            at += timedelta(days=1)
    except ValueError:
        return None
    return at


def retry_at(kind, message, now):
    """When the poller should try the background account again: just after the stated reset for a usage
    limit, otherwise a fixed backoff for its kind."""
    if kind == AUTH:
        return now + AUTH_BACKOFF
    reset = parse_reset(message, now)
    if reset is None or not now < reset <= now + MAX_RESET_HORIZON:
        return now + USAGE_LIMIT_DEFAULT_BACKOFF
    return reset + RESET_GRACE
