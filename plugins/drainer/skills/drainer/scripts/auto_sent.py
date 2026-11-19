"""Everything the pod sent without Russell that no seen digest has listed, as the digest's fixed section.

send-confidence is the one gate an unreviewed send passes through: whatever goes out on the model's
say-so is logged there with `disposition: "auto-sent"` (`record_auto_sent`), by the script that sent it,
at the moment it sent. So this reads that log, and every auto-sent action reaches the digest whether or
not its worker closed out an item, and whatever kind of send it was.

The digest launcher (`run-digest.py`) renders the section into the digest seed, which the session
presents verbatim. A send undone since (`slack_reaction.py undo`) is listed as undone. The section lists
every auto-sent draft id missing from `<runtime_dir>/auto-sent-reported.json`, and the digest session
adds the ids it listed by running `reported` once Russell has seen the section, so a digest that never
reaches him leaves its sends for the next one.
A queued auto-handle item with an `autoSent` list and an empty `dispositionReason` did nothing but send,
so the seed names it and the digest leaves it out of Auto-handled.

CLI:
    python auto_sent.py reported <runtime_dir> <draft_id>...     # record these sends as seen
"""
import datetime
import json
import os
import sys

REPORTED_FILE = "auto-sent-reported.json"
NOTHING = "Sent without you since the last digest: nothing."


def read_reported(runtime_dir):
    try:
        with open(os.path.join(runtime_dir, REPORTED_FILE), encoding="utf-8") as f:
            return set(json.load(f).get("draft_ids") or [])
    except (OSError, ValueError, AttributeError):
        return set()


def add_reported(runtime_dir, draft_ids):
    ids = read_reported(runtime_dir) | set(draft_ids)
    os.makedirs(runtime_dir, exist_ok=True)
    with open(os.path.join(runtime_dir, REPORTED_FILE), "w", encoding="utf-8") as f:
        json.dump({"draft_ids": sorted(ids)}, f)


def collect(sc, reported):
    """Each send logged auto-sent and not yet reported, oldest first, with whether it was undone since."""
    events = sc.load_events()
    drafts = sc.fold_drafts(events)
    out = []
    for e in events:
        did = e.get("draft_id")
        if e.get("event") != "outcome" or e.get("disposition") != "auto-sent" or not did or did in reported:
            continue
        try:
            ts = datetime.datetime.fromisoformat(e.get("ts")).astimezone()
        except (TypeError, ValueError):
            continue
        d = drafts.get(did) or {}
        out.append({"draft_id": did, "ts": ts, "scored": d.get("scored") or {},
                    "undone": (d.get("outcome") or {}).get("disposition") == "discarded"})
    return sorted(out, key=lambda s: s["ts"])


def describe(sent, undo_command):
    """One line for a send: when, what went where, and how to undo it (or that it was undone)."""
    scored = sent["scored"]
    when = sent["ts"].strftime("%b %d, %I:%M %p %Z").replace(" 0", " ")
    r = scored.get("reaction")
    if r:
        what = f"Reacted :{r.get('emoji') or '?'}: on {r.get('permalink') or '?'}"
        undo = f"Undo: `{undo_command(sent['draft_id'])}`"
    else:
        what = (f"Sent a {scored.get('message_type') or 'message'} to {scored.get('recipient') or '?'} "
                f"on {scored.get('channel') or '?'}")
        undo = f"No undo; draft {sent['draft_id']}"
    return f"- {when}: {what}" + (" - undone since." if sent["undone"] else f". {undo}")


def covered_queue_ids(queue):
    """The queued auto-handle items whose only action was a send, which this section or an earlier one
    lists."""
    return [entry.get("id") for entry in queue
            if (entry.get("item") or {}).get("triage") == "auto-handle" and entry["item"].get("autoSent")
            and not (entry["item"].get("dispositionReason") or "").strip()]


def section(sc, runtime_dir, queue, undo_command):
    """The seed's block for the digest's "Sent without you" section, and the draft ids it lists for
    `reported` to record. With send-confidence missing nothing could have gone out on its own."""
    sends = collect(sc, read_reported(runtime_dir)) if sc else []
    lines = [f"Sent without you since the last digest ({len(sends)}):"] if sends else [NOTHING]
    lines += ["    " + describe(s, undo_command) for s in sends]
    covered = covered_queue_ids(queue)
    if covered:
        lines.append("    Queue items that only repeat a send listed here or in an earlier digest: "
                     + ", ".join(covered))
    return "\n".join(lines), [s["draft_id"] for s in sends]


def main():
    if len(sys.argv) < 4 or sys.argv[1] != "reported":
        raise SystemExit("usage: auto_sent.py reported <runtime_dir> <draft_id>...")
    add_reported(sys.argv[2], sys.argv[3:])
    print(f"Sent without you reported: {len(sys.argv) - 3} send(s).")


if __name__ == "__main__":
    main()
