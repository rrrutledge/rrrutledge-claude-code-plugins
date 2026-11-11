"""The drainer's long-running keeper: one process that dispatches about once a minute and runs the feed
cycle beside it, then exits cleanly so the scheduled task starts a fresh copy.

The `DrainerKeeper` scheduled task starts the poller every 5 minutes and ignores a start while one is
running. The poller's scheduled form now runs this keeper, so the task is its watchdog: a keeper that is
alive makes the task's start a no-op, and one that died or recycled is back within 5 minutes. The keeper
also holds a single-instance lock, so a second start from any other path exits at once.

Two threads:
  - the dispatch loop (the main thread) runs `dispatch()` every `dispatch_interval` seconds, and at once
    whenever a feed cycle finishes, so new items wait seconds for a slot instead of a whole cycle;
  - the feed thread runs the feed command as a subprocess, starting each cycle `feed_interval` seconds
    after the previous one started, or straight away when one ran longer than that, so an overrun never
    makes the next cycle skip. A cycle that runs past `feed_timeout` is killed and the next one starts.

The keeper recycles (stops starting feeds, waits for the running one, releases the lock, exits 0) when it
has run `max_age` seconds, when its memory passes `max_rss_mb`, or when `should_recycle()` says so (the
poller passes a check that a newer drainer version has been installed).
"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

LOCK_FILE = "keeper.lock"
LOG_FILE = "keeper.log"
LOG_MAX_BYTES = 1_000_000


class InstanceLock:
    """An exclusive lock on a file, held for the life of the process. The operating system releases it
    when the process ends, however it ends, so a crashed keeper never leaves a stale lock behind."""

    def __init__(self, path):
        self.path = path
        self.fh = None

    def acquire(self, timeout=0.0):
        """True once the lock is held. Waits up to `timeout` seconds for another holder to let go."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, "a+")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock(fh)
                self.fh = fh
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    fh.close()
                    return False
                time.sleep(0.2)

    def release(self):
        if self.fh:
            try:
                _unlock(self.fh)
            except OSError:
                pass
            self.fh.close()
            self.fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


if os.name == "nt":
    import msvcrt

    def _lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def rss_mb():
    """This process's resident memory in MB, or None where it can't be read."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(get_process(), ctypes.byref(c), c.cb):
            return c.WorkingSetSize / 1_048_576
        return None
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except (ImportError, OSError):
        return None


class Log:
    """Timestamped lines appended to `<runtime_dir>/keeper.log`, started over once it passes 1 MB.
    The scheduled keeper runs under pythonw with no console, so this file is the one record of what it did."""

    def __init__(self, path):
        self.path = path
        self.mu = threading.Lock()

    def __call__(self, msg):
        line = f"{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} {msg}\n"
        with self.mu:
            try:
                if os.path.getsize(self.path) > LOG_MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


def run(runtime_dir, dispatch, feed_cmd, *, dispatch_interval=60, feed_interval=300, feed_timeout=1800,
        max_age=4 * 3600, max_rss_mb=600, should_recycle=None, clock=time.monotonic, log=None,
        popen=subprocess.Popen):
    """Run the keeper until it recycles. Returns "locked" when another keeper holds the lock, else the
    recycle reason."""
    log = log or Log(os.path.join(runtime_dir, LOG_FILE))
    lock = InstanceLock(os.path.join(runtime_dir, LOCK_FILE))
    if not lock.acquire():
        return "locked"
    started = clock()
    stop = threading.Event()
    wake = threading.Event()
    reason = []
    log(f"keeper started (pid {os.getpid()})")

    def feed_loop():
        while not stop.is_set():
            t0 = clock()
            try:
                proc = popen(feed_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    code = proc.wait(timeout=feed_timeout)
                    log(f"feed cycle exited {code} after {clock() - t0:.0f}s")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    log(f"feed cycle killed after {feed_timeout}s")
            except Exception as e:  # a failed start is retried next interval, never fatal
                log(f"feed cycle failed to start: {e}")
            wake.set()  # dispatch the items this cycle just added
            stop.wait(max(0.0, feed_interval - (clock() - t0)))

    feeder = threading.Thread(target=feed_loop, name="drainer-feed", daemon=True)
    feeder.start()
    try:
        while True:
            try:
                dispatch()
            except Exception as e:  # one bad pass is logged and the next one runs on schedule
                log(f"dispatch pass failed: {type(e).__name__}: {e}")
            age = clock() - started
            mem = rss_mb()
            if age >= max_age:
                reason.append(f"ran {age / 3600:.1f}h")
            elif mem is not None and mem >= max_rss_mb:
                reason.append(f"memory {mem:.0f} MB")
            elif not feeder.is_alive():
                reason.append("feed thread ended")
            else:
                try:
                    if should_recycle and should_recycle():
                        reason.append("a newer drainer version is installed")
                except Exception as e:
                    log(f"recycle check failed: {e}")
            if reason:
                break
            wake.wait(dispatch_interval)
            wake.clear()
    finally:
        stop.set()
        # Let a running feed cycle finish rather than cut it off, and keep dispatching while it does.
        wait_until = clock() + feed_timeout
        while feeder.is_alive() and clock() < wait_until:
            feeder.join(timeout=dispatch_interval)
            if feeder.is_alive() and reason:
                try:
                    dispatch()
                except Exception as e:
                    log(f"dispatch pass failed: {type(e).__name__}: {e}")
        why = reason[0] if reason else f"stopped by {sys.exc_info()[0].__name__ if sys.exc_info()[0] else 'exit'}"
        log(f"keeper recycling: {why}")
        lock.release()
    return reason[0]
