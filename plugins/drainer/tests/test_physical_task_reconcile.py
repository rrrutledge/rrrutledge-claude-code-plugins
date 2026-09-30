"""Test for the physical-task provider's crash-recovery reconcile: capture() writing messageId, and
reconcile_unhandled actually re-queuing an abandoned task through it.

A physical-task worker that never runs Started (the CLEAR step that moves the event off the
overnight parking grid) leaves its task recorded-as-seen but the calendar event still queued.
Before this fix, capture() never wrote messageId, so reconcile_unhandled's `if not message_id`
guard fired immediately and the task was memoized handled forever on the very next reconcile pass
- exactly the bug Russell hit with "Fix sink" and "Call Mom" never resurfacing. This confirms
messageId is now written, and that reconcile_unhandled re-queues the task while it is still due.

Run directly:
    python plugins/drainer/tests/test_physical_task_reconcile.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
POLLER = os.path.join(SCRIPTS, "run-poller.py")
ADAPTER = os.path.join(PROVIDERS, "physical-task-adapter.py")

sys.path.insert(0, SCRIPTS)  # run-poller and the adapter import their siblings by bare name


def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller = _load("run_poller", POLLER)
adapter_mod = _load("physical_task_adapter", ADAPTER)
Provider = adapter_mod.Provider

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


CFG = {"orphan_grace_minutes": 15}
GRACE_S = CFG["orphan_grace_minutes"] * 60


def task(tid, minutes=15, date="2026-09-21", **extra):
    return {"id": tid, "subject": tid, "date": date, "minutes": minutes, "isRecurring": False, **extra}


def make_provider(due, gap=120):
    """A Provider whose calendar.js calls are stubbed: --list-due-tasks returns `due`,
    --gap-minutes returns `gap`. Same fixture shape as test_physical_task_days.py."""

    def fake_run_node(args):
        if "--list-due-tasks" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(due), stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"minutes": gap}), stderr="")

    adapter_mod.run_node = fake_run_node
    adapter_mod.Provider._find_calendar_js = staticmethod(lambda: "calendar.js")
    return adapter_mod.Provider()


print("capture writes messageId == the task's raw Graph id, alongside eventId")
p = make_provider([])
item = task("AAAAeventid==", _bucket="needs-you", _kind="work")
with tempfile.TemporaryDirectory() as rt:
    path = p.capture(item, "physical-task-fix-sink-x", rt)
    with open(path, encoding="utf-8") as f:
        record = json.load(f)
check("eventId is the raw Graph id", record["eventId"], "AAAAeventid==")
check("messageId equals eventId, the handle reconcile checks", record["messageId"], "AAAAeventid==")


# --------------------------------------------------------------------- end-to-end reconcile
def _iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def workspace(seen, items):
    rt = tempfile.mkdtemp(prefix="physical-task-reconcile-")
    with open(os.path.join(rt, "seen.json"), "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.makedirs(os.path.join(rt, "items"), exist_ok=True)
    for iid_, rec_ in items.items():
        with open(os.path.join(rt, "items", f"{iid_}.json"), "w", encoding="utf-8") as f:
            json.dump(rec_, f)
    return rt


def write_session(rt, iid_, guid, launched_ago_s=3 * GRACE_S):
    seeds = os.path.join(rt, "seeds")
    os.makedirs(seeds, exist_ok=True)
    path = os.path.join(seeds, f"{iid_}.prompt.txt.session")
    with open(path, "w", encoding="utf-8") as f:
        f.write(guid)
    stamp = time.time() - launched_ago_s
    os.utime(path, (stamp, stamp))


def run(rt, providers, live=()):
    requeued = []
    real_live, real_seen_state = poller.live_session_ids, poller.seen_state

    class Result:
        stdout = ""

    def fake_seen_state(*args):
        if args and args[0] == "requeue":
            requeued.append((args[2], args[3]))
            return Result()
        if args and args[0] == "queue-list":
            r = Result()
            r.stdout = "[]"
            return r
        return real_seen_state(*args)

    poller.live_session_ids = lambda: set(live) if live is not None else None
    poller.seen_state = fake_seen_state
    try:
        n = poller.reconcile_unhandled(rt, CFG, providers)
    finally:
        poller.live_session_ids, poller.seen_state = real_live, real_seen_state
    return n, requeued


FIX_SINK_ID = "physical-task-fix-sink-x"
EVENT_ID = "AAAAeventid=="

print("\nreconcile: an abandoned task (dispatched, never Started) still queued -> re-queued")
rt = workspace(
    seen={Provider.name: {FIX_SINK_ID: {"triage": "needs-you"}}},
    items={FIX_SINK_ID: {"messageId": EVENT_ID, "ts": _iso_ago(3 * GRACE_S)}},
)
p = make_provider([task(EVENT_ID)])
n, requeued = run(rt, [p])
check("the abandoned task is re-queued", requeued, [(Provider.name, FIX_SINK_ID)])

print("\nreconcile: the same task WITH a live worker is left alone")
rt = workspace(
    seen={Provider.name: {FIX_SINK_ID: {"triage": "needs-you"}}},
    items={FIX_SINK_ID: {"messageId": EVENT_ID, "ts": _iso_ago(3 * GRACE_S)}},
)
write_session(rt, FIX_SINK_ID, "11111111-2222-3333-4444-555555555555")
p = make_provider([task(EVENT_ID)])
n, requeued = run(rt, [p], live=["11111111-2222-3333-4444-555555555555"])
check("a live worker holds the task", requeued, [])

print("\nreconcile: a task that was actually Started (no longer queued) is left alone, memoized handled")
rt = workspace(
    seen={Provider.name: {FIX_SINK_ID: {"triage": "needs-you"}}},
    items={FIX_SINK_ID: {"messageId": EVENT_ID, "ts": _iso_ago(3 * GRACE_S)}},
)
p = make_provider([])  # Started -> off the parking grid -> no longer in --list-due-tasks
n, requeued = run(rt, [p])
check("a started task is not re-queued", requeued, [])
check(
    "and is memoized handled",
    json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")),
    {Provider.name: [FIX_SINK_ID]},
)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
