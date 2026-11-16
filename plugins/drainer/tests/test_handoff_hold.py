"""Tests for a handed-off item's hold: a worker that hands its item's work to a new session through
spawn-session.py leaves a handoff link, and the drainer reads that session as holding the item for as
long as it is live - reconcile leaves the item alone, and its correspondent and conversation stay held -
while a handoff session that has ended releases the item within one cycle, as a dead worker does.

Run directly:
    python plugins/drainer/tests/test_handoff_hold.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller and provider_base import their siblings by bare name
import provider_base  # noqa: E402

provider_base.bg_session.PAUSED_PATH = os.path.join(tempfile.mkdtemp(), "paused.json")  # not the machine's record
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)
bg = provider_base.bg_session

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


WORKER = "69e3285a-3462-4414-992f-647f52ad77b3"
HANDOFF = "61eff9b0-d2c2-4949-8bfd-927cc7ce5906"
SECOND = "aaaabbbb-0000-1111-2222-333333333333"
IID = "trello-lpl-card-06c7fa-202611160600"
CARD = "trello-card|6aec8826a2b48ce1d406c7fa"
KEY = "trello|6aec8826a2b48ce1d406c7fa"


def workspace(receipt):
    """A runtime_dir holding one dispatched card: its captured item and its worker's receipt, written
    an hour ago so the launch grace has long passed."""
    rt = tempfile.mkdtemp(prefix="handoff-hold-")
    os.makedirs(os.path.join(rt, "seeds"))
    os.makedirs(os.path.join(rt, "items"))
    session = os.path.join(rt, "seeds", f"{IID}.prompt.txt.session")
    with open(session, "w", encoding="utf-8") as f:
        f.write(receipt)
    hour_ago = time.time() - 3600
    os.utime(session, (hour_ago, hour_ago))
    with open(os.path.join(rt, "items", f"{IID}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": IID, "messageId": IID, "correspondent": CARD, "conversationKey": KEY,
                   "ts": "2026-11-16T18:48:00+00:00"}, f)
    return rt


class TrelloStub:
    """A provider whose board still shows the card startable, as it does while a handoff works it."""
    name = "trello"

    def still_in_inbox_ids(self):
        return {IID}


def reconcile(rt, live):
    """Run reconcile on one seen card with seen-state stubbed, returning the ids it re-queued."""
    requeued = []
    real_seen, real_load = poller.seen_state, poller.load_seen

    def fake_seen(cmd, runtime_dir, *args):
        if cmd == "requeue":
            requeued.append(args[1])
        return type("R", (), {"stdout": "[]"})()
    poller.seen_state, poller.load_seen = fake_seen, lambda runtime_dir, source: [IID]
    try:
        poller.reconcile_unhandled(rt, {"orphan_grace_minutes": 10}, [TrelloStub()], live=live)
    finally:
        poller.seen_state, poller.load_seen = real_seen, real_load
    return requeued


print("before any handoff: a closed worker leaves its startable card to be re-queued")
rt = workspace(WORKER)
check("no live holder -> re-queued", reconcile(rt, set()), [IID])

print("\nhandoff recorded: the session the worker launched holds the card while it is live")
bg.record_handoff(WORKER, HANDOFF)
check("the resolver reaches the handoff session", bg.holder_resolver()(WORKER), [WORKER, HANDOFF])
check("worker closed, handoff live -> not re-queued", reconcile(rt, {HANDOFF}), [])
check("its correspondent stays held", poller.open_correspondents(rt, {HANDOFF}), {CARD})
check("its conversation is held by the handoff session",
      poller.open_conversations(rt, {HANDOFF}, set()), {KEY: (HANDOFF, "live")})

print("\nfail-open: a handoff session that has ended releases the card within one cycle")
check("worker and handoff both gone -> re-queued", reconcile(rt, set()), [IID])
check("no correspondent held", poller.open_correspondents(rt, set()), set())

print("\nchains: a handoff that hands off again, and a short-id receipt")
bg.record_handoff(HANDOFF, SECOND)
check("the second handoff holds the card", reconcile(rt, {SECOND}), [])
check("the furthest live session is the holder",
      poller.open_conversations(rt, {HANDOFF, SECOND}, set()), {KEY: (SECOND, "live")})
check("a worker receipt written as a short id still reaches its handoff",
      reconcile(workspace(WORKER.split("-", 1)[0]), {HANDOFF}), [])

print("\nresumes: a handoff session that was resumed holds under its newer guid")
RESUMED = "cccc1111-2222-3333-4444-555555555555"
bg.record_resume(HANDOFF, continued_as=RESUMED)
check("the resumed guid holds the card", reconcile(rt, {RESUMED}), [])
check("a paused worker with no live handoff holds its conversation as paused",
      poller.open_conversations(rt, set(), {WORKER}),
      {KEY: (WORKER, "paused")})

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
