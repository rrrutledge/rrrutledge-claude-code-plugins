"""The drainer's pending list: every judged item that needs a worker, held on disk until one opens.

Discovery (the poller's feed cycle) writes each needs-you and auto-handle item here once it has its triage
and screen verdicts. The dispatch loop reads the list, opens workers for the top items as slots free up, and
removes an item the moment it is recorded seen.

One file per item, under `<runtime_dir>/pending/`, so the feed and the dispatch loop never rewrite each
other's entries: the feed replaces one item's file, the dispatcher deletes one, and neither reads and
rewrites a shared file. Each write goes to a temp file first and then replaces the target, so a reader
never sees half an entry. A missing or unreadable entry reads as absent: the next feed cycle finds the
item again in its source and writes it back, so a lost entry costs one cycle, never the item.

An entry is the item dict exactly as the poller holds it (its `_id`, `_source`, `_bucket`, `_screen` and
the other `_` fields included), plus `_pendingSince`, the time it first entered the list.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone

PENDING_DIR = "pending"


def _dir(runtime_dir):
    return os.path.join(runtime_dir, PENDING_DIR)


def _path(runtime_dir, source, iid):
    # Item ids carry characters a filename can't (`:`, `/`, `|`), so the name is a digest of the key.
    name = hashlib.sha1(f"{source}\0{iid}".encode("utf-8")).hexdigest()[:24]
    return os.path.join(_dir(runtime_dir), f"{name}.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("_id") and data.get("_source") else None


def _replace(tmp, path):
    # On Windows a replace fails while another process has the target open for a read; that read is
    # over in milliseconds, so a few short retries cover it.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def get(runtime_dir, source, iid):
    """The pending entry for one item, or None."""
    return _read(_path(runtime_dir, source, iid))


def put(runtime_dir, item):
    """Store or refresh one item's entry. Keeps the `_pendingSince` of an earlier entry, and skips the
    write when nothing changed, so a feed cycle re-finding a held item costs a read, not a write."""
    path = _path(runtime_dir, item["_source"], item["_id"])
    old = _read(path)
    entry = json.loads(json.dumps(item, default=str))  # the form it reads back in, so the compare is like for like
    entry["_pendingSince"] = (old or {}).get("_pendingSince") or datetime.now(timezone.utc).isoformat()
    if old == entry:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f)
    _replace(tmp, path)
    return True


def remove(runtime_dir, source, iid):
    # A delete fails on Windows while another process is reading the file, as a replace does (_replace).
    for attempt in range(5):
        try:
            os.remove(_path(runtime_dir, source, iid))
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def load_all(runtime_dir):
    """Every readable pending entry, in no particular order (the dispatcher orders them)."""
    try:
        names = os.listdir(_dir(runtime_dir))
    except OSError:
        return []
    out = []
    for fn in names:
        if fn.endswith(".json"):
            entry = _read(os.path.join(_dir(runtime_dir), fn))
            if entry:
                out.append(entry)
    return out


def prune(runtime_dir, source, still_pending_ids, entries):
    """Drop every one of `entries` (load_all's list) of `source` whose id is not in `still_pending_ids`,
    the ids that source listed as new (unseen) this cycle - empty when it failed to list. Returns the
    count removed."""
    removed = 0
    for e in entries:
        if e["_source"] == source and e["_id"] not in still_pending_ids:
            removed += remove(runtime_dir, source, e["_id"])
    return removed
