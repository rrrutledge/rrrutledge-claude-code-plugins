"""Test for providers/physical-task-adapter.py's time-of-day window gate.

calendar.js decides `inWindow` per task (its own offline tests cover the clock math); this covers
the adapter side: enumerate drops out-of-window tasks alongside the gap filter, a row with no window
stays unrestricted, and a configured `default_window` reaches calendar.js. No Graph calls - the
calendar.js runner is stubbed. Run directly:
    python plugins/drainer/tests/test_physical_task_window.py
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


def test_no_window_is_unrestricted():
    print("test: a task with no window field enumerates on gap alone")
    p = make_provider([task("plain")])
    check("enumerated", ids(p.enumerate(10)) == ["plain"])


def test_null_window_in_window_is_eligible():
    print("test: a task whose window is null (no marker, no default) enumerates")
    p = make_provider([task("plain", window=None, inWindow=True)])
    check("enumerated", ids(p.enumerate(10)) == ["plain"])


def test_marker_inside_window_is_eligible():
    print("test: a marked task with now inside its window enumerates")
    p = make_provider([task("call-mom", window="09:00-20:00", inWindow=True)])
    check("enumerated", ids(p.enumerate(10)) == ["call-mom"])


def test_marker_outside_window_is_not_eligible():
    print("test: a marked task with now outside its window sits the cycle out")
    p = make_provider([task("call-mom", window="09:00-20:00", inWindow=False), task("plain")])
    check("only the unwindowed task enumerated", ids(p.enumerate(10)) == ["plain"])


def test_outside_window_skips_gap_check_when_nothing_left():
    print("test: when every due task is out of window, no gap check runs")
    calls = []
    p = make_provider([task("call-mom", window="09:00-20:00", inWindow=False)], calls=calls)
    check("nothing enumerated", p.enumerate(10) == [])
    check("no gap call", not any("--gap-minutes" in c for c in calls), calls)


def test_outside_window_stays_in_inbox():
    print("test: an out-of-window task is still queued for reconcile")
    p = make_provider([task("call-mom", window="09:00-20:00", inWindow=False)])
    check("still in inbox", p.still_in_inbox_ids() == {"call-mom"})


def test_window_and_gap_both_apply():
    print("test: an in-window task that doesn't fit the gap still doesn't enumerate")
    p = make_provider([task("long", minutes=90, window="09:00-20:00", inWindow=True)], gap=60)
    check("not enumerated", p.enumerate(10) == [])


def write_config(repo, body):
    os.makedirs(os.path.join(repo, ".claude"))
    with open(os.path.join(repo, ".claude", "drainer.local.md"), "w", encoding="utf-8") as f:
        f.write(body)


def test_default_window_unset_by_default():
    print("test: with no default_window configured, calendar.js gets no --default-window")
    calls = []
    with tempfile.TemporaryDirectory() as repo:
        write_config(repo, "providers:\n  physical-task:\n    buffer_minutes: 10\n")
        p = make_provider([task("plain")], calls=calls)
        p.configure({"repo": repo})
    check("default_window is None", p.default_window is None, p.default_window)
    p.enumerate(10)
    list_call = next(c for c in calls if "--list-due-tasks" in c)
    check("no flag passed", not any(a.startswith("--default-window") for a in list_call), list_call)


def test_default_window_configured_reaches_calendar_js():
    print("test: a configured default_window is passed to calendar.js")
    calls = []
    with tempfile.TemporaryDirectory() as repo:
        write_config(repo, 'providers:\n  physical-task:\n    default_window: "08:00-21:00"  # waking hours\n')
        p = make_provider([task("plain")], calls=calls)
        p.configure({"repo": repo})
    check("default_window parsed", p.default_window == "08:00-21:00", p.default_window)
    p.enumerate(10)
    list_call = next(c for c in calls if "--list-due-tasks" in c)
    check("flag passed", "--default-window=08:00-21:00" in list_call, list_call)


def test_malformed_default_window_is_config_error():
    print("test: calendar.js rejecting default_window surfaces as a config error, not auth")

    def fake_run_node(args):
        return SimpleNamespace(returncode=1, stdout="",
                               stderr='Error: --default-window must be HH:MM-HH:MM, got "daytime"')

    p = make_provider([])
    p.default_window = "daytime"
    adapter_mod.run_node = fake_run_node
    try:
        p.enumerate(10)
        check("raised", False)
    except adapter_mod.ProviderError as e:
        check("kind is config", e.kind == "config", e.kind)


def test_capture_records_window():
    print("test: capture records the task's window")
    p = make_provider([])
    item = task("call-mom", window="09:00-20:00", inWindow=True, _bucket="needs-you", _kind="work")
    with tempfile.TemporaryDirectory() as rt:
        path = p.capture(item, "physical-task-call-mom-x", rt)
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    check("window captured", record["window"] == "09:00-20:00", record.get("window"))


if __name__ == "__main__":
    for fn in [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]:
        fn()
    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        sys.exit(1)
    print("\nAll tests passed.")
