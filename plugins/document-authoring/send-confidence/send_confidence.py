#!/usr/bin/env python3
"""Send-confidence scoring - estimates how likely a drafted message is to need a change
before Russell sends it, and decides when a sent draft has something to teach.

Stage 1 is score-and-measure only: nothing here gates or auto-sends a message, and no score
is shown per draft. `verify_gate.py mint --score-context` calls `score_and_log` right after
the review receipt is minted; scoring is fail-open (a scorer exception never blocks the
receipt), so this module must never raise past its own CLI entry points.

Five factors, each 0-100 where higher means "more likely to go out untouched", one per aspect
of uncertainty described in `uncertainty-aspects.md`: rule coverage (the writing reviewer's
rating, passed in through the context), content latitude and stakes (one cold Haiku call per
draft, which reads that file whole), fact support (the same call's claim audit), and
familiarity (a Sent-folder count proxy). The composite is the weighted mean over whichever
factors came back non-null - see `weights.json` for the weights and thresholds.

The log is an append-only JSONL file per machine under `SEND_CONFIDENCE_DIR`
(`~/OneDrive/Claude/send-confidence` by default - see the module-level path helpers). Nothing
is ever rewritten in place, which is what keeps concurrent per-machine appends over a synced
folder safe; a reader folds each draft's `scored` event with its newest `outcome` event.

CLI:
    python send_confidence.py context --channel <c> --recipient <r> --session-kind <k> \
        (--ask <text> | --ask-file <file>) (--inputs <text> | --inputs-file <file>) \
        [--account <a>] [--thread-ref <t>] [--iid <i>] [--turns-before-draft <n>] \
        [--sent-count <n>] [--rule-coverage <1-5>] [--uncertain-spots-file <file>]
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --sent-file <file> [--edit-nature a,b]
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --discarded [--reason "<text>"] [--moot]
    python send_confidence.py weights-suggest
    python send_confidence.py show <draft_id>

`context` is the deterministic half of staging a scored draft: it builds the context JSON
`score_and_log` expects, writes it to a scratch file, and prints the exact `verify_gate.py
mint --score-context` command to run next - the drafting session only ever supplies judgment
(the ask, the inputs, which channel and recipient, the familiarity count it looked up, the
reviewer's rule coverage), never hand-authors the JSON or recalls the mint invocation from memory.

`outcome` is the whole of the drafting session's part in learning from a send: it labels the
edits (one Haiku call, unless `--edit-nature` overrides), records the outcome, and prints a
spawn command for a learning session whenever the send has something to teach - see
`learn-from-send.md` for what that session does.
"""

import os
import re
import sys
import glob
import json
import math
import socket
import difflib
import hashlib
import tempfile
import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_PATH = os.path.join(SCRIPT_DIR, "weights.json")
ASPECTS_PATH = os.path.join(SCRIPT_DIR, "uncertainty-aspects.md")
# send-confidence/ and hooks/ are always siblings under the same plugin checkout, whether
# that's a dev clone or an installed plugin cache snapshot, so this resolves correctly either way.
VERIFY_GATE_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "hooks", "verify_gate.py")
# Repo-relative, because the learning session runs rooted in the plugins repo clone.
PROCEDURE_PATH = "plugins/document-authoring/skills/document-authoring/learn-from-send.md"

INPUTS_CHAR_CAP = 12000
DIFF_CHAR_CAP = 20000

JUDGE_MODEL = "claude-haiku-4-5"

RESOLVED_DISPOSITIONS = {"sent-as-is", "edited", "discarded"}
FACTOR_NAMES = ["familiarity", "stakes", "content_latitude", "fact_support", "rule_coverage"]

# Log entries written before the factor set was reshaped still fold under the new names.
LEGACY_FACTOR_NAMES = {
    "phrasing_complexity": "stakes",
    "task_ambiguity": "content_latitude",
    "input_completeness": "fact_support",
}
DROPPED_FACTORS = {"stylometric"}

EDIT_LABELS = ["factual-correction", "tone-voice", "structural-rewrite"]
VOICE_LABELS = {"tone-voice", "structural-rewrite"}

NATURE_TO_FACTORS = {
    "factual-correction": ["fact_support", "content_latitude"],
    "tone-voice": ["rule_coverage", "familiarity"],
    "structural-rewrite": ["rule_coverage"],
}

DEFAULT_WEIGHTS = {
    "version": 2,
    "weights": {f: 0.2 for f in FACTOR_NAMES},
    "thresholds": {"factor_high": 70, "factor_low": 40, "composite_high": 75,
                   "composite_low": 40, "sent_as_is_max_distance": 0.01},
}


# --- data directory layout ---

def data_dir():
    return os.environ.get("SEND_CONFIDENCE_DIR") or os.path.expanduser("~/OneDrive/Claude/send-confidence")


def log_path():
    return os.path.join(data_dir(), f"log-{socket.gethostname()}.jsonl")


def drafts_dir():
    return os.path.join(data_dir(), "drafts")


def briefs_dir():
    return os.path.join(data_dir(), "briefs")


def _load_weights():
    try:
        with open(WEIGHTS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return json.loads(json.dumps(DEFAULT_WEIGHTS))


def _load_aspects():
    try:
        with open(ASPECTS_PATH, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        text = ""
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _anthropic_api_key():
    return os.environ.get("ANTHROPIC_HOOK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")


# --- the Haiku calls ---

def _call_model(system, user, api_key, max_tokens):
    """One JSON-answering Haiku call. Returns (parsed_dict, error)."""
    if os.environ.get("SEND_CONFIDENCE_JUDGE") == "off":
        return None, "judge disabled (SEND_CONFIDENCE_JUDGE=off)"
    if not api_key:
        return None, "no API key"
    payload = json.dumps({
        "model": JUDGE_MODEL, "max_tokens": max_tokens, "temperature": 0,
        "system": system, "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = Request(
        "https://api.anthropic.com/v1/messages", data=payload, method="POST",
        headers={"anthropic-version": "2023-06-01", "content-type": "application/json",
                 "x-api-key": api_key},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        return None, f"model call failed: {e}"
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None, "model returned unparseable output"
    try:
        return json.loads(m.group(0)), None
    except Exception:
        return None, "model returned invalid JSON"


JUDGE_SYSTEM = """You are estimating, for Russell's send-confidence system, how likely it is \
that he will change a drafted message before sending it. You never judge whether the draft \
follows a writing rule - a separate reviewer does that. You rate the situation the draft is in.

The aspects of uncertainty, with their rating scales:

{aspects}

Rate Content latitude and Stakes on their 1-5 scales, and list every factual claim in the \
draft for Fact support. The other aspects are measured elsewhere; ignore them.

Respond with ONLY this JSON, no other text:
{{"content_latitude": {{"rating": 1-5, "rationale": "..."}}, "stakes": {{"rating": 1-5, \
"rationale": "..."}}, "claims": [{{"text": "...", "supported": true, "source": "ask|inputs|none"}}]}}"""


def run_judge(ctx, body, aspects, api_key):
    """One Haiku call rating content latitude and stakes and auditing claim support.
    Returns (result_dict, error) - result is None and error is set on any failure, so the
    three judged factors fall out as null and scoring continues on the others."""
    user = json.dumps({
        "channel": ctx.get("channel"), "ask": ctx.get("ask", ""),
        "inputs": (ctx.get("inputs") or "")[:INPUTS_CHAR_CAP], "draft": body,
    })
    parsed, error = _call_model(JUDGE_SYSTEM.format(aspects=aspects or ""), user, api_key, 2048)
    if error:
        return None, error
    if not all(k in parsed for k in ("content_latitude", "stakes", "claims")):
        return None, "judge response missing expected fields"
    return parsed, None


LABEL_SYSTEM = """You compare a draft message with the version Russell actually sent, and \
label every change he made. Use only these labels:
- factual-correction: a corrected fact, name, link, date, number, or detail, or a decision \
the draft made on his behalf that he changed.
- tone-voice: phrasing, filler, warmth, or altitude changed while the sentence structure \
stayed intact.
- structural-rewrite: sentences reordered, reshaped, merged, split, or cut.
Ignore pure whitespace and formatting.

Respond with ONLY this JSON, no other text:
{"changes": [{"label": "...", "summary": "one short phrase naming what changed"}]}"""


def label_edits(draft_text, sent_text, api_key):
    """One Haiku call labelling each draft-to-sent change. Returns (labels, changes, error),
    labels being the distinct labels in first-seen order."""
    user = json.dumps({"draft": draft_text, "sent": sent_text})
    parsed, error = _call_model(LABEL_SYSTEM, user, api_key, 1024)
    if error:
        return [], [], error
    changes = [c for c in (parsed.get("changes") or [])
               if isinstance(c, dict) and c.get("label") in EDIT_LABELS]
    labels = []
    for c in changes:
        if c["label"] not in labels:
            labels.append(c["label"])
    return labels, changes, None


def _judge_log_shape(judge):
    if not judge:
        return None
    return {
        "model": JUDGE_MODEL,
        "rationales": {
            "content_latitude": (judge.get("content_latitude") or {}).get("rationale"),
            "stakes": (judge.get("stakes") or {}).get("rationale"),
        },
        "claims": judge.get("claims") or [],
    }


# --- scoring ---

def score_draft(body, ctx, *, weights, aspects, api_key):
    """Pure scoring: returns {"score", "factors", "features", "judge", "errors"}. No I/O
    besides the judge call."""
    errors = []
    features = {}
    factors = {}

    sent_count = ctx.get("sent_count")
    features["sent_count"] = sent_count
    if sent_count is None:
        factors["familiarity"] = None
    else:
        n = min(sent_count, 20)
        factors["familiarity"] = round(100 * math.log2(1 + n) / math.log2(21))

    coverage = ctx.get("rule_coverage")
    if isinstance(coverage, int) and 1 <= coverage <= 5:
        factors["rule_coverage"] = round(100 * (coverage - 1) / 4)
        features["rule_coverage_rating"] = coverage
    else:
        factors["rule_coverage"] = None

    judge, judge_error = run_judge(ctx, body, aspects, api_key)
    if judge_error:
        errors.append(judge_error)

    if judge:
        rating_s = judge["stakes"]["rating"]
        factors["stakes"] = round(100 * (5 - rating_s) / 4)
        features["stakes_rating"] = rating_s

        rating_c = judge["content_latitude"]["rating"]
        turns = ctx.get("turns_before_draft") or 0
        penalty = min(20, 5 * turns)
        factors["content_latitude"] = max(0, round(100 * (5 - rating_c) / 4) - penalty)
        features["latitude_rating"] = rating_c
        features["latitude_turn_penalty"] = penalty

        claims = judge.get("claims") or []
        total = len(claims)
        supported = sum(1 for c in claims if c.get("supported"))
        factors["fact_support"] = 100 if total == 0 else round(100 * supported / total)
        features["claims_total"] = total
        features["claims_unsupported"] = total - supported
    else:
        factors["stakes"] = None
        factors["content_latitude"] = None
        factors["fact_support"] = None

    w = (weights or {}).get("weights", {})
    available = {f: s for f, s in factors.items() if s is not None}
    if available:
        total_w = sum(w.get(f, 0) for f in available)
        score = round(sum(w.get(f, 0) * s for f, s in available.items()) / total_w) if total_w else None
    else:
        score = None

    return {"score": score, "factors": factors, "features": features, "judge": judge, "errors": errors}


# --- the log ---

def _append_event(event):
    os.makedirs(data_dir(), exist_ok=True)
    with open(log_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _iter_log_files():
    d = data_dir()
    if not os.path.isdir(d):
        return []
    return sorted(glob.glob(os.path.join(d, "log-*.jsonl")))


def load_events():
    events = []
    for path in _iter_log_files():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
    return events


def _normalize_factors(factors):
    out = {}
    for name, value in (factors or {}).items():
        if name in DROPPED_FACTORS:
            continue
        out[LEGACY_FACTOR_NAMES.get(name, name)] = value
    return out


def fold_drafts(events=None):
    """{draft_id: {"scored": event, "outcome": event_or_None}}, each draft's scored event
    folded with its newest outcome event, its factors under their current names."""
    events = events if events is not None else load_events()
    drafts = {}
    for e in events:
        did = e.get("draft_id")
        kind = e.get("event")
        if not did or kind not in ("scored", "outcome"):
            continue
        d = drafts.setdefault(did, {"scored": None, "outcome": None})
        if kind == "scored":
            d["scored"] = {**e, "factors": _normalize_factors(e.get("factors"))}
        elif d["outcome"] is None or e.get("ts", "") >= d["outcome"].get("ts", ""):
            d["outcome"] = e
    return drafts


def _read_draft_text(draft_id):
    with open(os.path.join(drafts_dir(), f"{draft_id}.txt"), "r", encoding="utf-8") as fh:
        return fh.read()


def _supersede_pending(thread_ref, recipient):
    if not thread_ref and not recipient:
        return
    ts = datetime.datetime.now().astimezone().isoformat()
    for draft_id, d in fold_drafts().items():
        scored = d["scored"]
        if not scored or d["outcome"] is not None:
            continue
        if scored.get("thread_ref") == thread_ref and scored.get("recipient") == recipient:
            _append_event({
                "event": "outcome", "v": 1, "draft_id": draft_id, "ts": ts,
                "disposition": "superseded", "edit_distance": None, "edit_nature": [],
                "reason": None, "miscalibrated": False, "direction": None,
                "implicated": [], "under_factors": [], "learn_reasons": [],
            })


def score_and_log(body, sha256, ctx_path):
    """Load ctx, weights.json, uncertainty-aspects.md; call score_draft; snapshot the body to
    drafts/<draft_id>.txt; append a superseded outcome for any earlier pending draft with the
    same thread_ref + recipient; append the scored event; return the draft_id."""
    with open(ctx_path, "r", encoding="utf-8") as fh:
        ctx = json.load(fh)

    weights = _load_weights()
    aspects, aspects_sha = _load_aspects()
    api_key = _anthropic_api_key()

    result = score_draft(body, ctx, weights=weights, aspects=aspects, api_key=api_key)

    ts = datetime.datetime.now().astimezone()
    draft_id = f"d-{sha256[:12]}-{ts.strftime('%Y%m%d%H%M%S')}"

    os.makedirs(drafts_dir(), exist_ok=True)
    with open(os.path.join(drafts_dir(), f"{draft_id}.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)

    _supersede_pending(ctx.get("thread_ref"), ctx.get("recipient"))

    event = {
        "event": "scored", "v": 2, "draft_id": draft_id,
        "ts": ts.isoformat(), "machine": socket.gethostname(), "sha256": sha256,
        "channel": ctx.get("channel"), "account": ctx.get("account"),
        "recipient": ctx.get("recipient"), "thread_ref": ctx.get("thread_ref"),
        "iid": ctx.get("iid"), "session_kind": ctx.get("session_kind"),
        "turns_before_draft": ctx.get("turns_before_draft"),
        "score": result["score"], "weights_version": weights.get("version"),
        "aspects_sha": aspects_sha,
        "factors": result["factors"], "features": result["features"],
        "judge": _judge_log_shape(result["judge"]),
        "uncertain_spots": ctx.get("uncertain_spots"),
        "errors": result["errors"],
    }
    _append_event(event)
    return draft_id


def _edit_distance(draft_text, sent_text):
    a = " ".join((draft_text or "").split())
    b = " ".join((sent_text or "").split())
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return round(1 - ratio, 4)


# --- the learning-session brief ---

def _spawn_title(scored):
    who = f"{scored.get('channel') or 'message'} to {scored.get('recipient') or 'unknown'}"
    return "Learn from send: " + who.replace('"', "'")


def _reason_lines(outcome_event, scored):
    lines = []
    reasons = outcome_event["learn_reasons"]
    if "over" in reasons:
        lines.append(f"- **Over-confident:** the estimate ({scored.get('score')}) expected the draft to go "
                     f"out untouched, and it was {outcome_event['disposition']}. "
                     f"Implicated: {', '.join(outcome_event['implicated'])}.")
    if "under" in reasons:
        lines.append(f"- **Under-confident:** the estimate ({scored.get('score')}) expected an edit, and "
                     "Russell sent the draft as-is.")
    if "voice" in reasons:
        lines.append("- **Voice edit:** Russell changed the wording, tone, or structure, so a rule may "
                     "have left that choice unsettled.")
    return lines


def _write_brief(draft_id, scored, outcome_event, sent_text):
    os.makedirs(briefs_dir(), exist_ok=True)
    draft_text = _read_draft_text(draft_id)
    if sent_text is not None:
        diff = "\n".join(difflib.unified_diff(
            draft_text.splitlines(), sent_text.splitlines(), fromfile="draft", tofile="sent", lineterm="",
        ))[:DIFF_CHAR_CAP]
    else:
        diff = "(discarded - no sent text)"

    judge = scored.get("judge") or {}
    features = scored.get("features") or {}
    lines = [
        f"# {_spawn_title(scored)}",
        "",
        f"Follow `{PROCEDURE_PATH}` in this repo - it is the whole procedure for this session.",
        "",
        "## Why this session spawned",
        *_reason_lines(outcome_event, scored),
        "",
        "## The send",
        f"- draft_id: {draft_id}",
        f"- channel: {scored.get('channel')}",
        f"- turns_before_draft: {scored.get('turns_before_draft')}",
        f"- disposition: {outcome_event['disposition']}",
        f"- edit_distance: {outcome_event['edit_distance']}",
        f"- edit labels: {', '.join(outcome_event['edit_nature']) or '(none)'}",
    ]
    if outcome_event.get("reason"):
        lines.append(f"- discard reason: {outcome_event['reason']}")
    if outcome_event.get("changes"):
        lines += ["", "## Changes Russell made"]
        lines += [f"- [{c['label']}] {c.get('summary', '')}" for c in outcome_event["changes"]]

    lines += [
        "",
        "## What the estimate thought",
        f"- composite score: {scored.get('score')}",
        f"- factors (0-100, higher = more likely untouched): {json.dumps(scored.get('factors', {}))}",
    ]
    if features.get("rule_coverage_rating") is not None:
        lines.append(f"- reviewer's rule coverage: {features['rule_coverage_rating']} of 5")
    if scored.get("uncertain_spots"):
        lines += ["", "### Spots the reviewer found the rules didn't settle", scored["uncertain_spots"].strip()]
    if judge.get("rationales"):
        lines += ["", "### Judge rationales"]
        lines += [f"- {k}: {v}" for k, v in judge["rationales"].items()]
    if judge.get("claims"):
        lines += ["", "### Claims"]
        lines += [
            f"- [{'supported' if c.get('supported') else 'UNSUPPORTED'}] ({c.get('source')}) {c.get('text')}"
            for c in judge["claims"]
        ]

    lines += ["", "## Draft (as staged)", "```text", draft_text.rstrip(), "```"]
    if sent_text is not None:
        lines += ["", "## Sent", "```text", sent_text.rstrip(), "```"]
    lines += [
        "",
        "## Diff (draft -> sent)",
        "```diff",
        diff,
        "```",
        "",
        "## Files you may change",
        "- plugins/document-authoring/skills/message-rules/SKILL.md",
        "- plugins/document-authoring/skills/authoring-rules/SKILL.md",
        "- plugins/document-authoring/skills/document-authoring/SKILL.md",
        "- plugins/document-authoring/send-confidence/uncertainty-aspects.md",
        "- plugins/document-authoring/send-confidence/weights.json",
        "",
        "## Commands",
        "- `python plugins/document-authoring/send-confidence/send_confidence.py weights-suggest`",
        f"- log directory: `{data_dir()}`",
    ]
    brief_path = os.path.join(briefs_dir(), f"{draft_id}.md")
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return brief_path


def record_outcome(draft_id, sent_text, *, discarded, edit_nature, reason, moot=False):
    """Compute disposition + edit_distance, label the edits (unless `edit_nature` overrides),
    decide whether the send has something to teach, append the outcome event, and when it
    does, write the learning-session brief. Returns the outcome event plus "dispatch" (the
    spawn command string, or None) and "label_error".

    A learning session spawns when the estimate was wrong in either direction (confident and
    then edited or discarded, or expecting an edit and then sent as-is) and on any voice edit.
    `moot` (discards only - what it does and when to pass it live in learn-from-send.md)."""
    d = fold_drafts().get(draft_id)
    if not d or not d["scored"]:
        raise ValueError(f"no scored draft found for {draft_id}")
    scored = d["scored"]

    thresholds = {**DEFAULT_WEIGHTS["thresholds"], **_load_weights().get("thresholds", {})}
    factor_high = thresholds["factor_high"]
    factor_low = thresholds["factor_low"]
    composite_high = thresholds["composite_high"]
    composite_low = thresholds["composite_low"]
    sent_as_is_max = thresholds["sent_as_is_max_distance"]

    edit_nature = list(edit_nature or [])
    changes = []
    label_error = None
    if discarded:
        disposition = "discarded"
        edit_distance = None
    else:
        draft_text = _read_draft_text(draft_id)
        edit_distance = _edit_distance(draft_text, sent_text)
        disposition = "sent-as-is" if edit_distance <= sent_as_is_max else "edited"
        os.makedirs(drafts_dir(), exist_ok=True)
        with open(os.path.join(drafts_dir(), f"{draft_id}.sent.txt"), "w", encoding="utf-8") as fh:
            fh.write(sent_text or "")
        if disposition == "edited" and not edit_nature:
            edit_nature, changes, label_error = label_edits(draft_text, sent_text, _anthropic_api_key())

    factors = scored.get("factors", {})
    score = scored.get("score")
    implicated = []
    direction = None
    under_factors = []
    learn_reasons = []

    if discarded and moot:
        pass
    elif disposition in ("edited", "discarded"):
        for nature in edit_nature:
            for f in NATURE_TO_FACTORS.get(nature, []):
                sub = factors.get(f)
                if sub is not None and sub >= factor_high and f not in implicated:
                    implicated.append(f)
        if implicated:
            direction = "over"
        elif score is not None and score >= composite_high:
            implicated = ["composite"]
            direction = "over"
        if disposition == "edited" and VOICE_LABELS & set(edit_nature):
            learn_reasons.append("voice")
    elif disposition == "sent-as-is":
        under_factors = [f for f, s in factors.items() if s is not None and s < factor_low]
        if score is not None and score < composite_low:
            direction = "under"

    if direction:
        learn_reasons.insert(0, direction)

    event = {
        "event": "outcome", "v": 2, "draft_id": draft_id,
        "ts": datetime.datetime.now().astimezone().isoformat(),
        "disposition": disposition, "edit_distance": edit_distance,
        "edit_nature": edit_nature, "changes": changes, "reason": reason,
        "miscalibrated": direction is not None, "direction": direction,
        "implicated": implicated, "under_factors": under_factors,
        "learn_reasons": learn_reasons,
    }
    _append_event(event)

    dispatch = None
    if learn_reasons:
        brief_path = _write_brief(draft_id, scored, event, sent_text)
        dispatch = (
            'python <drainer>/skills/drainer/scripts/spawn-handoff.py '
            f'--title "{_spawn_title(scored)}" '
            '--cwd "<plugins repo clone>" '
            f'--brief "{brief_path}" --model sonnet'
        )

    return {**event, "dispatch": dispatch, "label_error": label_error}


def _find_draft_id_by_body_file(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n").strip()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    matches = [e for e in load_events() if e.get("event") == "scored" and e.get("sha256") == sha]
    if not matches:
        return None
    matches.sort(key=lambda e: e.get("ts", ""))
    return matches[-1]["draft_id"]


# --- weight fitting ---

def _point_biserial(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def suggest_weights(min_n=20):
    """Fit proposed weights from every resolved draft in the log (deterministic plain code;
    see learn-from-send.md for how a learning session applies the result).

    Each factor is fitted over the drafts where it came back non-null, and only once it has
    `min_n` of them; a factor short of that keeps its current share, so a newly added factor
    never resets the fit for the others."""
    resolved = []
    for d in fold_drafts().values():
        scored, outcome = d["scored"], d["outcome"]
        if not scored or not outcome or outcome["disposition"] not in RESOLVED_DISPOSITIONS:
            continue
        resolved.append((scored.get("factors", {}), outcome["disposition"] == "sent-as-is"))

    current_doc = _load_weights()
    current = {f: current_doc.get("weights", {}).get(f, 0.2) for f in FACTOR_NAMES}

    correlations = {}
    for f in FACTOR_NAMES:
        pairs = [(factors[f], untouched) for factors, untouched in resolved if factors.get(f) is not None]
        if len(pairs) >= min_n:
            correlations[f] = round(_point_biserial([x for x, _ in pairs],
                                                    [1 if u else 0 for _, u in pairs]), 4)

    n = len(resolved)
    if not correlations:
        return {"n": n, "correlations": {}, "current": current, "proposed": None}

    fitted_share = sum(current[f] for f in correlations)
    targets_raw = {f: max(c, 0.02) for f, c in correlations.items()}
    total_target = sum(targets_raw.values())
    targets = {f: current[f] for f in FACTOR_NAMES}
    targets.update({f: fitted_share * v / total_target for f, v in targets_raw.items()})

    proposed = {}
    for f in FACTOR_NAMES:
        step = max(-0.05, min(0.05, targets[f] - current[f]))
        proposed[f] = max(0.05, min(0.40, current[f] + step))
    total_proposed = sum(proposed.values())
    proposed = {f: round(v / total_proposed, 4) for f, v in proposed.items()}

    moved = any(abs(proposed[f] - current[f]) >= 0.02 for f in FACTOR_NAMES)
    result_proposed = {"version": current_doc.get("version", 1) + 1, "weights": proposed} if moved else None

    return {"n": n, "correlations": correlations, "current": current, "proposed": result_proposed}


# --- CLI ---

def _flag_value(argv, name):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    prefix = name + "="
    for a in argv:
        if a.startswith(prefix):
            return a[len(prefix):]
    return None


def build_context(*, channel, recipient, session_kind, ask, inputs, account=None,
                   thread_ref=None, iid=None, turns_before_draft=0, sent_count=None,
                   rule_coverage=None, uncertain_spots=None):
    return {
        "channel": channel, "account": account, "recipient": recipient,
        "thread_ref": thread_ref, "iid": iid, "session_kind": session_kind,
        "ask": ask, "inputs": (inputs or "")[:INPUTS_CHAR_CAP],
        "turns_before_draft": turns_before_draft, "sent_count": sent_count,
        "rule_coverage": rule_coverage, "uncertain_spots": uncertain_spots,
    }


def cmd_context(argv):
    """The deterministic half of staging: build the context JSON from the caller's judgment
    fields (the ask, the inputs, which channel/recipient, the familiarity count it already
    looked up, the reviewer's rule coverage), write it to a scratch file, and print the exact
    mint command to run next."""
    channel = _flag_value(argv, "--channel")
    recipient = _flag_value(argv, "--recipient")
    session_kind = _flag_value(argv, "--session-kind")
    if not channel or not recipient or not session_kind:
        print("context: --channel, --recipient, and --session-kind are required", file=sys.stderr)
        return 2

    ask = _flag_value(argv, "--ask")
    ask_file = _flag_value(argv, "--ask-file")
    if ask_file:
        with open(ask_file, "r", encoding="utf-8") as fh:
            ask = fh.read()
    inputs = _flag_value(argv, "--inputs")
    inputs_file = _flag_value(argv, "--inputs-file")
    if inputs_file:
        with open(inputs_file, "r", encoding="utf-8") as fh:
            inputs = fh.read()
    if ask is None or inputs is None:
        print("context: --ask/--ask-file and --inputs/--inputs-file are required", file=sys.stderr)
        return 2

    coverage_raw = _flag_value(argv, "--rule-coverage")
    rule_coverage = None
    if coverage_raw is not None:
        if coverage_raw not in {"1", "2", "3", "4", "5"}:
            print("context: --rule-coverage must be 1-5", file=sys.stderr)
            return 2
        rule_coverage = int(coverage_raw)
    spots_file = _flag_value(argv, "--uncertain-spots-file")
    uncertain_spots = None
    if spots_file:
        with open(spots_file, "r", encoding="utf-8") as fh:
            uncertain_spots = fh.read()

    turns_raw = _flag_value(argv, "--turns-before-draft")
    sent_count_raw = _flag_value(argv, "--sent-count")

    ctx = build_context(
        channel=channel, recipient=recipient, session_kind=session_kind, ask=ask, inputs=inputs,
        account=_flag_value(argv, "--account"), thread_ref=_flag_value(argv, "--thread-ref"),
        iid=_flag_value(argv, "--iid"),
        turns_before_draft=int(turns_raw) if turns_raw is not None else 0,
        sent_count=int(sent_count_raw) if sent_count_raw is not None else None,
        rule_coverage=rule_coverage, uncertain_spots=uncertain_spots,
    )

    fd, path = tempfile.mkstemp(prefix="send-confidence-ctx-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ctx, fh)
    print(f"context written: {path}")
    print(f"mint: python {VERIFY_GATE_PATH} mint <body-file> --score-context {path}")
    return 0


def cmd_outcome(argv):
    draft_id = _flag_value(argv, "--draft-id")
    body_file = _flag_value(argv, "--body-file")
    if not draft_id and body_file:
        draft_id = _find_draft_id_by_body_file(body_file)
    if not draft_id:
        print("outcome: could not resolve a draft id (--draft-id or --body-file)", file=sys.stderr)
        return 2

    discarded = "--discarded" in argv
    moot = "--moot" in argv
    reason = _flag_value(argv, "--reason")
    sent_file = _flag_value(argv, "--sent-file")
    edit_nature_raw = _flag_value(argv, "--edit-nature")
    edit_nature = [s.strip() for s in edit_nature_raw.split(",") if s.strip()] if edit_nature_raw else []
    unknown = [s for s in edit_nature if s not in EDIT_LABELS]
    if unknown:
        print(f"outcome: unknown --edit-nature label(s) {unknown}; use {EDIT_LABELS}", file=sys.stderr)
        return 2

    sent_text = None
    if not discarded:
        if not sent_file:
            print("outcome: --sent-file is required unless --discarded", file=sys.stderr)
            return 2
        with open(sent_file, "r", encoding="utf-8") as fh:
            sent_text = fh.read()

    try:
        result = record_outcome(draft_id, sent_text, discarded=discarded,
                                 edit_nature=edit_nature, reason=reason, moot=moot)
    except Exception as e:
        print(f"outcome: {e}", file=sys.stderr)
        return 2

    suffix = f" (edit_distance {result['edit_distance']})" if result["edit_distance"] is not None else ""
    print(f"outcome recorded: {draft_id} -> {result['disposition']}{suffix}")
    if result["edit_nature"]:
        print(f"edit labels: {', '.join(result['edit_nature'])}")
    if result["label_error"]:
        print(f"labelling failed ({result['label_error']}); rerun with --edit-nature "
              f"<{'|'.join(EDIT_LABELS)}> to label the edits yourself")
    if result["miscalibrated"]:
        print(f"miscalibrated: {result['direction']}-confident")
    if result["dispatch"]:
        print(result["dispatch"])
    return 0


def cmd_show(draft_id):
    d = fold_drafts().get(draft_id)
    if not d or not d["scored"]:
        print(f"show: no scored draft found for {draft_id}", file=sys.stderr)
        return 2
    print(json.dumps(d, indent=2))
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: send_confidence.py context|outcome|weights-suggest|show ...", file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "context":
        return cmd_context(rest)
    if verb == "outcome":
        return cmd_outcome(rest)
    if verb == "weights-suggest":
        print(json.dumps(suggest_weights(), indent=2))
        return 0
    if verb == "show":
        if not rest:
            print("show: requires a draft_id", file=sys.stderr)
            return 2
        return cmd_show(rest[0])
    print(f"unknown verb: {verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
