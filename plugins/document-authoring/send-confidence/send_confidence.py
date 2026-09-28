#!/usr/bin/env python3
"""Send-confidence scoring - estimates whether a drafted message will go out untouched.

Stage 1 is score-and-measure only: nothing here gates or auto-sends a message, and no score
is shown per draft. `verify_gate.py mint --score-context` calls `score_and_log` right after
the review receipt is minted; scoring is fail-open (a scorer exception never blocks the
receipt), so this module must never raise past its own CLI entry points.

Five factors, each 0-100 where higher means "more likely to go out untouched": familiarity
(a Sent-folder count proxy), phrasing complexity and task ambiguity (one cold Haiku call per
draft), input completeness (the same call's claim audit), and stylometric fidelity (plain
code over the draft body against a baseline corpus). The composite is the weighted mean over
whichever factors came back non-null - see `weights.json` for the weights and thresholds,
and `calibration.md` for the judge's anchored-scale notes.

The log is an append-only JSONL file per machine under `SEND_CONFIDENCE_DIR`
(`~/OneDrive/Claude/send-confidence` by default - see the module-level path helpers). Nothing
is ever rewritten in place, which is what keeps concurrent per-machine appends over a synced
folder safe; a reader folds each draft's `scored` event with its newest `outcome` event.

CLI:
    python send_confidence.py context --channel <c> --recipient <r> --session-kind <k> \
        (--ask <text> | --ask-file <file>) (--inputs <text> | --inputs-file <file>) \
        [--account <a>] [--thread-ref <t>] [--iid <i>] [--turns-before-draft <n>] \
        [--sent-count <n>]
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --sent-file <file> [--edit-nature a,b]
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --discarded [--reason "<text>"] [--moot]
    python send_confidence.py weights-suggest
    python send_confidence.py show <draft_id>

`context` is the deterministic half of staging a scored draft: it builds the context JSON
`score_and_log` expects, writes it to a scratch file, and prints the exact `verify_gate.py
mint --score-context` command to run next - the drafting session only ever supplies judgment
(the ask, the inputs, which channel and recipient, the familiarity count it looked up), never
hand-authors the JSON or recalls the mint invocation from memory.
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
import statistics
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_PATH = os.path.join(SCRIPT_DIR, "weights.json")
CALIBRATION_PATH = os.path.join(SCRIPT_DIR, "calibration.md")
# send-confidence/ and hooks/ are always siblings under the same plugin checkout, whether
# that's a dev clone or an installed plugin cache snapshot, so this resolves correctly either way.
VERIFY_GATE_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "hooks", "verify_gate.py")

INPUTS_CHAR_CAP = 12000

JUDGE_MODEL = "claude-haiku-4-5"

EMAIL_CHANNELS = {"gmail", "outlook-personal", "outlook-work"}
RESOLVED_DISPOSITIONS = {"sent-as-is", "edited", "discarded"}
FACTOR_NAMES = ["familiarity", "phrasing_complexity", "task_ambiguity",
                "input_completeness", "stylometric"]

NATURE_TO_FACTORS = {
    "factual-correction": ["input_completeness"],
    "tone-voice": ["stylometric", "familiarity"],
    "structural-rewrite": ["task_ambiguity", "phrasing_complexity"],
    "scope-recipient": ["task_ambiguity"],
}

DEFAULT_WEIGHTS = {
    "version": 1,
    "weights": {"familiarity": 0.2, "phrasing_complexity": 0.2, "task_ambiguity": 0.2,
                "input_completeness": 0.2, "stylometric": 0.2},
    "thresholds": {"factor_high": 70, "factor_low": 40, "composite_high": 75,
                   "sent_as_is_max_distance": 0.01},
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


def baseline_path():
    return os.path.join(data_dir(), "voice-baseline.json")


def _load_weights():
    try:
        with open(WEIGHTS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return json.loads(json.dumps(DEFAULT_WEIGHTS))


def _load_baseline():
    try:
        with open(baseline_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


_JUDGMENT_SECTIONS = {"phrasing complexity", "task ambiguity", "input completeness"}


def _judgment_sections(markdown_text):
    """The judge prompt reads only the three judgment-factor sections of calibration.md -
    Familiarity and Stylometric fidelity collect code-formula observations instead."""
    parts = re.split(r"(?m)^(## .+)$", markdown_text or "")
    kept = []
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        title = header[3:].strip().lower()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if title in _JUDGMENT_SECTIONS:
            kept.append(header + body)
    return "\n".join(kept).strip()


def _load_calibration():
    try:
        with open(CALIBRATION_PATH, "r", encoding="utf-8") as fh:
            full = fh.read()
    except Exception:
        full = ""
    sha = hashlib.sha256(full.encode("utf-8")).hexdigest()[:12]
    return _judgment_sections(full), sha


def _anthropic_api_key():
    return os.environ.get("ANTHROPIC_HOOK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")


# --- stylometric features (imported by the baseline builder, so this is the shared source) ---

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
CONTRACTION_RE = re.compile(r"^[A-Za-z]+'[A-Za-z]+$")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")
FIRST_PERSON_WORDS = {"i", "me", "my", "mine", "i'm", "i've", "i'd", "i'll"}
MATTR_WINDOW = 50


def stylometric_features(text):
    """Return {"words", "em_dash", "contraction_rate", "first_person_rate", "sent_len_sd", "mattr"}
    for a body of text, with None for any feature the text is too short to support."""
    text = text or ""
    em_dash = text.count("—")
    tokens = WORD_RE.findall(text)
    words = len(tokens)
    if words == 0:
        return {"words": 0, "em_dash": em_dash, "contraction_rate": None,
                "first_person_rate": None, "sent_len_sd": None, "mattr": None}

    contractions = sum(1 for t in tokens if CONTRACTION_RE.match(t))
    contraction_rate = round(100 * contractions / words, 1)

    first_person = sum(1 for t in tokens if t.lower() in FIRST_PERSON_WORDS)
    first_person_rate = round(100 * first_person / words, 1)

    sentences = [s for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    sent_lengths = [n for n in (len(WORD_RE.findall(s)) for s in sentences) if n > 0]
    sent_len_sd = round(statistics.stdev(sent_lengths), 1) if len(sent_lengths) >= 3 else None

    lower_tokens = [t.lower() for t in tokens]
    if words < MATTR_WINDOW:
        mattr = round(len(set(lower_tokens)) / words, 2)
    else:
        ratios = [
            len(set(lower_tokens[i:i + MATTR_WINDOW])) / MATTR_WINDOW
            for i in range(0, words - MATTR_WINDOW + 1)
        ]
        mattr = round(sum(ratios) / len(ratios), 2)

    return {"words": words, "em_dash": em_dash, "contraction_rate": contraction_rate,
            "first_person_rate": first_person_rate, "sent_len_sd": sent_len_sd, "mattr": mattr}


# --- the judge call ---

JUDGE_SYSTEM = """You are scoring one drafted message for Russell's send-confidence system - \
estimating whether he will send it unedited. Rate two things about the ASK (what he was asked \
to do or say) and list every factual claim in the DRAFT.

## Phrasing complexity
Rate the ask plus the draft on a 1-5 anchored scale:
1 - routine logistics, an acknowledgement, a yes/no.
2 - a plain informational reply with one or two points.
3 - several points to sequence, or light tact needed.
4 - persuasion, a sensitive ask, or pushing back.
5 - delicate: declining, bad news, money, conflict, or anything emotionally loaded.

## Task ambiguity
Rate the ask alone on a 1-5 anchored scale:
1 - Russell said what to say.
2 - the intent and the key content are explicit; only wording is open.
3 - the intent is clear, but the content has to be worked out.
4 - a goal with several plausible approaches.
5 - open-ended ("handle this").

## Claims
List every factual claim in the draft (a name, date, number, link, commitment, or statement of \
fact). Mark each supported when the ask or the inputs state it, or unsupported when the draft \
inferred or invented it.

{calibration_notes}

Respond with ONLY this JSON, no other text:
{{"phrasing_complexity": {{"rating": 1-5, "rationale": "..."}}, "task_ambiguity": {{"rating": 1-5, \
"rationale": "..."}}, "claims": [{{"text": "...", "supported": true, "source": "ask|inputs|none"}}]}}"""


def run_judge(ctx, body, calibration_notes, api_key):
    """One Haiku call scoring phrasing complexity, task ambiguity, and claim support.
    Returns (result_dict, error) - result is None and error is set on any failure, so the
    three judgment factors fall out as null and scoring continues on the other two."""
    if os.environ.get("SEND_CONFIDENCE_JUDGE") == "off":
        return None, "judge disabled (SEND_CONFIDENCE_JUDGE=off)"
    if not api_key:
        return None, "no API key"
    system = JUDGE_SYSTEM.format(calibration_notes=calibration_notes or "")
    user = json.dumps({
        "channel": ctx.get("channel"), "ask": ctx.get("ask", ""),
        "inputs": (ctx.get("inputs") or "")[:INPUTS_CHAR_CAP], "draft": body,
    })
    payload = json.dumps({
        "model": JUDGE_MODEL, "max_tokens": 2048, "temperature": 0,
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
        return None, f"judge call failed: {e}"
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None, "judge returned unparseable output"
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None, "judge returned invalid JSON"
    if not all(k in parsed for k in ("phrasing_complexity", "task_ambiguity", "claims")):
        return None, "judge response missing expected fields"
    return parsed, None


def _judge_log_shape(judge):
    if not judge:
        return None
    return {
        "model": JUDGE_MODEL,
        "rationales": {
            "phrasing_complexity": (judge.get("phrasing_complexity") or {}).get("rationale"),
            "task_ambiguity": (judge.get("task_ambiguity") or {}).get("rationale"),
        },
        "claims": judge.get("claims") or [],
    }


# --- scoring ---

def score_draft(body, ctx, *, weights, baseline, calibration_notes, api_key):
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

    judge, judge_error = run_judge(ctx, body, calibration_notes, api_key)
    if judge_error:
        errors.append(judge_error)

    if judge:
        rating_p = judge["phrasing_complexity"]["rating"]
        factors["phrasing_complexity"] = round(100 * (5 - rating_p) / 4)
        features["complexity_rating"] = rating_p

        rating_a = judge["task_ambiguity"]["rating"]
        turns = ctx.get("turns_before_draft") or 0
        penalty = min(20, 5 * turns)
        factors["task_ambiguity"] = max(0, round(100 * (5 - rating_a) / 4) - penalty)
        features["ambiguity_rating"] = rating_a
        features["ambiguity_turn_penalty"] = penalty

        claims = judge.get("claims") or []
        total = len(claims)
        supported = sum(1 for c in claims if c.get("supported"))
        factors["input_completeness"] = 100 if total == 0 else round(100 * supported / total)
        features["claims_total"] = total
        features["claims_unsupported"] = total - supported
    else:
        factors["phrasing_complexity"] = None
        factors["task_ambiguity"] = None
        factors["input_completeness"] = None

    style = stylometric_features(body)
    features.update(style)
    words = style["words"]
    if words < 25:
        factors["stylometric"] = None
    else:
        sub_scores = [100 if style["em_dash"] == 0 else 0]
        # Stage 1's baseline corpus is email-only, so every channel scores against the
        # "email" group; a chat draft just skips the two length-sensitive features below.
        channel_group = "email" if ctx.get("channel") in EMAIL_CHANNELS else "chat"
        group_stats = ((baseline or {}).get("groups") or {}).get("email", {}).get("features", {})
        zscored = ["contraction_rate", "first_person_rate"]
        if channel_group == "email":
            zscored += ["sent_len_sd", "mattr"]
        for name in zscored:
            x = style.get(name)
            stats = group_stats.get(name)
            if x is None or not stats or not stats.get("sd"):
                continue
            z = (x - stats["mean"]) / stats["sd"]
            sub_scores.append(round(100 * max(0, 1 - abs(z) / 3)))
        factors["stylometric"] = round(sum(sub_scores) / len(sub_scores))

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


def fold_drafts(events=None):
    """{draft_id: {"scored": event, "outcome": event_or_None}}, each draft's scored event
    folded with its newest outcome event."""
    events = events if events is not None else load_events()
    drafts = {}
    for e in events:
        did = e.get("draft_id")
        kind = e.get("event")
        if not did or kind not in ("scored", "outcome"):
            continue
        d = drafts.setdefault(did, {"scored": None, "outcome": None})
        if kind == "scored":
            d["scored"] = e
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
                "implicated": [], "under_factors": [],
            })


def score_and_log(body, sha256, ctx_path):
    """Load ctx, weights.json, calibration.md, the baseline; call score_draft; snapshot the
    body to drafts/<draft_id>.txt; append a superseded outcome for any earlier pending draft
    with the same thread_ref + recipient; append the scored event; return the draft_id."""
    with open(ctx_path, "r", encoding="utf-8") as fh:
        ctx = json.load(fh)

    weights = _load_weights()
    baseline = _load_baseline()
    calibration_notes, calibration_sha = _load_calibration()
    api_key = _anthropic_api_key()

    result = score_draft(body, ctx, weights=weights, baseline=baseline,
                          calibration_notes=calibration_notes, api_key=api_key)

    ts = datetime.datetime.now().astimezone()
    draft_id = f"d-{sha256[:12]}-{ts.strftime('%Y%m%d%H%M%S')}"

    os.makedirs(drafts_dir(), exist_ok=True)
    with open(os.path.join(drafts_dir(), f"{draft_id}.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)

    _supersede_pending(ctx.get("thread_ref"), ctx.get("recipient"))

    event = {
        "event": "scored", "v": 1, "draft_id": draft_id,
        "ts": ts.isoformat(), "machine": socket.gethostname(), "sha256": sha256,
        "channel": ctx.get("channel"), "account": ctx.get("account"),
        "recipient": ctx.get("recipient"), "thread_ref": ctx.get("thread_ref"),
        "iid": ctx.get("iid"), "session_kind": ctx.get("session_kind"),
        "turns_before_draft": ctx.get("turns_before_draft"),
        "score": result["score"], "weights_version": weights.get("version"),
        "calibration_sha": calibration_sha,
        "factors": result["factors"], "features": result["features"],
        "judge": _judge_log_shape(result["judge"]),
        "errors": result["errors"],
    }
    _append_event(event)
    return draft_id


def _edit_distance(draft_text, sent_text):
    a = " ".join((draft_text or "").split())
    b = " ".join((sent_text or "").split())
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    return round(1 - ratio, 4)


def _write_brief(draft_id, scored, outcome_event, sent_text):
    os.makedirs(briefs_dir(), exist_ok=True)
    draft_text = _read_draft_text(draft_id)
    if sent_text is not None:
        diff = "\n".join(difflib.unified_diff(
            draft_text.splitlines(), sent_text.splitlines(), fromfile="draft", tofile="sent", lineterm="",
        ))
    else:
        diff = "(discarded - no sent text)"

    judge = scored.get("judge") or {}
    lines = [
        f"# Calibrate send-confidence: {', '.join(outcome_event['implicated'])}",
        "",
        f"- draft_id: {draft_id}",
        f"- channel: {scored.get('channel')}",
        f"- turns_before_draft: {scored.get('turns_before_draft')}",
        f"- composite score: {scored.get('score')}",
        f"- factors: {json.dumps(scored.get('factors', {}))}",
        f"- disposition: {outcome_event['disposition']}",
        f"- edit_nature: {outcome_event['edit_nature']}",
    ]
    if outcome_event.get("reason"):
        lines.append(f"- discard reason: {outcome_event['reason']}")
    if judge.get("rationales"):
        lines += ["", "## Judge rationales"]
        lines += [f"- {k}: {v}" for k, v in judge["rationales"].items()]
    if judge.get("claims"):
        lines += ["", "## Claims"]
        lines += [
            f"- [{'supported' if c.get('supported') else 'UNSUPPORTED'}] ({c.get('source')}) {c.get('text')}"
            for c in judge["claims"]
        ]
    lines += [
        "",
        "## Implicated factors",
        f"{outcome_event['implicated']}",
        "",
        "## Diff (draft -> sent)",
        "```diff",
        diff,
        "```",
        "",
        "## Files you may change",
        "- send-confidence/calibration.md",
        "- send-confidence/weights.json",
        "- skills/message-rules/SKILL.md",
        "- skills/document-authoring/SKILL.md",
        "",
        "## Commands",
        "- `python send_confidence.py weights-suggest`",
        f"- log directory: `{data_dir()}`",
    ]
    brief_path = os.path.join(briefs_dir(), f"{draft_id}.md")
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return brief_path


def record_outcome(draft_id, sent_text, *, discarded, edit_nature, reason, moot=False):
    """Compute disposition + edit_distance, detect miscalibration, append the outcome event,
    and when miscalibrated write the calibration brief. Returns the outcome event plus
    "dispatch" (the spawn command string, or None).

    `moot` (discards only - what it does and when to pass it live in send-confidence.md)."""
    d = fold_drafts().get(draft_id)
    if not d or not d["scored"]:
        raise ValueError(f"no scored draft found for {draft_id}")
    scored = d["scored"]

    thresholds = _load_weights().get("thresholds", DEFAULT_WEIGHTS["thresholds"])
    factor_high = thresholds.get("factor_high", 70)
    factor_low = thresholds.get("factor_low", 40)
    composite_high = thresholds.get("composite_high", 75)
    sent_as_is_max = thresholds.get("sent_as_is_max_distance", 0.01)

    edit_nature = edit_nature or []
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

    factors = scored.get("factors", {})
    implicated = []
    direction = None
    under_factors = []

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
        elif scored.get("score") is not None and scored["score"] >= composite_high:
            implicated = ["composite"]
            direction = "over"
    elif disposition == "sent-as-is":
        under_factors = [f for f, s in factors.items() if s is not None and s < factor_low]

    event = {
        "event": "outcome", "v": 1, "draft_id": draft_id,
        "ts": datetime.datetime.now().astimezone().isoformat(),
        "disposition": disposition, "edit_distance": edit_distance,
        "edit_nature": edit_nature, "reason": reason,
        "miscalibrated": direction == "over", "direction": direction,
        "implicated": implicated, "under_factors": under_factors,
    }
    _append_event(event)

    dispatch = None
    if direction == "over":
        brief_path = _write_brief(draft_id, scored, event, sent_text)
        dispatch = (
            'python <drainer>/skills/drainer/scripts/spawn-handoff.py '
            f'--title "Calibrate send-confidence: {", ".join(implicated)}" '
            '--cwd "<plugins repo clone>" '
            f'--brief "{brief_path}" --model claude-sonnet-5'
        )

    return {**event, "dispatch": dispatch}


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
    see calibration.md's Weights section for how a session applies the result)."""
    resolved = []
    for d in fold_drafts().values():
        scored, outcome = d["scored"], d["outcome"]
        if not scored or not outcome or outcome["disposition"] not in RESOLVED_DISPOSITIONS:
            continue
        factors = scored.get("factors", {})
        if any(factors.get(f) is None for f in FACTOR_NAMES):
            continue
        resolved.append((factors, outcome["disposition"] == "sent-as-is"))

    current_doc = _load_weights()
    current = dict(current_doc.get("weights", {}))
    n = len(resolved)
    if n < min_n:
        return {"n": n, "correlations": {}, "current": current, "proposed": None}

    ys = [1 if untouched else 0 for _, untouched in resolved]
    correlations = {
        f: round(_point_biserial([factors[f] for factors, _ in resolved], ys), 4)
        for f in FACTOR_NAMES
    }

    targets_raw = {f: max(correlations[f], 0.02) for f in FACTOR_NAMES}
    total_target = sum(targets_raw.values())
    targets = {f: v / total_target for f, v in targets_raw.items()}

    proposed = {}
    for f in FACTOR_NAMES:
        cur = current.get(f, 0.2)
        step = max(-0.05, min(0.05, targets[f] - cur))
        proposed[f] = max(0.05, min(0.40, cur + step))
    total_proposed = sum(proposed.values())
    proposed = {f: round(v / total_proposed, 4) for f, v in proposed.items()}

    moved = any(abs(proposed[f] - current.get(f, 0.2)) >= 0.02 for f in FACTOR_NAMES)
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
                   thread_ref=None, iid=None, turns_before_draft=0, sent_count=None):
    return {
        "channel": channel, "account": account, "recipient": recipient,
        "thread_ref": thread_ref, "iid": iid, "session_kind": session_kind,
        "ask": ask, "inputs": (inputs or "")[:INPUTS_CHAR_CAP],
        "turns_before_draft": turns_before_draft, "sent_count": sent_count,
    }


def cmd_context(argv):
    """The deterministic half of staging: build the context JSON from the caller's judgment
    fields (the ask, the inputs, which channel/recipient, the familiarity count it already
    looked up), write it to a scratch file, and print the exact mint command to run next."""
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

    turns_raw = _flag_value(argv, "--turns-before-draft")
    sent_count_raw = _flag_value(argv, "--sent-count")

    ctx = build_context(
        channel=channel, recipient=recipient, session_kind=session_kind, ask=ask, inputs=inputs,
        account=_flag_value(argv, "--account"), thread_ref=_flag_value(argv, "--thread-ref"),
        iid=_flag_value(argv, "--iid"),
        turns_before_draft=int(turns_raw) if turns_raw is not None else 0,
        sent_count=int(sent_count_raw) if sent_count_raw is not None else None,
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
    if result["miscalibrated"]:
        print(f"miscalibrated: over-confident on {result['implicated']}")
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
