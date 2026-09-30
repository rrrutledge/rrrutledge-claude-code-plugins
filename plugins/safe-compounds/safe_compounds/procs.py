"""Live Windows process-ancestry lookup, used to prove a `taskkill /PID` target
is really "the terminal tab hosting this session" rather than trusting an
argument value.

Pure ctypes against kernel32's toolhelp snapshot API — no third-party
dependency (psutil may or may not be installed on a given machine; stdlib
always is). Any failure (wrong platform, API error, unexpected data, or an
ancestry shape we don't recognize) returns None so callers fail closed to a
manual prompt instead of guessing.
"""
import ctypes
import os
from ctypes import wintypes

TH32CS_SNAPPROCESS = 0x00000002
MAX_ANCESTOR_DEPTH = 32
# The Claude Code CLI's own process name — the anchor we climb from. Everything
# below this in the chain (the hook's shell wrapper, etc.) is uninteresting;
# everything above it (the WindowsTerminal.exe container, services.exe, ...) is
# shared infrastructure a single session must never be allowed to kill.
CLAUDE_PROCESS_NAME = 'claude.exe'


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_char * 260),
    ]


def _snapshot():
    """Return {pid: (ppid, lowercase_exe_name)} for every running process, or
    {} on failure (wrong platform, API error)."""
    if os.name != 'nt':
        return {}
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, -1):
        return {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        mapping = {}
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            name = entry.szExeFile.decode('mbcs', errors='replace').lower()
            mapping[entry.th32ProcessID] = (entry.th32ParentProcessID, name)
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
        return mapping
    finally:
        kernel32.CloseHandle(snapshot)


def self_tab_host_pid(start_pid=None, max_depth=MAX_ANCESTOR_DEPTH, snapshot=None):
    """Return the PID of the process that hosts THIS session's terminal
    tab/pane — the immediate parent of this session's own `claude.exe` process
    — or None if it can't be determined.

    This is the only PID a `taskkill` may safely self-target: never
    `claude.exe` itself (that's a snapshot key, not a host), never
    WindowsTerminal.exe (shared across every tab in the window), and never
    anything further up (services.exe, wininit.exe, the System process).
    Walks upward from `start_pid` (default: this process) live via the OS
    process table so the answer reflects current reality, not a cached or
    supplied value.
    """
    try:
        snap = snapshot if snapshot is not None else _snapshot()
        if not snap:
            return None
        current = os.getpid() if start_pid is None else start_pid
        seen = {current}
        for _ in range(max_depth):
            entry = snap.get(current)
            if not entry:
                return None
            ppid, name = entry
            if name == CLAUDE_PROCESS_NAME:
                return ppid if ppid and ppid not in seen else None
            if ppid == 0 or ppid in seen:
                return None
            seen.add(ppid)
            current = ppid
        return None
    except Exception:
        return None


def _find_ancestor_claude_pid(start_pid, max_depth, snap):
    """Walk upward from `start_pid` and return the PID of the nearest
    claude.exe ancestor (this session's own CLI process), or None if the
    chain breaks, cycles, or exceeds `max_depth` before finding one."""
    current = start_pid
    seen = {current}
    for _ in range(max_depth):
        entry = snap.get(current)
        if not entry:
            return None
        ppid, name = entry
        if name == CLAUDE_PROCESS_NAME:
            return current
        if ppid == 0 or ppid in seen:
            return None
        seen.add(ppid)
        current = ppid
    return None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_creation_time(pid):
    """Return `pid`'s creation time as a single comparable integer (the raw
    FILETIME), or None on any failure (exited process, access denied, wrong
    platform). Used to guard against Windows PID reuse — see
    `is_own_descendant`."""
    if os.name != 'nt':
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time))
            if not ok:
                return None
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _lookup_creation_time(pid, creation_times):
    if creation_times is not None:
        return creation_times.get(pid)
    return _process_creation_time(pid)


def is_own_descendant(pid, start_pid=None, max_depth=MAX_ANCESTOR_DEPTH,
                       snapshot=None, creation_times=None):
    """True only if `pid` is a live descendant of THIS session's own
    claude.exe process — proven via a live OS process-ancestry walk plus a
    creation-time check on every hop, not by trusting the PID itself.

    `pid` must never resolve to this session's claude.exe itself, or to
    anything above it (a terminal tab host, WindowsTerminal.exe,
    services.exe, ...) — walking up from `pid` must reach claude.exe as a
    strict ancestor, or the check fails.

    Windows recycles PIDs: once a process exits, its PID can be reassigned to
    an unrelated process, which would otherwise let a stale ancestry chain
    "prove" a false parentage (a dead parent's old PID handed to a new,
    unrelated process). A real parent is always created before its child, so
    every ancestor hop additionally requires
    creation_time(ancestor) <= creation_time(child); a violation, or any
    creation-time lookup failure, fails closed — not a descendant.

    `creation_times`, like `snapshot`, is an injectable {pid: FILETIME-int}
    mapping for tests; production callers leave it None to query the OS live.

    Known gap: a process the Bash tool detached (`run_in_background`) has its
    immediate spawning wrapper exit right after launch, so by the time this
    check runs, that wrapper's PID is already gone from the process table —
    the chain is genuinely, unrecoverably broken (Windows doesn't reparent
    orphans), not just racy. Such a PID fails closed to a manual prompt even
    though it did originate in this session. A still-live idle shell/conhost
    child — the Stop-hook guard's actual target — keeps its link to
    claude.exe intact and is unaffected.
    """
    try:
        snap = snapshot if snapshot is not None else _snapshot()
        if not snap:
            return False
        anchor = os.getpid() if start_pid is None else start_pid
        claude_pid = _find_ancestor_claude_pid(anchor, max_depth, snap)
        if claude_pid is None or pid == claude_pid:
            return False

        current = pid
        seen = {current}
        child_time = _lookup_creation_time(current, creation_times)
        if child_time is None:
            return False
        for _ in range(max_depth):
            entry = snap.get(current)
            if not entry:
                return False
            ppid, _name = entry
            if ppid == 0 or ppid in seen:
                return False
            parent_time = _lookup_creation_time(ppid, creation_times)
            if parent_time is None or parent_time > child_time:
                return False
            if ppid == claude_pid:
                return True
            seen.add(ppid)
            current = ppid
            child_time = parent_time
        return False
    except Exception:
        return False
