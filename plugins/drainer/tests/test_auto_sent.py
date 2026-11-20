"""Tests for the digest's "Sent without you" section (scripts/auto_sent.py and its run-digest wiring):
every send send-confidence logged as auto-sent that no seen digest has reported is listed, a reaction with
its emoji, permalink, and undo command and any other kind of send generically, an undone send reads as
undone, a send leaves the section only once a digest reports it, and a queued item whose only action was a
send is named so it shows once. send-confidence is the dev-repo sibling, pointed at a temp log; slack.js
is stubbed.

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


def fake_slack(*args):
    if args[0].startswith("--resolve-name="):
        return {"name": {"C0123ABCD": "#summit2026planning", "D0DM": "Jeff Bailey"}[args[0].split("=")[1]]}
    if args[0] == "--show":
        ts = [a for a in args if a.startswith("--ts=")][0].split("=")[1]
        if ts == "1761600000.000100":
            return {"from": "Paul Berschick", "text": "These postcards are so engaging."}
        return {"from": "Paul Berschick", "text": "And I enjoyed the\nsmiles on the faces"}
    return [{"ts": "1761690000.000100", "from": "Russell", "text": "ask"},
            {"ts": "1761700000.000100", "from": "Jeff Bailey", "text": "later"}]


def section(rt, queue=()):
    return auto_sent.section(sc, rt, list(queue), slack_reaction.undo_command, fake_slack)


def now():
    return datetime.datetime.now().astimezone().isoformat()


rt = tempfile.mkdtemp()
print("with nothing auto-sent, the section says so")
check("nothing, and nothing to report", section(rt), (auto_sent.NOTHING, []))
check("send-confidence missing sends nothing",
      auto_sent.section(None, rt, [], slack_reaction.undo_command), (auto_sent.NOTHING, []))

print("an auto-sent reaction is listed with its emoji, permalink, and undo")
kept = propose("1761700003.000100", "heart")
undone = propose("1761700004.000100", "tada")
check("both went out on their own", (kept["auto_sent"], undone["auto_sent"]), (True, True))
slack_reaction.cmd_undo(argparse.Namespace(draft_id=undone["draft_id"]))
block, ids = section(rt)
check("counts both, oldest first", ("(2):" in block, ids), (True, [kept["draft_id"], undone["draft_id"]]))
check("names the channel, real emoji, author, and quoted text",
      '**#summit2026planning** - ❤️ on Paul Berschick (' in block
      and '> "And I enjoyed the smiles on the faces"' in block, True)
check("no permalink and no :name: shortcode in what Russell sees",
      PERMALINK in block or "Reacted :" in block, False)
check("the undo command sits under the digest-session-only line",
      block.index("for you alone") < block.index(slack_reaction.undo_command(kept["draft_id"])), True)
check("an undone one reads as undone, with no undo of its own",
      "🎉 on Paul Berschick" in block and "(undone since)" in block
      and slack_reaction.undo_command(undone["draft_id"]) not in block, True)
check("emoji map", [auto_sent.emoji_display(n) for n in ("heart", "+1", "isc", "heart::skin-tone-3")],
      ["❤️", "👍", "custom :isc: emoji", "❤️"])

print("a threaded reply carries its parent as raw context; a DM carries the previous message")
facts = auto_sent.reaction_facts({"channel": "C0123ABCD", "ts": "1761700000.000100",
                                  "thread_ts": "1761600000.000100"}, fake_slack)
check("thread parent", facts["parent"]["text"], "These postcards are so engaging.")
facts = auto_sent.reaction_facts({"channel": "D0DM", "ts": "1761700000.000100"}, fake_slack)
check("DM name and previous message", (facts["where"], facts["parent"]["text"]), ("DM with Jeff Bailey", "ask"))
def broken(*a):
    raise RuntimeError("slack down")
facts = auto_sent.reaction_facts({"channel": "C0123ABCD", "ts": "1"}, broken)
check("a Slack failure degrades to the id",
      auto_sent.describe({"scored": {"reaction": {"channel": "C0123ABCD", "emoji": "heart"}}, "undone": False},
                         facts), "- **C0123ABCD** - ❤️:\n  > (message text unavailable)")

print("any other kind of send is listed too, and a malformed event is skipped")
sc._append_event({"event": "scored", "draft_id": "d-email1", "ts": now(), "message_type": "reply",
                  "channel": "outlook", "recipient": "jane@example.com", "factors": {}})
sc._append_event({**sc._superseded_event("d-email1", now()), "v": 2, "disposition": "auto-sent"})
sc._append_event({"event": "outcome", "ts": now(), "disposition": "auto-sent"})
block, ids = section(rt)
check("a reply that went out on its own", "**outlook** - sent a reply to jane@example.com" in block, True)
check("three sends listed", len(ids), 3)

print("a send leaves the section only once a digest reports it")
check("an unreported digest leaves its sends for the next", section(rt)[1], ids)
later = propose("1761700005.000100", "eyes")
sys.argv = ["auto_sent.py", "reported", rt] + ids
auto_sent.main()
check("a send logged after the seed still shows next time", section(rt)[1], [later["draft_id"]])
auto_sent.add_reported(rt, [kept["draft_id"]])
check("reporting an old send again keeps the newer ones reported", section(rt)[1], [later["draft_id"]])

print("a queued item whose only action was a send is named so it shows once")
queue = [
    {"id": "slack-repeat", "item": {"triage": "auto-handle", "disposition": "advanced",
                                    "autoSent": [kept["draft_id"]], "dispositionReason": ""}},
    {"id": "slack-more", "item": {"triage": "auto-handle", "disposition": "advanced",
                                  "autoSent": [later["draft_id"]], "dispositionReason": "Approved a workspace invite"}},
    {"id": "slack-other", "item": {"triage": "auto-handle", "disposition": "advanced",
                                   "dispositionReason": "Approved a workspace invite"}},
    {"id": "fyi-1", "item": {"triage": "fyi", "autoSent": [later["draft_id"]]}},
]
block = section(rt, queue)[0]
check("the repeat is named, its send already reported or not", "in an earlier digest: slack-repeat" in block,
      True)
check("an item that did more, an unrelated item, and an fyi are not",
      [i for i in ("slack-more", "slack-other", "fyi-1") if i in block], [])

print("the digest launcher seeds the block and survives a failure")
orig = auto_sent.section
auto_sent.section = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log unreadable"))
try:
    check("a failure renders as unavailable and lists nothing to report",
          digest.measure_auto_sent(rt, []), ("Sent without you since the last digest: unavailable this run "
                                             "(log unreadable).", []))
finally:
    auto_sent.section = orig
with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, "BLOCK-MARKER", ["d-1", "d-2"]),
          encoding="utf-8") as f:
    seed = f.read()
check("the seed carries the block", "(digest-core step 1c):\nBLOCK-MARKER" in seed, True)
check("and the command that reports it", "reported" in seed and " d-1 d-2`" in seed, True)
with open(digest.write_seed(rt, "repo", {"local_dir": rt}, None, auto_sent.NOTHING, []), encoding="utf-8") as f:
    check("an empty block carries no command", "reported command" in f.read(), False)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
