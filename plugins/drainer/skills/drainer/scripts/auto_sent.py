"""Everything the pod sent without Russell since the last digest, rendered as the digest's fixed section.

send-confidence is the one gate an unreviewed send passes through: whatever goes out on the model's
say-so is logged there with `disposition: "auto-sent"` (`record_auto_sent`), by the script that sent it,
at the moment it sent. So this reads that log rather than anything a worker records, and every auto-sent
action reaches the digest whether or not its worker closed out an item, and whatever kind of send it was.

The digest launcher (`run-digest.py`) renders the section into the digest seed, which the session
presents verbatim, and then advances a watermark in `<runtime_dir>/auto-sent-reported.json` so the next
digest starts where this one ended. A send undone since (`slack_reaction.py undo`) is listed as undone.
A queued auto-handle item that only repeats one of these sends is named in the seed so the digest
leaves it out of its Auto-handled section and the send shows once.
"""
import datetime
import json
import os

WATERMARK_FILE = "auto-sent-reported.json"
FIRST_RUN_LOOKBACK = datetime.timedelta(days=7)


def _parse(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _now():
    return datetime.datetime.now().astimezone()


def read_watermark(runtime_dir, now=None):
    """The time the last digest's section ran through; a week back when no digest has run one yet."""
    try:
        with open(os.path.join(runtime_dir, WATERMARK_FILE), encoding="utf-8") as f:
            through = _parse(json.load(f).get("through"))
    except (OSError, ValueError, AttributeError):
        through = None
    return through or (now or _now()) - FIRST_RUN_LOOKBACK


def write_watermark(runtime_dir, through):
    os.makedirs(runtime_dir, exist_ok=True)
    with open(os.path.join(runtime_dir, WATERMARK_FILE), "w", encoding="utf-8") as f:
        json.dump({"through": through.isoformat()}, f)


def collect(sc, since, until):
    """Each send logged auto-sent in (since, until], oldest first, with whether it was undone since."""
    events = sc.load_events()
    drafts = sc.fold_drafts(events)
    out = []
    for e in events:
        if e.get("event") != "outcome" or e.get("disposition") != "auto-sent":
            continue
        ts = _parse(e.get("ts"))
        if not ts or not since < ts <= until:
            continue
        d = drafts.get(e.get("draft_id")) or {}
        latest = d.get("outcome") or {}
        out.append({"draft_id": e["draft_id"], "ts": ts, "scored": d.get("scored") or {},
                    "undone": latest.get("disposition") == "discarded"})
    return sorted(out, key=lambda s: s["ts"])


def describe(sent, undo_command):
    """One line for a send: when, what went where, and how to undo it (or that it was undone)."""
    scored = sent["scored"]
    when = sent["ts"].astimezone().strftime("%b %d, %I:%M %p %Z").replace(" 0", " ")
    r = scored.get("reaction")
    if r:
        what = f"Reacted :{r.get('emoji')}: on {r.get('permalink') or r.get('channel') + '/' + r.get('ts')}"
        undo = f"Undo: `{undo_command(sent['draft_id'])}`"
    else:
        what = (f"Sent a {scored.get('message_type') or 'message'} to {scored.get('recipient') or '?'} "
                f"on {scored.get('channel') or '?'}")
        undo = f"draft {sent['draft_id']}"
    return f"- {when}: {what}" + (" - undone since." if sent["undone"] else f". {undo}")


def covered_queue_ids(queue, sends):
    """The queued auto-handle items that only repeat one of these sends: any whose record names one of
    their draft ids, except a briefing, which carries findings of its own."""
    ids = [s["draft_id"] for s in sends]
    covered = []
    for entry in queue:
        item = entry.get("item") or {}
        if item.get("triage") != "auto-handle" or item.get("disposition") == "briefed":
            continue
        blob = json.dumps(item)
        if any(i in blob for i in ids):
            covered.append(entry.get("id"))
    return covered


def render(sends, covered, undo_command):
    """The seed's block for the digest's "Sent without you" section."""
    if not sends:
        return "Sent without you since the last digest: nothing."
    lines = [f"Sent without you since the last digest ({len(sends)}):"]
    lines += ["    " + describe(s, undo_command) for s in sends]
    if covered:
        lines.append("    Already shown above, so leave these queue items out of Auto-handled "
                     "(still queue-clear them on Russell's OK): " + ", ".join(covered))
    return "\n".join(lines)


def section(sc, runtime_dir, queue, undo_command, now=None):
    """The rendered block and the time it runs through, for the launcher to save once the digest
    launches. With send-confidence missing nothing could have gone out on its own."""
    until = now or _now()
    if not sc:
        return "Sent without you since the last digest: nothing (send-confidence is not installed).", until
    sends = collect(sc, read_watermark(runtime_dir, until), until)
    return render(sends, covered_queue_ids(queue, sends), undo_command), until
