"""Tests for the drainer keeper (keeper.py): the single-instance lock, the dispatch loop and feed thread,
a feed cycle that overruns or hangs, and each way the keeper recycles.

Run directly:
    python plugins/drainer/tests/test_keeper.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "drainer", "scripts"))
import keeper  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class Proc:
    """A feed cycle that exits at once, or hangs past every timeout when `hang` is set."""

    def __init__(self, hang=False):
        self.hang = hang
        self.killed = False
        self.pid = 0

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            time.sleep(min(timeout or 0, 0.05))
            raise subprocess.TimeoutExpired("feed", timeout)
        return 0

    def kill(self):
        self.killed = True


def harness(hang=False):
    calls = {"dispatch": 0, "feeds": [], "log": []}

    def dispatch():
        calls["dispatch"] += 1

    def popen(cmd, **kw):
        p = Proc(hang)
        calls["feeds"].append(p)
        return p
    return calls, dispatch, popen


keeper.kill_tree = lambda proc: proc.kill()  # the fakes have no process tree to end
FAST = dict(dispatch_interval=0.02, feed_interval=0.05, feed_timeout=0.05)

print("\nInstanceLock: one holder at a time, free again once released")
rt = tempfile.mkdtemp(prefix="keeper-")
a = keeper.InstanceLock(os.path.join(rt, "x.lock"))
b = keeper.InstanceLock(os.path.join(rt, "x.lock"))
check("the first acquire wins", a.acquire(), True)
check("a second holder is refused", b.acquire(), False)
a.release()
check("after release it can be taken", b.acquire(), True)
b.release()

print("\nrun: a second keeper exits at once while one holds the lock")
rt = tempfile.mkdtemp(prefix="keeper-")
held = keeper.InstanceLock(os.path.join(rt, keeper.LOCK_FILE))
held.acquire()
calls, dispatch, popen = harness()
check("it reports locked", keeper.run(rt, dispatch, ["feed"], popen=popen, log=calls["log"].append, **FAST), "locked")
check("and neither dispatched nor fed", (calls["dispatch"], len(calls["feeds"])), (0, 0))
held.release()

print("\nrun: dispatches repeatedly, feeds repeatedly, and recycles once it has run max_age")
rt = tempfile.mkdtemp(prefix="keeper-")
calls, dispatch, popen = harness()
reason = keeper.run(rt, dispatch, ["feed"], popen=popen, log=calls["log"].append, max_age=0.4, **FAST)
check("the reason names the age", reason.startswith("ran "), True)
check("several dispatch passes ran", calls["dispatch"] >= 3, True)
check("several feed cycles ran", len(calls["feeds"]) >= 2, True)
check("the lock is free afterwards", keeper.InstanceLock(os.path.join(rt, keeper.LOCK_FILE)).acquire(), True)

print("\nrun: a hung feed cycle is killed and the next one starts")
rt = tempfile.mkdtemp(prefix="keeper-")
calls, dispatch, popen = harness(hang=True)
keeper.run(rt, dispatch, ["feed"], popen=popen, log=calls["log"].append, max_age=0.4, **FAST)
check("every hung cycle was killed", all(p.killed for p in calls["feeds"]), True)
check("cycles kept starting", len(calls["feeds"]) >= 2, True)
check("the kill is logged", any("killed" in line for line in calls["log"]), True)

print("\nrun: a failing dispatch pass is logged and the loop carries on")
rt = tempfile.mkdtemp(prefix="keeper-")
calls, _, popen = harness()
count = [0]


def flaky():
    count[0] += 1
    raise RuntimeError("boom")


keeper.run(rt, flaky, ["feed"], popen=popen, log=calls["log"].append, max_age=0.2, **FAST)
check("passes kept running", count[0] >= 3, True)
check("the failure is logged", any("dispatch pass failed: RuntimeError: boom" in line for line in calls["log"]), True)

print("\nrun: recycles when should_recycle says a newer version is installed, checked between feed cycles")
rt = tempfile.mkdtemp(prefix="keeper-")
calls, dispatch, popen = harness()
answers = iter([False, False, True])
reason = keeper.run(rt, dispatch, ["feed"], popen=popen, log=calls["log"].append,
                    should_recycle=lambda: next(answers, True), **FAST)
check("the reason names the new version", reason, "a newer drainer version is installed")
check("it ran a check after each of three feed cycles, then stopped feeding", len(calls["feeds"]), 3)
check("it dispatched meanwhile", calls["dispatch"] >= 1, True)

print("\nrun: recycles past the memory threshold")
rt = tempfile.mkdtemp(prefix="keeper-")
calls, dispatch, popen = harness()
saved = keeper.rss_mb
keeper.rss_mb = lambda: 5000.0
reason = keeper.run(rt, dispatch, ["feed"], popen=popen, log=calls["log"].append, max_rss_mb=600, **FAST)
keeper.rss_mb = saved
check("the reason names the memory", reason, "memory 5000 MB")

print("\nrss_mb reads this process's memory")
mem = keeper.rss_mb()
check("a plausible size in MB", mem is not None and 1 < mem < 4000, True)

if failures:
    print(f"\n{len(failures)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
