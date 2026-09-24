"""Tests for provider_base.run_subprocess_bounded (and run_node, its thin wrapper): the shared fix for
the 2026-09-24 incident where a node helper wedged badly enough that it stayed alive - and kept its
stdout/stderr pipes open - even after being killed. `subprocess.run(..., timeout=X)` hangs unboundedly
on Windows in exactly that situation (its own stdlib implementation calls `communicate()` a SECOND time
with no timeout at all, to drain leftover output, after a TimeoutExpired kill). These tests simulate a
child that never responds to `communicate()`, not even after being killed, and assert the call still
returns within a small bounded wall-clock time instead of hanging - the thing that used to freeze the
whole poller (and every scheduled cycle after it) for 45+ minutes.

Run directly:
    python plugins/drainer/tests/test_run_subprocess_bounded.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")

sys.path.insert(0, SCRIPTS)
import provider_base  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _NeverRespondsPopen:
    """Simulates a child that never answers `communicate()` - not even after being killed. This is
    exactly the 2026-09-24 failure mode: a wedged node/ImapFlow child whose pipes stayed open long after
    `taskkill` reported success. Both the main-timeout call AND the post-kill drain call raise
    TimeoutExpired, so a correct implementation must give up and return rather than waiting forever."""

    def __init__(self, args, **kw):
        self.args = args
        self.pid = 4242
        self.returncode = None
        self.killed = False

    def communicate(self, input=None, timeout=None):
        raise subprocess.TimeoutExpired(self.args, timeout)

    def kill(self):
        self.killed = True


class _HappyPopen:
    """A normal, well-behaved child: returns immediately with output and an exit code."""

    def __init__(self, args, returncode=0, **kw):
        self.args = args
        self.pid = 1111
        self.returncode = returncode

    def communicate(self, input=None, timeout=None):
        return "stdout text", "stderr text"


def _patch(popen_cls, taskkill_calls=None):
    """Monkeypatch provider_base.subprocess.Popen (and .run, used only for the taskkill invocation) so
    no real child process or real taskkill.exe is ever spawned. Returns the restore function."""
    real_popen, real_run = provider_base.subprocess.Popen, provider_base.subprocess.run

    def fake_run(cmd, **kw):
        if taskkill_calls is not None:
            taskkill_calls.append(cmd)
        return _FakeCompleted(0)

    provider_base.subprocess.Popen = popen_cls
    provider_base.subprocess.run = fake_run

    def restore():
        provider_base.subprocess.Popen = real_popen
        provider_base.subprocess.run = real_run

    return restore


print("a child that never responds, even after being killed, still returns bounded - not hung")
taskkill_calls = []
restore = _patch(_NeverRespondsPopen, taskkill_calls)
try:
    start = time.monotonic()
    res = provider_base.run_subprocess_bounded(["node", "x.js"], timeout=0.05, drain_timeout=0.05)
    elapsed = time.monotonic() - start
finally:
    restore()

check("returns well within a bounded wall-clock time (would be infinite pre-fix)", elapsed < 5.0, True)
check("returncode reflects the failure", res.returncode, 1)
check("timed_out is stamped so a caller can tell this apart from an ordinary nonzero exit",
      getattr(res, "timed_out", False), True)
check("stderr explains the timeout", "timed out after 0.05s" in res.stderr, True)
check("the whole process tree was killed via taskkill /F /T, not just the immediate child",
      any(c[:4] == ["taskkill", "/F", "/T", "/PID"] for c in taskkill_calls), True)

print("\nrun_node (the thin wrapper adapters call) inherits the same bound")
restore = _patch(_NeverRespondsPopen)
try:
    start = time.monotonic()
    res = provider_base.run_node(["fake.js"], timeout=0.05)
    elapsed = time.monotonic() - start
finally:
    restore()
check("run_node also returns bounded instead of hanging", elapsed < 5.0, True)
check("run_node surfaces the same nonzero-exit contract adapters already check", res.returncode, 1)

print("\na well-behaved child on the normal (non-timeout) path is unaffected")
restore = _patch(lambda args, **kw: _HappyPopen(args, returncode=0, **kw))
try:
    res = provider_base.run_subprocess_bounded(["echo", "hi"], timeout=5)
finally:
    restore()
check("stdout/stderr/returncode pass through untouched", (res.stdout, res.stderr, res.returncode),
      ("stdout text", "stderr text", 0))
check("timed_out is absent on the normal path", getattr(res, "timed_out", False), False)

print("\ncheck=True raises CalledProcessError on a nonzero exit, mirroring subprocess.run(check=True)")
restore = _patch(lambda args, **kw: _HappyPopen(args, returncode=7, **kw))
try:
    try:
        provider_base.run_subprocess_bounded(["git", "fetch"], timeout=5, check=True)
        check("should have raised CalledProcessError", False, True)
    except subprocess.CalledProcessError as e:
        check("CalledProcessError carries the real exit code", e.returncode, 7)
finally:
    restore()

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
