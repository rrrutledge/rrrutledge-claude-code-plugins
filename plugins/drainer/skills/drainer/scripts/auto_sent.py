"""Everything the pod sent without Russell since the last digest, rendered as the digest's fixed section.

send-confidence is the one gate an unreviewed send passes through: whatever goes out on the model's
say-so is logged there with `disposition: "auto-sent"` (`record_auto_sent`), by the script that sent it,
at the moment it sent. So this reads that log, and every auto-sent action reaches the digest whether or
not its worker closed out an item, and whatever kind of send it was.

The digest launcher (`run-digest.py`) renders the section into the digest seed, which the session
presents verbatim. A send undone since (`slack_reaction.py undo`) is listed as undone. The section runs
from a watermark in `<runtime_dir>/auto-sent-reported.json` through the newest send it lists, and the
watermark moves only when the digest session runs `reported` once Russell has seen the section, so a
digest that never reaches him leaves its sends for the next one.
A queued auto-handle item whose `autoSent` list names one of these sends and whose `dispositionReason` is
empty did nothing else, so the seed names it and the digest leaves it out of Auto-handled.

CLI:
    python auto_sent.py reported <runtime_dir> <through>     # advance the watermark to <through>
"""
import datetime
import json
import os
import sys

WATERMARK_FILE = "auto-sent-reported.json"
FIRST_RUN_LOOKBACK = datetime.timedelta(days=7)
NOTHING = "Sent without you since the last digest: nothing."


def _parse(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def read_watermark(runtime_dir, now):
    """The newest send the last seen digest listed; a week before `now` when no digest has listed one."""
    try:
        with open(os.path.join(runtime_dir, WATERMARK_FILE), encoding="utf-8") as f:
            through = _parse(json.load(f).get("through"))
    except (OSError, ValueError, AttributeError):
        through = None
    return through or now - FIRST_RUN_LOOKBACK


def write_watermark(runtime_dir, through):
    os.makedirs(runtime_dir, exist_ok=True)
    with open(os.path.join(runtime_dir, WATERMARK_FILE), "w", encoding="utf-8") as f:
        json.dump({"through": through}, f)


def collect(sc, since):
    """Each send logged auto-sent after `since`, oldest first, with whether it was undone since."""
    events = sc.load_events()
    drafts = sc.fold_drafts(events)
    out = []
    for e in events:
        if e.get("event") != "outcome" or e.get("disposition") != "auto-sent":
            continue
        ts = _parse(e.get("ts"))
        if not ts or ts <= since:
            continue
        d = drafts.get(e.get("draft_id")) or {}
        latest = d.get("outcome") or {}
        out.append({"draft_id": e["draft_id"], "ts": ts, "raw_ts": e["ts"], "scored": d.get("scored") or {},
                    "undone": latest.get("disposition") == "discarded"})
    return sorted(out, key=lambda s: s["ts"])


def describe(sent, undo_command):
    """One line for a send: when, what went where, and how to undo it (or that it was undone)."""
    scored = sent["scored"]
    when = sent["ts"].astimezone().strftime("%b %d, %I:%M %p %Z").replace(" 0", " ")
    r = scored.get("reaction")
    if r:
        what = f"Reacted :{r.get('emoji')}: on {r.get('permalink')}"
        undo = f"Undo: `{undo_command(sent['draft_id'])}`"
    else:
        what = (f"Sent a {scored.get('message_type') or 'message'} to {scored.get('recipient') or '?'} "
                f"on {scored.get('channel') or '?'}")
        undo = f"No undo; draft {sent['draft_id']}"
    return f"- {when}: {what}" + (" - undone since." if sent["undone"] else f". {undo}")


def covered_queue_ids(queue, sends):
    """The queued auto-handle items whose only action was one of these sends."""
    ids = {s["draft_id"] for s in sends}
    covered = []
    for entry in queue:
        item = entry.get("item") or {}
        if (item.get("triage") == "auto-handle" and ids & set(item.get("autoSent") or [])
                and not (item.get("dispositionReason") or "").strip()):
            covered.append(entry.get("id"))
    return covered


def render(sends, covered, undo_command):
    """The seed's block for the digest's "Sent without you" section."""
    if not sends:
        return NOTHING
    lines = [f"Sent without you since the last digest ({len(sends)}):"]
    lines += ["    " + describe(s, undo_command) for s in sends]
    if covered:
        lines.append("    Queue items repeating these sends: " + ", ".join(covered))
    return "\n".join(lines)


def section(sc, runtime_dir, queue, undo_command):
    """The rendered block, and the timestamp of the newest send it lists for `reported` to save (None
    when it lists none). With send-confidence missing nothing could have gone out on its own."""
    if not sc:
        return NOTHING, None
    sends = collect(sc, read_watermark(runtime_dir, datetime.datetime.now().astimezone()))
    return render(sends, covered_queue_ids(queue, sends), undo_command), (sends[-1]["raw_ts"] if sends else None)


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "reported" or not _parse(sys.argv[3]):
        raise SystemExit("usage: auto_sent.py reported <runtime_dir> <through>")
    write_watermark(sys.argv[2], sys.argv[3])
    print(f"Sent without you reported through {sys.argv[3]}.")


if __name__ == "__main__":
    main()
