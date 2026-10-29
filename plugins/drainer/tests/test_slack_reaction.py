"""Tests for Slack reaction proposals (scripts/slack_reaction.py): a proposal is staged and scored as a
`reaction` draft, it waits for Russell until the model's auto-send test passes, his approve or discard is
its outcome, and undoing an auto-sent reaction records a discard. slack.js is stubbed; send-confidence is
the dev-repo sibling, pointed at a temp log.

Run directly:
    python plugins/drainer/tests/test_slack_reaction.py
"""
import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.abspath(os.path.join(HERE, "..")), "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)

os.environ["SEND_CONFIDENCE_DIR"] = tempfile.mkdtemp()
os.environ["SEND_CONFIDENCE_JUDGE"] = "off"

import slack_reaction  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


calls = []
slack_reaction.slack = lambda verb, channel, ts, emoji: calls.append((verb, channel, ts, emoji))
sc = slack_reaction.send_confidence()
PERMALINK = "https://example.slack.com/archives/C0123ABCD/p1761700000000100"


def run(command, **kw):
    args = {"channel": "C0123ABCD", "ts": None, "thread_ts": None, "emoji": None, "permalink": PERMALINK,
            "draft_id": None, "reason": None, "moot": False, **kw}
    handler = {"propose": slack_reaction.cmd_propose, "approve": slack_reaction.cmd_approve,
               "discard": slack_reaction.cmd_discard, "undo": slack_reaction.cmd_undo}[command]
    return handler(argparse.Namespace(**args))


def outcome(draft_id):
    return sc.fold_drafts()[draft_id]["outcome"]


print("with no model live, a proposal waits for Russell")
p = run("propose", ts="1761700000.000100", emoji=":thumbsup:")
check("not auto-sent, and staged", (p["auto_sent"], bool(p["draft_id"])), (False, True))
check("nothing reached Slack", calls, [])
check("staged as a reaction", sc.fold_drafts()[p["draft_id"]]["scored"]["message_type"], "reaction")
a = run("approve", draft_id=p["draft_id"])
check("approval adds the proposed emoji", calls[-1], ("--react", "C0123ABCD", "1761700000.000100", "thumbsup"))
check("approved as proposed is sent as-is", (a["disposition"], outcome(p["draft_id"])["edit_distance"]),
      ("sent-as-is", 0.0))

p = run("propose", ts="1761700001.000100", emoji="thumbsup")
a = run("approve", draft_id=p["draft_id"], emoji="tada")
check("approval with another emoji adds that one", calls[-1][3], "tada")
check("and counts as an edit", (a["disposition"], outcome(p["draft_id"])["edit_distance"]), ("edited", 1.0))
check("the outcome names both emoji and the change",
      (a["emoji"], outcome(p["draft_id"])["edit_nature"]),
      ({"proposed": "thumbsup", "sent": "tada"}, ["different-emoji"]))

p = run("propose", ts="1761700002.000100", emoji="thumbsup")
d = run("discard", draft_id=p["draft_id"], reason="a written reply fits better")
check("a discard records the outcome and reacts nothing", (d["disposition"], len(calls)), ("discarded", 2))
a = run("approve", ts="1761700009.000100", emoji="eyes")
check("approving an unstaged proposal just adds it", (calls[-1], a["disposition"]),
      (("--react", "C0123ABCD", "1761700009.000100", "eyes"), None))

print("with a model that passes reactions, a proposal goes out on its own")
model = {"version": 3, "fitted_at": "2026-10-28T22:15:00-05:00", "binary_terms": [["kind:reaction"]],
         "value_terms": [], "values": {}, "latent": {}, "intercept": [0.0] * 4, "beta": [[5.0]] * 4,
         "clear_binary": [0], "support_rows": [[0]] * 25}
os.makedirs(os.path.dirname(sc.model_path()), exist_ok=True)
with open(sc.model_path(), "w", encoding="utf-8") as f:
    json.dump(model, f)
p = run("propose", ts="1761700003.000100", emoji="white_check_mark")
check("auto-sent", p["auto_sent"], True)
check("the reaction went out", calls[-1], ("--react", "C0123ABCD", "1761700003.000100", "white_check_mark"))
check("the digest line names the emoji, the permalink, and the undo",
      (":white_check_mark:" in p["digest"], PERMALINK in p["digest"], p["undo"] in p["digest"]), (True, True, True))
check("logged as auto-sent, outside the target", outcome(p["draft_id"])["disposition"], "auto-sent")
u = run("undo", draft_id=p["draft_id"])
check("undo takes the reaction off", calls[-1], ("--unreact", "C0123ABCD", "1761700003.000100", "white_check_mark"))
check("and records a discard", (u["disposition"], outcome(p["draft_id"])["disposition"]), ("discarded", "discarded"))

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
