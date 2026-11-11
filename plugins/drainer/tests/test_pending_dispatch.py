"""Tests for the pending list (pending.py) and the dispatch pass that works it (run-poller.py):
storing and pruning entries, reusing a held item's stored verdicts, and a pass that opens workers only up to
the free slots, keeps the correspondent hold, drops already-dispatched items, and fails closed on a failed
session scan.

Run directly:
    python plugins/drainer/tests/test_pending_dispatch.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("run_poller", os.path.join(SCRIPTS, "run-poller.py"))
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)
import pending  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def item(iid, source="gmail", bucket="needs-you", **fields):
    it = {"id": iid, "subject": f"subject {iid}", "received": fields.pop("received", f"2026-11-11T0{len(iid)}:00Z"),
          "_id": iid, "_source": source, "_bucket": bucket, "_kind": "reply", "_complexity": "simple"}
    it.update(fields)
    return it


def ids(entries):
    return sorted(e["_id"] for e in entries)


# ---------------------------------------------------------------------------- the store
print("\npending store: put, get, load_all, remove")
rt = tempfile.mkdtemp(prefix="pending-")
check("an empty list reads as empty", pending.load_all(rt), [])
check("a first put writes", pending.put(rt, item("a:1/x")), True)
first = pending.get(rt, "gmail", "a:1/x")
check("an id with : and / round-trips", (first or {}).get("_id"), "a:1/x")
check("the entry is stamped with when it joined", bool((first or {}).get("_pendingSince")), True)
check("an unchanged put skips the write", pending.put(rt, item("a:1/x")), False)
pending.put(rt, item("a:1/x", subject="edited"))
check("a changed put keeps the original join time",
      pending.get(rt, "gmail", "a:1/x")["_pendingSince"], first["_pendingSince"])
pending.put(rt, item("b"))
pending.put(rt, item("c", source="slack"))
check("load_all lists every entry", ids(pending.load_all(rt)), ["a:1/x", "b", "c"])
check("remove reports a removal", pending.remove(rt, "gmail", "b"), True)
check("remove of a missing entry is a no-op", pending.remove(rt, "gmail", "b"), False)
with open(os.path.join(rt, "pending", "junk.json"), "w", encoding="utf-8") as f:
    f.write("{not json")
check("an unreadable entry reads as absent", ids(pending.load_all(rt)), ["a:1/x", "c"])

print("\nprune drops only the named source's entries it no longer offers")
pending.put(rt, item("d"))
check("two gmail entries go, one stays", pending.prune(rt, "gmail", {"d"}, pending.load_all(rt)), 1)
check("slack is untouched", ids(pending.load_all(rt)), ["c", "d"])

# ---------------------------------------------------------------------------- verdict reuse
print("\nreuse_pending_verdicts: a held item keeps its stored verdicts while its listing is unchanged")
rt = tempfile.mkdtemp(prefix="pending-")
stored = item("m1", _screen={"flag": True, "reason": "odd"}, _triageReason="asks for a reply")
pending.put(rt, stored)
fresh = {"id": "m1", "subject": "subject m1", "received": stored["received"], "_id": "m1", "_source": "gmail"}
check("an unchanged listing reuses", poller.reuse_pending_verdicts(rt, fresh), True)
check("the bucket comes back", fresh.get("_bucket"), "needs-you")
check("the screen verdict comes back", fresh.get("_screen"), {"flag": True, "reason": "odd"})
check("the join time is not copied onto the item", "_pendingSince" in fresh, False)
changed = {"id": "m1", "subject": "a new subject", "received": stored["received"], "_id": "m1", "_source": "gmail"}
check("a changed listing is judged again", poller.reuse_pending_verdicts(rt, changed), False)
check("an item not on the list is judged",
      poller.reuse_pending_verdicts(rt, {"id": "zz", "_id": "zz", "_source": "gmail"}), False)

# ---------------------------------------------------------------------------- feed_pending
print("\nfeed_pending: store needs-you and auto-handle, drop no-worker items, prune what a source stopped offering")
rt = tempfile.mkdtemp(prefix="pending-")
pending.put(rt, item("gone"))          # dispatched or archived since: gmail no longer offers it
pending.put(rt, item("now-fyi"))       # re-judged fyi this cycle
pending.put(rt, item("t1", source="trello"))  # trello's listing failed this cycle: kept as is
poller.feed_pending(rt, {"gmail": {"new", "auto", "now-fyi"}},
                    [item("new"), item("auto", bucket="auto-handle")], [item("now-fyi", bucket="fyi")])
check("the new items are stored; the rest pruned", ids(pending.load_all(rt)), ["auto", "new", "t1"])

# ---------------------------------------------------------------------------- dispatch_pass
print("\ndispatch_pass: opens workers up to the free slots and leaves the rest pending")


class Provider:
    def __init__(self, name):
        self.name = name

    def conversation_key(self, it):
        return None

    def correspondent(self, it):
        return it.get("who")

    def capture(self, it, iid, runtime_dir):
        return os.path.join(runtime_dir, "items", f"{iid}.json")

    def clear(self, it):
        return None


seen = {}
launched = []
poller.seen_state = lambda cmd, rt_, source, iid, label: seen.setdefault(source, {}).__setitem__(iid, label)
poller.load_seen = lambda rt_, source: dict(seen.get(source, {}))
poller.spawn_worker = lambda iid, *a, **k: launched.append(iid) or True
poller._stamp_item_fields = lambda *a, **k: None
poller.paused_ids = lambda agents: set()
poller._worker_title = lambda iid, jf: iid
agents_scan = [{"pid": 1, "status": "idle", "kind": "interactive"}]
poller._claude_agents = lambda: agents_scan


def setup(entries):
    rt_ = tempfile.mkdtemp(prefix="dispatch-")
    for e in entries:
        pending.put(rt_, e)
    seen.clear()
    launched.clear()
    cfg = {"runtime_dir": rt_, "target_reviewable": 3, "max_concurrent": 18, "worker_model": "sonnet",
           "worker_model_complex": "opus", "local_dir": rt_}
    return rt_, cfg


prov = {"gmail": Provider("gmail"), "slack": Provider("slack")}
rt, cfg = setup([item("a", received="2026-11-11T05:00Z"), item("b", received="2026-11-11T04:00Z"),
                 item("c", received="2026-11-11T03:00Z"), item("d", received="2026-11-11T02:00Z")])
n = poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
# One live session is already waiting, so a target of 3 leaves 2 slots.
check("two workers open, newest first", launched, ["a", "b"])
check("the pass reports two", n, 2)
check("both are recorded seen", sorted(seen.get("gmail", {})), ["a", "b"])
check("the two held items stay pending", ids(pending.load_all(rt)), ["c", "d"])

print("\ndispatch_pass: an item already dispatched leaves the list without a worker")
rt, cfg = setup([item("a"), item("b")])
seen["gmail"] = {"a": "needs-you"}
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
check("only b opens", launched, ["b"])
check("the list is empty", pending.load_all(rt), [])

print("\ndispatch_pass: a second item from a correspondent with an open worker waits")
rt, cfg = setup([item("a", who="addie", received="2026-11-11T05:00Z"),
                 item("b", who="addie", received="2026-11-11T04:00Z"),
                 item("c", who="bo", received="2026-11-11T03:00Z")])
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
check("one worker per correspondent", launched, ["a", "c"])
check("Addie's second item stays pending", ids(pending.load_all(rt)), ["b"])

print("\ndispatch_pass: auto-handle opens even with no needs-you slots")
rt, cfg = setup([item("x", bucket="auto-handle"), item("y")])
cfg["target_reviewable"] = 1  # the one waiting session already fills it
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
check("only the auto-handle item opens", launched, ["x"])
check("its seen label is auto-handle", seen.get("gmail", {}).get("x"), "auto-handle")

print("\ndispatch_pass: an item whose source isn't loaded waits for a pass that has it")
rt, cfg = setup([item("z", source="teams")])
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
check("nothing opens", launched, [])
check("the entry stays", ids(pending.load_all(rt)), ["z"])

print("\ndispatch_pass: a launch keeps holding its correspondent on later passes for LAUNCH_HOLD")
recent = {"convs": {}, "corrs": {}}
rt, cfg = setup([item("a", who="addie")])
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt), recent=recent)
check("the first item opens", launched, ["a"])
check("its correspondent is remembered", sorted(recent["corrs"]), ["addie"])
# The session scan doesn't list the new worker yet, so only the remembered claim can hold the follow-up.
pending.put(rt, item("b", who="addie"))
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt), recent=recent)
check("the follow-up waits on the next pass", (launched, ids(pending.load_all(rt))), (["a"], ["b"]))
recent["corrs"]["addie"] -= poller.LAUNCH_HOLD
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt), recent=recent)
check("once the hold lapses it opens", launched, ["a", "b"])

print("\nsave_feed_health keeps the scan fields a dispatch pass wrote meanwhile")
rt = tempfile.mkdtemp(prefix="health-")
poller.save_health(rt, {poller.POLLER_KEY: {"tab_scan_consecutive_failures": 2, "last_run_ts": "old"},
                        "gmail": {"consecutive_failures": 3}})
poller.save_feed_health(rt, {poller.POLLER_KEY: {"last_run_ts": "new", "last_drained_ts": "new"},
                             "gmail": {"consecutive_failures": 0}})
h = poller.load_health(rt)
check("the scan count survives", h[poller.POLLER_KEY].get("tab_scan_consecutive_failures"), 2)
check("the heartbeat updates", h[poller.POLLER_KEY].get("last_run_ts"), "new")
check("the provider outcome updates", h["gmail"]["consecutive_failures"], 0)

print("\ndispatch_pass: a failed session scan opens nothing (fail-closed)")
agents_scan = None
rt, cfg = setup([item("a"), item("b")])
poller.dispatch_pass(cfg, prov, rt, rt, pending.load_all(rt))
check("no worker opens", launched, [])
check("both stay pending", ids(pending.load_all(rt)), ["a", "b"])
check("the scan failure is counted",
      poller.load_health(rt).get(poller.POLLER_KEY, {}).get("tab_scan_consecutive_failures"), 1)
agents_scan = [{"pid": 1, "status": "idle", "kind": "interactive"}]

print("\ndispatch_lock: one pass at a time")
rt = tempfile.mkdtemp(prefix="lock-")
with poller.dispatch_lock(rt) as first_got:
    with poller.dispatch_lock(rt, timeout=0) as second_got:
        check("the first holder gets it, a second does not", (first_got, second_got), (True, False))
with poller.dispatch_lock(rt, timeout=0) as again:
    check("it is free once released", again, True)

if failures:
    print(f"\n{len(failures)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
