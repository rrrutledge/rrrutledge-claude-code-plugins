"""Append one worker lesson to the ledger - the "record only" half of worker-core step 7.

`engine/worker-core.md` step 7 says when a worker runs this and with which flags.

It stamps the time, the session id (`CLAUDE_CODE_SESSION_ID`, set for the Bash tool's children),
and the item id and source (read from `--item`, the captured item json the worker's seed names:
the id is the file's stem, the source its `source` field), then appends one JSON line to
`<dir>/log-<YYYY-MM>.jsonl`. The dir is `$WORKER_LESSONS_DIR`, else `~/OneDrive/Claude/worker-lessons`.

Schema: ts, session_id, iid, source, trigger, what, target, change, pr (null when absent).
The weekly State of the Pod review reads the ledger.
"""
import argparse
import datetime
import json
import os
import sys

TRIGGERS = ("correction", "dead-end", "retries")


def ledger_dir():
    return os.environ.get("WORKER_LESSONS_DIR") or os.path.expanduser("~/OneDrive/Claude/worker-lessons")


def item_identity(item_path):
    """(iid, source) for a captured item json, or (None, None) when it isn't given or unreadable."""
    if not item_path:
        return None, None
    iid = os.path.splitext(os.path.basename(item_path))[0]
    try:
        with open(item_path, encoding="utf-8") as f:
            source = (json.load(f) or {}).get("source")
    except (OSError, ValueError):
        source = None
    return iid, source


def build_record(args, now=None):
    now = now or datetime.datetime.now().astimezone()
    iid, source = item_identity(args.item)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "iid": iid,
        "source": source,
        "trigger": args.trigger,
        "what": args.what,
        "target": args.target,
        "change": args.change,
        "pr": args.pr,
    }


def append(record, directory=None):
    directory = directory or ledger_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"log-{record['ts'][:7]}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Append one worker lesson to the ledger.")
    parser.add_argument("--trigger", required=True, choices=TRIGGERS)
    parser.add_argument("--what", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument("--pr")
    parser.add_argument("--item", help="path to the captured items/<id>.json, for the item id and source")
    args = parser.parse_args(argv)
    path = append(build_record(args))
    print(f"record-lesson: appended to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
