"""Tests for providers/zoom-adapter.py's correspondent override — the fan-out dispatch guarantee.

A meeting fans out into one needs-you item per owner-assigned next step plus one recap. Every candidate
carries the meeting topic as its `from`, so the default correspondent (which keys on `from`) would give
all of them one shared identity. The dispatch loop holds any needs-you item whose correspondent is already
active this cycle, so a shared identity throttles a whole meeting's action items to one dispatch per cycle
and, in a busy needs-you queue, starves the rest indefinitely.

The override returns None for every zoom item, so each fanned-out action item competes for a tab slot on
its own. This test drives the real adapter's `correspondent()` and the poller's `held_for_correspondent`
to prove the whole meeting's items dispatch in one cycle instead of one per cycle.

No network or Zoom credentials. Run directly:
    python plugins/drainer/tests/test_zoom_correspondent_fanout.py
"""
import importlib.util
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "zoom-adapter.py")
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("zoom_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

pspec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(pspec)
pspec.loader.exec_module(poller)

OWNER = ["Russell", "Russ", "Rutledge"]
TOPIC = "2026 InnerSource Summit Planning"
UUID = "KehewilXSuekv8XzxrNRdQ=="

OWNER_STEPS = [f"Russell: {t}" for t in (
    "Send speaker acceptance emails tomorrow", "Start outreach to speakers and sponsors this week",
    "Draft the sponsor prospectus", "Confirm the keynote speaker", "Finalize the review rubric",
    "Book the venue walkthrough", "Send the save-the-date", "Publish the CFP results",
    "Line up the program committee", "Review the budget")]
OTHER_STEPS = ["Priya: publish the registration page", "Sam: line up the AV vendor"]

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def make_provider():
    p = adapter_mod.Provider.__new__(adapter_mod.Provider)
    p.owner_names = list(OWNER)
    return p


def meeting_items(p):
    inst = {"uuid": UUID, "id": "82127920509", "topic": TOPIC, "start_time": "2026-09-17T13:29:49Z"}
    summary = {"summary_overview": "Planning sync for the 2026 Summit.",
               "next_steps": OWNER_STEPS + OTHER_STEPS}
    return p._meeting_items(inst, summary, OWNER)


def test_every_candidate_has_no_correspondent():
    print("test: every zoom candidate resolves to a None correspondent")
    p = make_provider()
    items = meeting_items(p)
    actions = [it for it in items if it.get("kind") == "action-item"]
    recaps = [it for it in items if it.get("kind") == "recap"]
    check("the meeting fans out to 10 owner action items", len(actions) == 10, len(actions))
    check("plus one recap", len(recaps) == 1, len(recaps))
    check("every action item's correspondent is None",
          all(p.correspondent(it) is None for it in actions),
          [p.correspondent(it) for it in actions])
    check("the recap's correspondent is None too", p.correspondent(recaps[0]) is None)


def test_whole_meeting_dispatches_in_one_cycle():
    print("test: with None correspondents, a whole meeting's action items dispatch in one cycle")
    p = make_provider()
    actions = [it for it in meeting_items(p) if it.get("kind") == "action-item"]

    # Replays the needs-loop's correspondent discipline (run-poller.main): an item is held when its
    # correspondent is already active this cycle; on dispatch its correspondent is registered. Tab cap is
    # left out on purpose — this isolates the correspondent throttle, which is what the fix addresses.
    active = set()
    dispatched = 0
    for it in actions:
        corr = p.correspondent(it)
        if poller.held_for_correspondent(corr, active):
            continue
        dispatched += 1
        if corr:
            active.add(corr)
    check("all 10 dispatch in the same cycle (none held behind another)", dispatched == 10, dispatched)

    # Contrast: the shared meeting-topic identity the DEFAULT correspondent would have produced collapses
    # the same 10 to a single dispatch per cycle — the starvation this override removes.
    shared = "2026 innersource summit planning"
    active = set()
    collapsed = 0
    for _ in actions:
        if poller.held_for_correspondent(shared, active):
            continue
        collapsed += 1
        active.add(shared)
    check("a shared identity would have collapsed them to one per cycle", collapsed == 1, collapsed)


def test_stable_ids_stay_distinct():
    print("test: the fanned-out items keep distinct stable ids (dispatch is per action item)")
    p = make_provider()
    actions = [it for it in meeting_items(p) if it.get("kind") == "action-item"]
    ids = {p.stable_id(it) for it in actions}
    check("10 action items -> 10 distinct ids", len(ids) == 10, len(ids))


if __name__ == "__main__":
    test_every_candidate_has_no_correspondent()
    test_whole_meeting_dispatches_in_one_cycle()
    test_stable_ids_stay_distinct()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
