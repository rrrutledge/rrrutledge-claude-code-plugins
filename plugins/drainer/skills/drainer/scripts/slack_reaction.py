"""Slack reaction proposals - every deterministic step of one, from staging to its outcome.

A worker that judges an emoji reaction fits a Slack message proposes it here rather than adding it.
`propose` stages the reaction with send-confidence as a draft of message type `reaction` and scores it
under the live model. When the model's auto-send test passes, it adds the reaction at once, logs it as
auto-sent, and prints the digest line and the undo command; otherwise it prints the draft id, and the
worker puts the reaction in front of Russell like any written draft.
Russell's answer is the reaction's outcome, recorded by `approve` (which adds it, with a different emoji
if he chose one) or `discard`.
`undo` takes an auto-sent reaction off and records it as discarded, so the model learns from the undo.

Whether a reaction fits the message, and which emoji, stays with the worker (providers/slack-provider.md,
REACT).
Every send-confidence step is fail-open: with document-authoring missing, `propose` reports nothing
staged and never auto-sends.

CLI (each prints one JSON object):
    python slack_reaction.py propose --channel <C> --ts <ts> --emoji <name> --permalink <url> [--thread-ts <tts>]
    python slack_reaction.py approve --draft-id <id> [--emoji <name>]
    python slack_reaction.py approve --channel <C> --ts <ts> --emoji <name>      # a proposal that wasn't staged
    python slack_reaction.py discard --draft-id <id> [--reason "<text>"] [--moot]
    python slack_reaction.py undo    --draft-id <id>
"""
import argparse
import importlib.util
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from provider_base import find_skill_script, run_node  # noqa: E402
from ack_sweep import _doc_authoring_script  # noqa: E402


def send_confidence():
    """The send_confidence module, or None when document-authoring isn't installed."""
    path = _doc_authoring_script(os.path.join("send-confidence", "send_confidence.py"))
    if not path:
        return None
    sys.path.insert(0, os.path.dirname(path))
    spec = importlib.util.spec_from_file_location("send_confidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slack(verb, channel, ts, emoji):
    """Add (`--react`) or take off (`--unreact`) one reaction; raises with slack.js's error on failure."""
    path = find_skill_script(__file__, "slack", os.path.join("scripts", "slack.js"))
    if not path:
        raise SystemExit("the slack skill's slack.js was not found")
    res = run_node([path, verb, f"--channel={channel}", f"--ts={ts}", f"--emoji={emoji}"])
    if res.returncode != 0:
        raise SystemExit(f"slack.js {verb} failed: {(res.stderr or res.stdout).strip()[-300:]}")


def _scored(sc, draft_id):
    if not sc:
        raise SystemExit("send-confidence is not installed, so no reaction was proposed through it")
    d = sc.fold_drafts().get(draft_id)
    if not d or not d["scored"] or d["scored"].get("message_type") != sc.REACTION:
        raise SystemExit(f"no proposed reaction {draft_id}")
    return d["scored"]


def undo_command(draft_id):
    return f'python "{os.path.abspath(__file__)}" undo --draft-id {draft_id}'


def cmd_propose(args):
    sc = send_confidence()
    emoji = args.emoji.strip().strip(":")
    if not sc:
        return {"auto_sent": False, "draft_id": None, "staged": "send-confidence not installed"}
    try:
        scored = sc.stage_reaction(emoji, args.channel, args.ts, permalink=args.permalink,
                                   thread_ts=args.thread_ts,
                                   session_kind="background" if os.environ.get("CLAUDE_JOB_DIR") else "live")
    except Exception as e:  # fail-open: an unscored reaction is proposed like any other
        return {"auto_sent": False, "draft_id": None, "staged": f"not staged ({e})"}
    model = scored.get("model") or {}
    out = {"auto_sent": False, "draft_id": scored["draft_id"], "model": model or None}
    if model.get("auto_send"):
        slack("--react", args.channel, args.ts, emoji)
        sc.record_auto_sent(scored["draft_id"])
        out.update(auto_sent=True, undo=undo_command(scored["draft_id"]),
                   digest=f"Reacted :{emoji}: on {args.permalink} (send-confidence auto-send, model "
                          f"v{model.get('version')}: floor {model.get('floor')} over {model.get('support')} "
                          f"drafts). Undo: `{undo_command(scored['draft_id'])}`")
    return out


def cmd_approve(args):
    if not args.draft_id:
        # An unstaged proposal has no outcome to record: add the reaction and stop.
        emoji = args.emoji.strip().strip(":")
        slack("--react", args.channel, args.ts, emoji)
        return {"reacted": emoji, "disposition": None}
    sc = send_confidence()
    scored = _scored(sc, args.draft_id)
    r = scored["reaction"]
    emoji = (args.emoji or r["emoji"]).strip().strip(":")
    slack("--react", r["channel"], r["ts"], emoji)
    out = sc.record_outcome(args.draft_id, sc.reaction_text(emoji, r["channel"], r["ts"]),
                            discarded=False, edit_nature=[], reason=None)
    return {"reacted": emoji, "disposition": out["disposition"], "emoji": out.get("reaction_emoji")}


def cmd_discard(args):
    sc = send_confidence()
    _scored(sc, args.draft_id)
    out = sc.record_outcome(args.draft_id, None, discarded=True, edit_nature=[], reason=args.reason,
                            moot=args.moot)
    return {"disposition": out["disposition"], "moot": out["moot"]}


def cmd_undo(args):
    sc = send_confidence()
    r = _scored(sc, args.draft_id)["reaction"]
    slack("--unreact", r["channel"], r["ts"], r["emoji"])
    out = sc.record_outcome(args.draft_id, None, discarded=True, edit_nature=[],
                            reason="Russell undid a reaction the model sent on its own")
    return {"removed": r["emoji"], "disposition": out["disposition"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["propose", "approve", "discard", "undo"])
    ap.add_argument("--channel")
    ap.add_argument("--ts")
    ap.add_argument("--thread-ts")
    ap.add_argument("--emoji")
    ap.add_argument("--permalink")
    ap.add_argument("--draft-id")
    ap.add_argument("--reason")
    ap.add_argument("--moot", action="store_true")
    args = ap.parse_args()
    unstaged_approve = args.command == "approve" and not args.draft_id
    if args.command == "propose" or unstaged_approve:
        needed = ("channel", "ts", "emoji") + (() if unstaged_approve else ("permalink",))
        missing = [f for f in needed if not getattr(args, f)]
        if missing:
            raise SystemExit(f"{args.command} needs --{', --'.join(missing)}")
    elif not args.draft_id:
        raise SystemExit(f"{args.command} needs --draft-id")
    handler = {"propose": cmd_propose, "approve": cmd_approve, "discard": cmd_discard, "undo": cmd_undo}
    print(json.dumps(handler[args.command](args), indent=2))


if __name__ == "__main__":
    main()
