"""Tests for the digest's "Sent without you" section (scripts/auto_sent.py and its run-digest wiring):
every send send-confidence logged as auto-sent since the last digest is listed, a reaction with its emoji,
permalink, and undo command and any other kind of send generically, an undone send reads as undone, the
watermark moves only when a digest records it seen so a send shows in exactly one seen digest, and a
queued item whose only action was a send is named so it shows once. send-confidence is the dev-repo sibling, pointed at a temp log; slack.js is stubbed.

Run directly:
    python plugins/drainer/tests/test_auto_sent.py
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.abspath(os.path.join(HERE, "..")), "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)

os.environ["SEND_CONFIDENCE_DIR"] = tempfile.mkdtemp()
os.environ["SEND_CONFIDENCE_JUDGE"] = "off"

import auto_sent  # noqa: E402
import slack_reaction  # noqa: E402

spec = importlib.util.spec_from_file_location("run_digest", os.path.join(SCRIPTS, "run-digest.py"))
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


slack_reaction.slack = lambda *a: None
sc = slack_reaction.send_confidence()
PERMALINK = "https://example.slack.com/archives/C0123ABCD/p1761700000000100"
model = {"version": 3, "fitted_at": "2026-10-28T22:15:00-05:00", "binary_terms": [["kind:reaction"]],
         "value_terms": [], "values": {}, "latent": {}, "intercept": [0.0] * 4, "beta": [[5.0]] * 4,
         "clear_binary": [0], "support_rows": [[0]] * 25}
os.makedirs(os.path.dirname(sc.model_path()), exist_ok=True)
with open(sc.model_path(), "w", encoding="utf-8") as f:
    json.dump(model, f)


def propose(ts, emoji):
    return slack_reaction.cmd_propose(argparse.Namespace(channel="C0123ABCD", ts=ts, thread_ts=None,
                                                         emoji=emoji, permalink=PERMALINK))


def section(rt, queue=()):
    return auto_sent.section(sc, rt, list(queue), slack_reaction.undo_command)


rt = tempfile.mkdtemp()
print("with nothing auto-sent, the section says so")
check("nothing, and nothing to record", section(rt), (auto_sent.NOTHING, None))
check("send-confidence missing sends nothing",
      auto_sent.section(None, rt, [], slack_reaction.undo_command), (auto_sent.NOTHING, None))

print("an auto-sent reaction is listed with its emoji, permalink, and undo")
kept = propose("1761700003.000100", "heart")
undone = propose("1761700004.000100", "tada")
check("both went out on their own", (kept["auto_sent"], undone["auto_sent"]), (True, True))
slack_reaction.cmd_undo(argparse.Namespace(draft_id=undone["draft_id"]))
block, through = section(rt)
check("counts both", "(2):" in block, True)
check("names the emoji and the permalink", f"Reacted :heart: on {PERMALINK}" in block, True)
check("carries the undo command", slack_reaction.undo_command(kept["draft_id"]) in block, True)
check("an undone one reads as undone", "Reacted :tada:" in block and "undone since." in block, True)
check("and carries no undo of its own", slack_reaction.undo_command(undone["draft_id"]) in block, False)

print("any other kind of send is listed too")
sc._append_event({"event": "scored", "draft_id": "d-email1", "ts": datetime.datetime.now().astimezone().isoformat(),
                  "message_type": "reply", "channel": "outlook", "recipient": "jane@example.com",
                  "factors": {}})
sc._append_event({**sc._superseded_event("d-email1", datetime.datetime.now().astimezone().isoformat()),
                  "v": 2, "disposition": "auto-sent"})
block, through = section(rt)
check("a reply that went out on its own",
      "Sent a reply to jane@example.com on outlook. No undo; draft d-email1" in block, True)
check("runs through the newest send listed", through, sc.fold_drafts()["d-email1"]["outcome"]["ts"])

print("the watermark keeps a send to one seen digest")
check("an unrecorded digest leaves its sends for the next", "Reacted :heart:" in section(rt)[0], True)
auto_sent.write_watermark(rt, through)
check("a recorded one starts the next digest after it", section(rt), (auto_sent.NOTHING, None))
later = propose("1761700005.000100", "eyes")
check("and lists only what came after", ("Reacted :eyes:" in section(rt)[0], "Reacted :heart:" in section(rt)[0]),
      (True, False))
now = datetime.datetime.now().astimezone()
check("a first run looks back a week",
      auto_sent.read_watermark(tempfile.mkdtemp(), now) == now - datetime.timedelta(days=7), True)

print("a queued item whose only action was a send is named so it shows once")
queue = [
    {"id": "slack-repeat", "item": {"triage": "auto-handle", "disposition": "advanced",
                                    "autoSent": [later["draft_id"]], "dispositionReason": ""}},
    {"id": "slack-more", "item": {"triage": "auto-handle", "disposition": "advanced",
                                  "autoSent": [later["draft_id"]], "dispositionReason": "Approved a workspace invite"}},
    {"id": "slack-other", "item": {"triage": "auto-handle", "disposition": "advanced",
                                   "dispositionReason": "Approved a workspace invite"}},
    {"id": "fyi-1", "item": {"triage": "fyi", "autoSent": [later["draft_id"]]}},
]
block = section(rt, queue)[0]
check("the repeat is named", "Queue items repeating these sends: slack-repeat" in block, True)
check("an item that did more, an unrelated item, and an fyi are not",
      [i for i in ("slack-more", "slack-other", "fyi-1") if i in block], [])

print("the digest launcher seeds the block and survives a failure")
orig = auto_sent.section
auto_sent.section = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log unreadable"))
try:
    check("a failure renders as unavailable and lists nothing to record",
          digest.measure_auto_sent(rt), ("Sent without you since the last digest: unavailable this run "
                                         "(log unreadable).", None))
finally:
    auto_sent.section = orig
with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, "BLOCK-MARKER", "2026-11-19T09:00:00-06:00"),
          encoding="utf-8") as f:
    seed = f.read()
check("the seed carries the block", "step 1c - present it verbatim):\nBLOCK-MARKER" in seed, True)
check("and the command that records it as seen", "reported" in seed and "2026-11-19T09:00:00-06:00`" in seed, True)
with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, auto_sent.NOTHING, None), encoding="utf-8") as f:
    check("an empty block carries no command", "reported command" in f.read(), False)

print("reported records the watermark")
sys.argv = ["auto_sent.py", "reported", rt, "2026-11-19T09:00:00-06:00"]
auto_sent.main()
check("read back", auto_sent.read_watermark(rt, now).isoformat(), "2026-11-19T09:00:00-06:00")

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
