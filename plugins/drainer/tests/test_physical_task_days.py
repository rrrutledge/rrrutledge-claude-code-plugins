"""Test for providers/physical-task-adapter.py's day-of-week gate.

calendar.js decides `inDays` per task (its own offline tests cover the weekday math): this covers
the adapter side: enumerate drops off-day tasks alongside the gap and window filters, and a row
with no days stays unrestricted. No Graph calls - the calendar.js runner is stubbed. Run directly:
    python plugins/drainer/tests/test_physical_task_days.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "physical-task-adapter.py")

spec = importlib.util.spec_from_file_location("physical_task_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def task(tid, minutes=15, date="2026-09-21", **extra):
    return {"id": tid, "subject": tid, "date": date, "minutes": minutes, "isRecurring": False, **extra}


def make_provider(due, gap=120, calls=None):
    """A Provider whose calendar.js calls are stubbed: --list-due-tasks returns `due`,
    --gap-minutes returns `gap`. Every argv is appended to `calls`."""
    calls = calls if calls is not None else []

    def fake_run_node(args):
        calls.append(args)
        if "--list-due-tasks" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(due), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"minutes": gap}), stderr="")

    adapter_mod.run_node = fake_run_node
    adapter_mod.Provider._find_calendar_js = staticmethod(lambda: "calendar.js")
    return adapter_mod.Provider()


def ids(items):
    return [t["id"] for t in items]


def test_no_days_is_unrestricted():
    print("test: a task with no days field enumerates on gap alone")
    p = make_provider([task("plain")])
    check("enumerated", ids(p.enumerate(10)) == ["plain"])


def test_null_days_in_days_is_eligible():
    print("test: a task whose days is null (no marker, no default) enumerates")
    p = make_provider([task("plain", days=None, inDays=True)])
    check("enumerated", ids(p.enumerate(10)) == ["plain"])


def test_marker_matching_today_is_eligible():
    print("test: a marked task that includes today enumerates")
    p = make_provider([task("goals-checkin", days=["SA"], inDays=True)])
    check("enumerated", ids(p.enumerate(10)) == ["goals-checkin"])


def test_marker_not_matching_today_is_not_eligible():
    print("test: a marked task that excludes today sits the cycle out")
    p = make_provider([task("goals-checkin", days=["SA"], inDays=False), task("plain")])
    check("only the unrestricted task enumerated", ids(p.enumerate(10)) == ["plain"])


def test_off_day_skips_gap_check_when_nothing_left():
    print("test: when every due task is off-day, no gap check runs")
    calls = []
    p = make_provider([task("goals-checkin", days=["SA"], inDays=False)], calls=calls)
    check("nothing enumerated", p.enumerate(10) == [])
    check("no gap call", not any("--gap-minutes" in c for c in calls), calls)


def test_off_day_stays_in_inbox():
    print("test: an off-day task is still queued for reconcile")
    p = make_provider([task("goals-checkin", days=["SA"], inDays=False)])
    check("still in inbox", p.still_in_inbox_ids() == {"goals-checkin"})


def test_days_and_window_and_gap_all_apply():
    print("test: a task off-day and in-window still doesn't enumerate")
    p = make_provider([task("long", minutes=90, days=["SA"], inDays=False, window="09:00-20:00", inWindow=True)], gap=200)
    check("not enumerated", p.enumerate(10) == [])


def test_capture_records_days():
    print("test: capture records the task's days")
    p = make_provider([])
    item = task("goals-checkin", days=["SA"], inDays=True, _bucket="needs-you", _kind="work")
    with tempfile.TemporaryDirectory() as rt:
        path = p.capture(item, "physical-task-goals-checkin-x", rt)
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    check("days captured", record["days"] == ["SA"], record.get("days"))


if __name__ == "__main__":
    for fn in [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]:
        fn()
    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        sys.exit(1)
    print("\nAll tests passed.")
