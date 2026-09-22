"""Tests for run-poller.py's reconcile_unhandled: which items it re-queues, and every guard that
keeps it off work that is fine.

Run directly:
    python plugins/drainer/tests/test_reconcile_unhandled.py
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

sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


CFG = {"orphan_grace_minutes": 15}
GRACE_S = CFG["orphan_grace_minutes"] * 60


class FakeProvider:
    """An email adapter stub: `name` plus the one hook the reconcile drives."""

    def __init__(self, name, inbox_ids):
        self.name = name
        self._inbox_ids = inbox_ids

    def still_in_inbox_ids(self):
        if isinstance(self._inbox_ids, Exception):
            raise self._inbox_ids
        return self._inbox_ids


def workspace(seen, items=None, queue=None, handled=None):
    """A runtime_dir holding seen.json, items/<id>.json captures, the digest queue and the memo."""
    rt = tempfile.mkdtemp(prefix="reconcile-")
    with open(os.path.join(rt, "seen.json"), "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.makedirs(os.path.join(rt, "items"), exist_ok=True)
    for iid, rec in (items or {}).items():
        with open(os.path.join(rt, "items", f"{iid}.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)
    if queue is not None:
        with open(os.path.join(rt, "digest-queue.json"), "w", encoding="utf-8") as f:
            json.dump(queue, f)
    if handled is not None:
        with open(os.path.join(rt, poller.HANDLED_FILE), "w", encoding="utf-8") as f:
            json.dump(handled, f)
    return rt


def captured(message_id, age_s=3 * GRACE_S):
    """An items/<id>.json as capture writes it, aged `age_s` seconds into the past."""
    return {"messageId": message_id, "ts": _iso_ago(age_s)}


def _iso_ago(seconds):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def write_session(rt, iid, guid, launched_ago_s=3 * GRACE_S):
    """The seeds/<id>.prompt.txt.session a launched worker leaves behind, with its launch mtime."""
    seeds = os.path.join(rt, "seeds")
    os.makedirs(seeds, exist_ok=True)
    p = os.path.join(seeds, f"{iid}.prompt.txt.session")
    with open(p, "w", encoding="utf-8") as f:
        f.write(guid)
    stamp = time.time() - launched_ago_s
    os.utime(p, (stamp, stamp))


def run(rt, providers, live=(), dry_run=False):
    """Drive the reconcile with a pinned live-session set, and report what it re-queued."""
    requeued = []
    real_live, real_seen_state = poller.live_session_ids, poller.seen_state
    poller.live_session_ids = lambda headless=False: set(live) if live is not None else None
    poller.seen_state = _recording_seen_state(rt, requeued, real_seen_state)
    try:
        n = poller.reconcile_unhandled(rt, CFG, providers, dry_run=dry_run)
    finally:
        poller.live_session_ids, poller.seen_state = real_live, real_seen_state
    return n, requeued


def _recording_seen_state(rt, requeued, real):
    """Intercept `requeue` so a test never shells out to node; everything else runs for real."""
    class Result:
        stdout = ""

    def fake(*args):
        if args and args[0] == "requeue":
            requeued.append((args[2], args[3]))
            return Result()
        if args and args[0] == "queue-list":
            r = Result()
            try:
                with open(os.path.join(rt, "digest-queue.json"), encoding="utf-8") as f:
                    r.stdout = f.read()
            except OSError:
                r.stdout = "[]"
            return r
        return real(*args)

    return fake


GMAIL = "gmail"

print("the rule: still in the inbox, no live worker -> re-queued")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})])
check("re-queues the unhandled item", requeued, [(GMAIL, "a")])
check("and counts it", n, 1)

print("\nan archived message is handled, self-evidently")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-other"})])
check("gone from the inbox -> left alone", requeued, [])
check(
    "and memoized so the next cycle skips it",
    json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")),
    {GMAIL: ["a"]},
)

print("\nguard: an item awaiting the digest sits in the inbox by design")
rt = workspace(
    seen={GMAIL: {"q": {"triage": "fyi"}}},
    items={"q": captured("msg-q")},
    queue=[{"id": "q", "source": GMAIL, "item": {"triage": "fyi"}}],
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-q"})])
check("queued item is not re-queued", requeued, [])
check(
    "and is not memoized either, so it is re-checked once the digest clears it",
    json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")),
    {GMAIL: []},
)

print("\nguard: a live worker session means the item is being worked or parked")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
write_session(rt, "a", "11111111-2222-3333-4444-555555555555")
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})],
                  live=["11111111-2222-3333-4444-555555555555"])
check("live tab is left alone however long it is up", requeued, [])

n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})], live=["99999999-0000-0000-0000-000000000000"])
check("a dead session re-queues", requeued, [(GMAIL, "a")])

print("\nguard: the launch grace covers a worker that has not written its session file yet")
rt = workspace(
    seen={GMAIL: {"fresh": {"triage": "needs-you"}}},
    items={"fresh": captured("msg-fresh", age_s=60)},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-fresh"})])
check("just dispatched -> left alone", requeued, [])

rt = workspace(
    seen={GMAIL: {"spun-up": {"triage": "needs-you"}}},
    items={"spun-up": captured("msg-spun-up")},
)
write_session(rt, "spun-up", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", launched_ago_s=60)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-spun-up"})])
check("session file younger than the grace -> left alone", requeued, [])

print("\nno capture means there is nothing to observe")
rt = workspace(seen={GMAIL: {"nocapture": {"triage": "junk"}}})
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})])
check("an item recorded seen without a capture is skipped", requeued, [])

print("\nfail-safe: reconcile nothing rather than re-queue live work")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})], live=None)
check("no process scan -> the whole cycle is skipped", (n, requeued), (0, []))

rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, None)])
check("an inbox listing that failed -> that provider is skipped", requeued, [])

rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, RuntimeError("adapter blew up"))])
check("an adapter that raises -> that provider is skipped", requeued, [])

print("\nscope: a provider with no inbox notion is never touched")
rt = workspace(
    seen={"slack": {"s": {"triage": "needs-you"}}},
    items={"s": captured("msg-s")},
)


class Chat:
    name = "slack"

    def still_in_inbox_ids(self):
        return None  # provider_base's default


n, requeued = run(rt, [Chat()])
check("slack is skipped with no code of its own", requeued, [])

print("\nthe memo carries proven-handled ids forward and drops ids no longer seen")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
    handled={GMAIL: ["a", "long-gone"]},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})])
check("a memoized id is not re-checked, even though it is in the inbox", requeued, [])
check(
    "and an id no longer in seen-state is pruned",
    json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")),
    {GMAIL: ["a"]},
)

print("\ndry-run counts without touching anything")
rt = workspace(
    seen={GMAIL: {"a": {"triage": "needs-you"}}},
    items={"a": captured("msg-a")},
)
n, requeued = run(rt, [FakeProvider(GMAIL, {"msg-a"})], dry_run=True)
check("counts what it would re-queue", n, 1)
check("but re-queues nothing", requeued, [])
check("and writes no memo", os.path.exists(os.path.join(rt, poller.HANDLED_FILE)), False)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
