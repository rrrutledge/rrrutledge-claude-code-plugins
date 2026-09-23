"""Manage the persistent chauffeur browser: bring it up, sweep it, tidy its tabs.

The entry point for the dedicated Edge/Chrome automation instance. Its default
mode launches or reuses the browser (and sweeps stale tabs on reuse); other
modes act on an already-running one (e.g. `--close-owned` closes this session's
tabs). A noun-named multitool, so every mode reads sensibly — not just launch.

Default (persistent) mode — launch or reuse:
    python chauffeur.py
    python chauffeur.py --url https://example.com

  Checks ~/.claude/browser-chauffeur/state.json for an already-running browser.
  If alive (CDP port responds), prints the existing info and exits. Otherwise
  launches a fresh browser with a persistent profile at
  ~/.claude/browser-chauffeur/profile/ so logins survive across tasks.

  The persistent browser stays running across Claude instances. DO NOT kill it
  manually - this preserves your logged-in sessions for future tasks.

Fresh mode (one-off, temporary browser):
    python chauffeur.py --fresh --port 9222 --url https://example.com

  Always launches a new browser with a unique timestamped profile.
  For --fresh mode only: caller is responsible for killing the PID and
  cleaning the profile when done.

Notes baked in (so callers don't have to remember them):
- Windows --user-data-dir requires backslash paths. Forward-slash Unix paths
  from Git Bash silently fail and the CDP port never binds — handled here by
  building the path with pathlib + str().
- Edge sidebar hijack: Edge with Microsoft 365 accounts has a built-in
  Teams/Chat sidebar that intercepts Teams URLs into a popup widget instead
  of a full-page tab. We disable it via --disable-features and pass the
  target URL as a positional argument so it opens as a real tab.
"""

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

EDGE_DISABLE_FEATURES = (
    "msEdgeSidebarV2,msEdgeSidebar,msEdgeChatAndNotification,"
    "msTeamsLeftChrome,EdgeSidebar,msEdgeSidebarPwaIntegration,"
    "msEdgeSyncPromoRollout,msEdgeWelcomePageEnabled"
)

# Global state directory shared across all Claude instances
CHAUFFEUR_DIR = Path.home() / ".claude" / "browser-chauffeur"
STATE_FILE = str(CHAUFFEUR_DIR / "state.json")
PERSISTENT_PROFILE = str(CHAUFFEUR_DIR / "profile")
# One file per open tab: tabs/<ownerPid>-<targetId>.json, with mtime as the
# tab's last-activity time. Owner and targetId live in the filename, so the
# sweep answers "whose session has ended" from a directory listing alone. See
# tab-registry.js for why the state is split per tab rather than kept in one
# shared file.
TABS_DIR = CHAUFFEUR_DIR / "tabs"

# Backstop tab hygiene. The primary cleanup is the owner reap — a tab is closed
# promptly when its owning session ends (see sweep_tabs). These two are the
# safety nets under that: TAB_TTL_SECONDS ages out a tab that's been idle a long
# time (default 12h — comfortably past a browser login and any lunch/afk gap, so
# it only catches genuinely abandoned tabs); MAX_TABS is a hard ceiling that
# closes the least-recently-active until back under it, so the browser can never
# accumulate enough tabs to crash (default 15 — well under the ~20 where the
# browser starts to struggle). Both are env-overridable without a code change.
TAB_TTL_SECONDS = int(os.environ.get("BROWSER_CHAUFFEUR_TAB_TTL", 12 * 60 * 60))
MAX_TABS = int(os.environ.get("BROWSER_CHAUFFEUR_MAX_TABS", 15))


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("localhost", port))
            return False
        except (ConnectionRefusedError, socket.timeout, OSError):
            return True


def find_free_port(start: int = 9222, count: int = 5) -> int | None:
    for port in range(start, start + count):
        if is_port_available(port):
            return port
    return None


ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87  # no process holds this PID
_ERROR_ACCESS_DENIED = 5       # a process holds it, but we may not ask about it

try:
    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _K32.CloseHandle.argtypes = [wintypes.HANDLE]
except (OSError, AttributeError):  # pragma: no cover - non-Windows
    _K32 = None


def probe_owner(pid: int) -> tuple[str, int | None]:
    """Ask the OS about a session's process: (state, start_time).

    Returns ALIVE with the process's creation time, DEAD when no process holds
    the PID, or UNKNOWN when the question can't be answered. Start time is what
    separates the real owner from an impostor: Windows recycles PIDs, so a PID
    being live is not evidence that the session which recorded it is still
    running — a later process can inherit the number. Creation time is unique
    per process, so comparing it catches the impostor.

    UNKNOWN is deliberately distinct from DEAD. A process we lack the rights to
    open still exists, and treating "I couldn't tell" as "the session ended"
    would reap a live session's tabs.

    Uses the Win32 API directly rather than spawning a process lister: the sweep
    asks this for every tab it knows about, and at roughly 4 microseconds a call
    that cost stays invisible however many tabs accumulate.
    """
    if _K32 is None:
        return UNKNOWN, None
    ctypes.set_last_error(0)
    handle = _K32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        if err == _ERROR_INVALID_PARAMETER:
            return DEAD, None
        if err == _ERROR_ACCESS_DENIED:
            return ALIVE, None  # exists; start time simply unavailable
        return UNKNOWN, None
    try:
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = _K32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                  ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return ALIVE, None
        return ALIVE, (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        _K32.CloseHandle(handle)


# A record that carries a start time (a tab stamped with one — the recycle guard
# for those) has it compared with slack rather than for equality, because the
# recorded value and the sweep's live reading can come from different runtimes.
# Both are FILETIMEs, but demanding they agree to the tick would make any rounding
# difference look like a recycled PID and reap every live session's tabs at once
# — a catastrophic answer to a cosmetic disagreement. Two seconds cannot hide a
# real recycle: the replacement process starts no earlier than the moment the
# original exited, so mistaking one for the other needs the original to have
# lived under two seconds, which a session process never does. Records that carry
# no start (the norm — ownership tracks CLAUDE_PID, whose liveness the sweep reads
# directly) fall through to matching on the PID alone.
START_TIME_SLACK_TICKS = 2 * 10**7  # FILETIME ticks are 100ns


def owner_has_ended(owner_pid: int, recorded_start: int | None) -> bool:
    """Is the session that recorded this tab definitely gone?

    True only on evidence: no process holds the PID, or one does but it started
    at a different time than the session we recorded, which makes it a different
    process wearing a recycled PID. Anything unproven answers False, so a tab is
    left to the idle and count layers rather than reaped on a guess.
    """
    state, start = probe_owner(owner_pid)
    if state == DEAD:
        return True
    if state == ALIVE and recorded_start is not None and start is not None:
        return abs(start - recorded_start) > START_TIME_SLACK_TICKS
    return False


# tabs/<ownerPid>-<targetId>.json — `unowned` for a tab nobody claimed.
TAB_NAME_RE = re.compile(r"^(unowned|\d+)-([0-9A-Fa-f]+)\.json$")

# Sentinel for "this field hasn't been read off disk yet", distinct from a
# record that genuinely has no start time.
_UNREAD = object()


def replace_atomically(tmp: Path, target: Path, attempts: int = 5) -> None:
    """Rename tmp over target, retrying briefly if Windows refuses.

    A rename onto a file another process currently has open fails on Windows.
    Left at one attempt that surfaces as a silently dropped write — the update
    never lands and the temp file is stranded — which is how a shared registry
    quietly loses records. Retrying covers the moment a concurrent reader holds
    the file; the caller removes the temp either way.
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp, target)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


class TabRecord:
    """One tab's file: owner and targetId from the name, activity from mtime."""

    __slots__ = ("path", "target_id", "owner_pid", "last_active_ms", "_owner_start")

    def __init__(self, path: Path, target_id: str, owner_pid: int | None, last_active_ms: int):
        self.path = path
        self.target_id = target_id
        self.owner_pid = owner_pid
        self.last_active_ms = last_active_ms
        self._owner_start = _UNREAD

    def content(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @property
    def owner_start(self) -> int | None:
        """The owning process's creation time, or None if it wasn't recorded.

        Lives in the file rather than the name so the layout is unchanged and a
        record written before this existed still reads cleanly — it simply has
        no start time, and ownership falls back to matching the PID alone.
        """
        if self._owner_start is _UNREAD:
            value = self.content().get("ownerStart")
            self._owner_start = value if isinstance(value, int) else None
        return self._owner_start

    def write(self, **updates) -> None:
        """Merge updates into this tab's own file.

        No lock: the file describes exactly one tab, so a racing writer is
        writing that same tab's current state rather than a list it could
        truncate. Reading first preserves fields this writer doesn't set.
        """
        data = self.content()
        data.update({"targetId": self.target_id, "ownerPid": self.owner_pid}, **updates)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            replace_atomically(tmp, self.path)
        except OSError:
            pass
        finally:
            # A rename that never happened would otherwise strand the temp file
            # on disk for good.
            try:
                tmp.unlink()
            except OSError:
                pass
        self._owner_start = _UNREAD

    def bump(self) -> None:
        """Mark active without rewriting content."""
        try:
            os.utime(self.path)
        except OSError:
            pass

    def remove(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def read_tabs() -> list[TabRecord]:
    """Every recorded tab. Missing directory reads as no tabs."""
    records = []
    try:
        names = os.listdir(TABS_DIR)
    except OSError:
        return records
    for name in names:
        m = TAB_NAME_RE.match(name)
        if not m:
            continue
        p = TABS_DIR / name
        try:
            mtime_ms = int(p.stat().st_mtime * 1000)
        except OSError:
            continue  # vanished between listing and stat
        owner = None if m.group(1) == "unowned" else int(m.group(1))
        records.append(TabRecord(p, m.group(2), owner, mtime_ms))
    return records


def record_for(target_id: str, owner_pid: int | None) -> TabRecord:
    name = f"{'unowned' if owner_pid is None else owner_pid}-{target_id}.json"
    return TabRecord(TABS_DIR / name, target_id, owner_pid, int(time.time() * 1000))


def _resolve_owner_pid() -> int | None:
    """This session's tab-owner pid, resolved exactly as the open path records it
    (tab-registry.js ownerInfo): CLAUDE_PID, the session's own claude process,
    which owns every session's tabs. None when it is unset (not running under a
    Claude session), so --close-owned has nothing it can match. The node-pid
    fallback the open path uses last has no analog here: this process's pid could
    not match the node process that recorded the tab, so a bare invocation outside
    a Claude session simply owns nothing to close."""
    raw = os.environ.get("CLAUDE_PID")
    if raw:
        try:
            pid = int(raw)
        except ValueError:
            return None
        if pid > 0:
            return pid
    return None


def is_cdp_alive(port: int) -> bool:
    try:
        resp = urlopen(f"http://localhost:{port}/json/version", timeout=2)
        data = json.loads(resp.read())
        return "Browser" in data
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return False


def sweep_tabs(port: int) -> None:
    """Keep the persistent browser's tab count low and healthy.

    Reclaiming is driven by idleness; ownership only lets us clean up promptly
    instead of waiting. Three layers, applied in order:
      1. Owner reap (the courtesy) — a tab whose owning session has ended is
         closed right away, rather than waiting for it to age out or become the
         idlest under the cap. The owner is the Claude session that opened the
         tab (its own claude process, CLAUDE_PID), so a tab lives as long as that
         session runs and is cleaned up when it ends. A tab opened without the
         openTab helper has no owner — it's cleaned up by layers 2–3 instead.
      2. Age-out (TTL) — any tab idle (no activity) longer than TAB_TTL_SECONDS
         is closed, regardless of owner, catching genuinely abandoned tabs.
         Activity = a tab created/reused via the openTab/findTab helpers, or a
         URL/title change the sweep notices between runs. Tabs opened without
         openTab are adopted on first sight so their idleness can be tracked.
      3. Count cap — if more than MAX_TABS remain, close the least-recently-
         active until back under the ceiling. Pure idleness, ownership-agnostic:
         an actively-used tab has recent activity so it's never the one closed.

    Never touched: the browser's last remaining page (closing it would exit the
    browser and lose all logins). This is a dedicated automation browser
    separate from the user's personal browser, so idle tabs carry no user
    browsing to protect. Uses the raw CDP HTTP endpoints (/json, /json/close),
    which never auto-attach to targets and so never hang the way
    connectOverCDP can.

    Best-effort: any error here is swallowed so it can't block a launch.
    """
    now_ms = int(time.time() * 1000)

    # Stamped before the request, not after: a tab recorded while we were asking
    # for the target list isn't in the answer, so treating its file as stale
    # would strip a brand-new tab of its owner.
    snapshot_ms = int(time.time() * 1000)
    try:
        resp = urlopen(f"http://localhost:{port}/json", timeout=5)
        targets = json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError):
        return

    live = {t["id"]: t for t in targets if t.get("type") == "page" and "id" in t}
    if not live:
        return

    recs: dict[str, TabRecord] = {}
    for rec in read_tabs():
        if rec.target_id in live:
            # A tab can briefly have both an owned file and an `unowned` one, if
            # a sweep adopted it in the window before its session recorded it.
            # The owned file is the truth; the loser is left to be cleaned up as
            # a stale file once the tab closes.
            prev = recs.get(rec.target_id)
            if prev is None or (prev.owner_pid is None and rec.owner_pid is not None):
                recs[rec.target_id] = rec
        elif rec.last_active_ms < snapshot_ms:
            # The tab is gone and its file predates the snapshot, so it is
            # genuinely stale. A file newer than the snapshot belongs to a tab
            # opened after we looked — leave it be.
            rec.remove()

    # Passive activity signal: a tab whose URL or title changed since we last
    # looked was navigated or worked in between sweeps, so refresh its activity
    # time. This needs no cooperation from the scripts that touched the tab.
    # Writing here is safe without a lock because each file holds one tab, and
    # the value written is that tab's current URL either way.
    for tid, rec in recs.items():
        cur = live[tid]
        recorded = rec.content()
        if recorded.get("url") != cur.get("url", "") or recorded.get("title") != cur.get("title", ""):
            rec.write(url=cur.get("url", ""), title=cur.get("title", ""))
            rec.last_active_ms = now_ms

    # Adopt any live tab nobody recorded (opened without openTab, or by the
    # user), so its idleness can be tracked from first sight. Re-list first: a
    # session may have recorded one of these tabs while the work above was in
    # flight, and adopting it would leave a second, ownerless file for it.
    recorded_ids = {r.target_id for r in read_tabs()}
    for tid, t in live.items():
        if tid in recs or tid in recorded_ids:
            continue
        rec = record_for(tid, None)
        rec.write(url=t.get("url", ""), title=t.get("title", ""))
        recs[tid] = rec

    open_ids = set(live)

    def close(rec: TabRecord) -> bool:
        # Never close the browser's last page — that would exit the browser.
        if len(open_ids) <= 1:
            return False
        try:
            urlopen(f"http://localhost:{port}/json/close/{rec.target_id}", timeout=5).read()
        except (URLError, OSError):
            return False
        open_ids.discard(rec.target_id)
        rec.remove()
        return True

    # Answered once per owner, since many tabs usually share a session.
    ended_cache: dict[tuple[int, int | None], bool] = {}

    def owner_ended(rec: TabRecord) -> bool:
        if rec.owner_pid is None:
            return False  # nobody claimed it; layers 2 and 3 handle it
        key = (rec.owner_pid, rec.owner_start)
        if key not in ended_cache:
            ended_cache[key] = owner_has_ended(rec.owner_pid, rec.owner_start)
        return ended_cache[key]

    closed = 0
    # Layers 1 & 2 — owner reap (session ended) + age-out (idle past TTL),
    # least-recently-active first. Both apply regardless of whether some other
    # session is alive: a tab is reclaimed on its owner ending OR on its own
    # idleness, never held open just because unrelated sessions are running.
    for rec in sorted(recs.values(), key=lambda r: r.last_active_ms):
        if rec.target_id not in open_ids:
            continue
        is_idle = (now_ms - rec.last_active_ms) >= TAB_TTL_SECONDS * 1000
        if owner_ended(rec) or is_idle:
            if close(rec):
                closed += 1

    # Layer 3 — hard ceiling. Evict by how much the tab is worth keeping, not by
    # idleness alone: a tab nobody claimed has no session depending on it, while
    # one owned by a running session may be mid-flow in work the sweep can't
    # see. So unclaimed tabs go first and a live session's tabs are touched only
    # if the browser is still over the ceiling afterwards. Within each tier the
    # least-recently-active goes first, which is what protects a tab you are
    # using — including one you opened by hand, since interacting with it moves
    # its URL or title and the sweep bumps its activity.
    def eviction_order(rec: TabRecord) -> tuple[int, int]:
        return (0 if rec.owner_pid is None else 1, rec.last_active_ms)

    for rec in sorted(recs.values(), key=eviction_order):
        if len(open_ids) <= MAX_TABS:
            break
        if rec.target_id not in open_ids:
            continue
        if close(rec):
            closed += 1

    if closed:
        print(f"Swept {closed} tab(s) to keep the browser healthy.")


def load_state() -> dict | None:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def save_state(pid: int, port: int, profile_dir: str) -> None:
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"pid": pid, "port": port, "profile_dir": profile_dir}, f)


def close_owned_tabs() -> int:
    """Close every tab owned by the current session — level-1 self-cleanup.

    A session's final courtesy: close its own browser tabs when it's genuinely
    done with them, so they never reach the sweep. Matches tabs whose recorded
    owner is this session's CLAUDE_PID — the session's own claude process, the same
    owner the open path (tab-registry.js ownerInfo) records. Uses the raw CDP HTTP
    endpoint (never auto-attaches, can't hang), only ever touches this session's
    own tabs, and never closes the browser's last remaining page. Best-effort.
    """
    owner = _resolve_owner_pid()
    if owner is None:
        print("No CLAUDE_PID set (not running under a Claude session) — nothing owned to close.")
        return 0

    state = load_state()
    if not state or not is_cdp_alive(state["port"]):
        print("No live browser — nothing to close.")
        return 0
    port = state["port"]

    try:
        resp = urlopen(f"http://localhost:{port}/json", timeout=5)
        targets = json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError):
        return 0
    live = {t["id"] for t in targets if t.get("type") == "page" and "id" in t}
    open_count = len(live)

    closed = 0
    for rec in read_tabs():
        if rec.owner_pid != owner:
            continue  # another session's tab, or one nobody claimed
        if rec.target_id not in live:
            rec.remove()  # my tab, already gone
            continue
        # Close my own tabs, but never the browser's last remaining page.
        if open_count <= 1:
            continue
        try:
            urlopen(f"http://localhost:{port}/json/close/{rec.target_id}", timeout=5).read()
        except (URLError, OSError):
            continue
        rec.remove()
        closed += 1
        open_count -= 1

    print(f"Closed {closed} tab(s) owned by this session.")
    return 0


def launch_browser(port: int, url: str, profile_dir: str) -> tuple[str, int]:
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    common_args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--suppress-message-center-popups",
        "--start-maximized",
        url,
    ]

    if os.path.isfile(EDGE_PATH):
        cmd = [EDGE_PATH, f"--disable-features={EDGE_DISABLE_FEATURES}", *common_args]
        browser_name = "Edge"
    elif os.path.isfile(CHROME_PATH):
        cmd = [CHROME_PATH, *common_args]
        browser_name = "Chrome"
    else:
        print("No supported browser found (Edge or Chrome)", file=sys.stderr)
        sys.exit(1)

    proc = subprocess.Popen(cmd)
    return browser_name, proc.pid


def run_persistent(url: str) -> int:
    state = load_state()
    if state:
        pid, port, profile_dir = state["pid"], state["port"], state["profile_dir"]
        # Edge process sharing can change PIDs - if CDP responds, browser is alive
        if is_cdp_alive(port):
            # Reap orphaned, aged-out, and over-the-cap tabs before handing the
            # browser back — keeps the target count low so connectOverCDP stays
            # healthy and the browser can't accumulate enough tabs to crash.
            sweep_tabs(port)
            print(f"Reusing existing browser on port {port} (original PID {pid})")
            print(f"PID={pid}")
            print(f"PORT={port}")
            print(f"PROFILE_DIR={profile_dir}")
            return 0
        elif probe_owner(pid)[0] != DEAD:
            print(f"Warning: PID {pid} running but CDP port {port} not responding. Launching new browser.")
            # PID exists but CDP dead - likely a different process reused the PID

    port = find_free_port()
    if port is None:
        print("No available port in range 9222-9226", file=sys.stderr)
        return 1

    browser_name, pid = launch_browser(port, url, PERSISTENT_PROFILE)

    # Verify CDP port actually bound (Edge process sharing can prevent it)
    print(f"Launched {browser_name} (PID {pid}), waiting for CDP on port {port}...")
    time.sleep(3)  # Give browser time to start

    if not is_cdp_alive(port):
        print(f"\n[!] ERROR: CDP port {port} did not bind!", file=sys.stderr)
        print("This usually happens when Edge process sharing prevents --remote-debugging-port.", file=sys.stderr)
        print("\nTroubleshooting:", file=sys.stderr)
        print("1. Close ALL Edge windows (personal browser too)", file=sys.stderr)
        print("2. Run: taskkill /F /IM msedge.exe", file=sys.stderr)
        print("3. Try launching again", file=sys.stderr)
        print(f"\nIf that doesn't work, try --fresh mode with a different port:", file=sys.stderr)
        print(f"  python chauffeur.py --fresh --port 9226 --url {url}", file=sys.stderr)
        return 1

    save_state(pid, port, PERSISTENT_PROFILE)

    print(f"[OK] {browser_name} on port {port} (PID {pid})")
    print(f"PID={pid}")
    print(f"PORT={port}")
    print(f"PROFILE_DIR={PERSISTENT_PROFILE}")
    return 0


def run_fresh(port: int, url: str, profile_dir: str | None) -> int:
    if profile_dir is None:
        profile_dir = str(Path.cwd() / ".tmp" / f"cdp-profile-{int(time.time())}")

    browser_name, pid = launch_browser(port, url, profile_dir)

    print(f"Launched {browser_name} on port {port} (PID {pid})")
    print(f"PID={pid}")
    print(f"PORT={port}")
    print(f"PROFILE_DIR={profile_dir}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fresh", action="store_true",
                   help="Launch a one-off browser with a unique profile (old behavior)")
    p.add_argument("--port", type=int, default=None,
                   help="CDP port (required for --fresh, ignored in persistent mode)")
    p.add_argument("--url", default="about:blank",
                   help="Initial URL (default: about:blank)")
    p.add_argument("--profile-dir", default=None,
                   help="Profile directory (--fresh only; auto-generated if omitted)")
    p.add_argument("--close-owned", action="store_true",
                   help="Close all tabs owned by this session (its CLAUDE_PID) and exit")
    args = p.parse_args()

    if args.close_owned:
        return close_owned_tabs()

    if args.fresh:
        if args.port is None:
            print("--port is required with --fresh", file=sys.stderr)
            return 1
        return run_fresh(args.port, args.url, args.profile_dir)

    return run_persistent(args.url)


if __name__ == "__main__":
    sys.exit(main())
