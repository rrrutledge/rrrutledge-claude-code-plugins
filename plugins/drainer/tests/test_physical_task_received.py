"""Test that providers/physical-task-adapter.py stamps each task's `received` from its due day.

The poller orders needs-you items across sources by band, then `received`, newest first. A physical
task used to carry no `received`, so it sorted below every dated item in its band and never got a
worker while mail, Slack, and Trello kept the buffer busy. This pins that a physical task now sorts by
its due day among dated items from other sources. No Graph calls - the calendar.js runner is stubbed.
Run directly:
    python plugins/drainer/tests/test_physical_task_received.py
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "physical-task-adapter.py")

spec = importlib.util.spec_from_file_location("physical_task_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)
sys.path.insert(0, SCRIPTS)
from provider_base import band_rank  # noqa: E402

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def task(tid, date, minutes=15):
    return {"id": tid, "subject": tid, "date": date, "minutes": minutes, "isRecurring": False}


def make_provider(due, gap=120):
    def fake_run_node(args):
        if "--list-due-tasks" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(due), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"minutes": gap}), stderr="")

    adapter_mod.run_node = fake_run_node
    adapter_mod.Provider._find_calendar_js = staticmethod(lambda: "calendar.js")
    return adapter_mod.Provider()


def poller_order(items):
    """The poller's cross-source needs-you sort (run-poller.py's `other_needs`)."""
    return sorted(items, key=lambda it: (*band_rank(it), it.get("received") or ""), reverse=True)


def test_received_is_local_midnight_of_due_day():
    print("test: received is local midnight of the due day, in UTC")
    items = make_provider([task("chore", "2026-10-12")]).enumerate(50)
    want = datetime(2026, 10, 12).astimezone().astimezone(timezone.utc).isoformat()
    check("received stamped", items[0].get("received") == want, f"got {items[0].get('received')!r}")


def test_sorts_among_other_sources_by_date():
    print("test: a physical task interleaves with dated items instead of sorting last")
    phys = make_provider([task("chore", "2026-10-12")]).enumerate(50)[0]
    phys["_source"] = "physical-task"
    newer = {"_source": "outlook-graph", "received": "2026-10-14T15:00:00Z"}
    older = {"_source": "trello", "received": "2026-10-01T06:00:00+00:00"}
    order = [it["_source"] for it in poller_order([older, phys, newer])]
    check("newer mail, then the physical task, then the older card",
          order == ["outlook-graph", "physical-task", "trello"], f"got {order}")


def test_same_day_keeps_longest_first():
    print("test: tasks due the same day keep the adapter's longest-first order through the poller sort")
    items = make_provider([task("short", "2026-10-12", 15), task("long", "2026-10-12", 45)]).enumerate(50)
    order = [it["id"] for it in poller_order(items)]
    check("longest first", order == ["long", "short"], f"got {order}")


def test_bad_date_falls_back_to_empty():
    print("test: an unparseable date leaves received empty rather than raising")
    check("empty", adapter_mod.Provider._received("not-a-date") == "")


if __name__ == "__main__":
    test_received_is_local_midnight_of_due_day()
    test_sorts_among_other_sources_by_date()
    test_same_day_keeps_longest_first()
    test_bad_date_falls_back_to_empty()
    if failures:
        print(f"\n{len(failures)} failure(s)")
        sys.exit(1)
    print("\nall passed")
