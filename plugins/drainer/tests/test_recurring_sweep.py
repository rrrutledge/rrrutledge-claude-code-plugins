"""Test for the recurring-card sweep: scripts/recurrence.py's date math and trello-adapter's
_sweep_recurring (moves a missed `Recurs:` card's Start and leaves one dated comment). No network.
Run directly:
    python plugins/drainer/tests/test_recurring_sweep.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "trello-adapter.py")
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import recurrence  # noqa: E402

spec = importlib.util.spec_from_file_location("trello_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def test_parse_period():
    print("test: parse_period reads the Recurs line")
    p = recurrence.parse_period
    check("weekly", p("notes\nRecurs: weekly") == ("days", 7))
    check("monthly", p("Recurs: monthly") == ("months", 1))
    check("every Thursday is a weekly period", p("Recurs: every Thursday") == ("days", 7))
    check("every 3 days", p("Recurs: every 3 days") == ("days", 3))
    check("every 2 weeks", p("Recurs: Every 2 weeks") == ("days", 14))
    check("quarterly", p("Recurs: quarterly") == ("months", 3))
    check("no Recurs line", p("just a card") is None and p(None) is None)
    check("unrecognized cadence is left alone", p("Recurs: whenever the moon is full") is None)
    check("every 0 days is not a period", p("Recurs: every 0 days") is None)


def test_moved_start():
    print("test: moved_start acts only after a full period, on the original schedule")
    weekly = ("days", 7)
    thursday = utc(2026, 11, 5, 9)
    check("a day late is not touched", recurrence.moved_start(thursday, weekly, utc(2026, 11, 6)) is None)
    check("six days late is not touched", recurrence.moved_start(thursday, weekly, utc(2026, 11, 11, 23)) is None)
    check("exactly one period late moves to the next occurrence",
          recurrence.moved_start(thursday, weekly, utc(2026, 11, 12, 9)) == utc(2026, 11, 12, 9))
    check("missed several weeks ends on the latest Thursday, not a shifted day",
          recurrence.moved_start(thursday, weekly, utc(2026, 12, 1)) == utc(2026, 11, 26, 9),
          recurrence.moved_start(thursday, weekly, utc(2026, 12, 1)))
    check("a future Start is not missed", recurrence.moved_start(utc(2026, 11, 20), weekly, utc(2026, 11, 17)) is None)

    monthly = ("months", 1)
    first = utc(2026, 10, 1)
    check("monthly budget keeps a month of grace", recurrence.moved_start(first, monthly, utc(2026, 10, 31)) is None)
    check("monthly moves on the first once a month has passed",
          recurrence.moved_start(first, monthly, utc(2026, 11, 1)) == utc(2026, 11, 1))
    jan31 = utc(2026, 1, 31)
    check("a month-end start clamps without drifting the schedule",
          recurrence.moved_start(jan31, monthly, utc(2026, 4, 5)) == utc(2026, 3, 31),
          recurrence.moved_start(jan31, monthly, utc(2026, 4, 5)))
    check("february clamp for a single missed month",
          recurrence.moved_start(jan31, monthly, utc(2026, 3, 1)) == utc(2026, 2, 28))


class FakeUtils:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def trello_request(self, method, path, session, params=None, body=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((method, path, body))

    def add_comment(self, card_id, text, session):
        self.calls.append(("COMMENT", card_id, text))


def make_provider(fail=False):
    prov = adapter_mod.Provider.__new__(adapter_mod.Provider)
    prov._utils, prov._session = FakeUtils(fail), object()
    return prov


def test_sweep_recurring():
    print("test: _sweep_recurring moves Start, leaves one dated comment, and tolerates a failed write")
    now = utc(2026, 11, 17, 12)
    card = {"id": "c1", "desc": "Recurs: weekly", "start": utc(2026, 11, 5, 9).isoformat()}
    prov = make_provider()
    new = prov._sweep_recurring(card, utc(2026, 11, 5, 9), now)
    check("returns the moved Start", new == utc(2026, 11, 12, 9), new)
    check("card dict carries the new Start", card["start"] == utc(2026, 11, 12, 9).isoformat())
    kinds = [c[0] for c in prov._utils.calls]
    check("one Start write and one comment", kinds == ["PUT", "COMMENT"], kinds)
    check("comment names both dates and today",
          prov._utils.calls[1][2] == "2026-11-17: occurrence of 2026-11-05 missed, moved to 2026-11-12",
          prov._utils.calls[1][2])

    fresh = make_provider()
    same = utc(2026, 11, 15)
    check("a day-late card is untouched",
          fresh._sweep_recurring({"id": "c2", "desc": "Recurs: weekly"}, same, now) == same and not fresh._utils.calls)
    check("a non-recurring card is untouched",
          fresh._sweep_recurring({"id": "c3", "desc": "x"}, utc(2026, 1, 1), now) == utc(2026, 1, 1)
          and not fresh._utils.calls)

    broken = make_provider(fail=True)
    old = utc(2026, 11, 5, 9)
    check("a failed write keeps the old Start", broken._sweep_recurring(
        {"id": "c4", "desc": "Recurs: weekly"}, old, now) == old)


if __name__ == "__main__":
    test_parse_period()
    test_moved_start()
    test_sweep_recurring()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nall passed")
