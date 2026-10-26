#!/usr/bin/env python3
"""Send-confidence scoring - estimates how likely a drafted message is to need a change
before Russell sends it, and decides when a sent draft has something to teach.

Stage 1 is score-and-measure only: nothing here gates or auto-sends a message, and no score
is shown per draft. The stage gate is the one place a draft gets scored: it calls
`record_stage` for every stage it lets through, which logs a `staged` event and, when the hash
has no `scored` row yet, launches a detached `auto-score` process. That process builds the
context itself - channel, recipient, thread, and account from the stage command; the reviewer's
rule coverage and uncertain spots from the review receipt; the ask, the inputs, and the turn
count from the session transcript; familiarity from earlier stages in this log - and calls
`score_and_log`. Scoring is fail-open (a scorer exception never blocks the stage), so this
module must never raise past its own CLI entry points.

Five factors, each 0-100 where higher means "more likely to go out untouched", one per aspect
of uncertainty described in `uncertainty-aspects.md`: rule coverage (the writing reviewer's
rating, carried on the receipt), content latitude and stakes (one cold Haiku call per draft,
which reads that file whole), fact support (the same call's claim audit against the
transcript), and familiarity (how many distinct drafts this log has seen staged to the same
recipient). The composite is the weighted mean over whichever factors came back non-null -
see `weights.json` for the weights and thresholds.

The log is an append-only JSONL file per machine under `SEND_CONFIDENCE_DIR`
(`~/OneDrive/Claude/send-confidence` by default - see the module-level path helpers). Nothing
is ever rewritten in place, which is what keeps concurrent per-machine appends over a synced
folder safe; a reader folds each draft's `scored` event with its newest `outcome` event.

CLI:
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --sent-file <file> [--edit-nature a,b]
    python send_confidence.py outcome (--draft-id <id> | --body-file <staged body>) \
        --discarded [--reason "<text>"] [--moot]
    python send_confidence.py sweep [--dry-run] [--json]
    python send_confidence.py weights-suggest
    python send_confidence.py show <draft_id>
    python send_confidence.py auto-score <job.json>   (launched by the stage gate, not by hand)

`outcome` is the whole of the drafting session's part in learning from a send: it labels the
edits (one Haiku call, unless `--edit-nature` overrides), records the outcome, and prints a
spawn command for a learning session whenever the send has something to teach - see
`learn-from-send.md` for what that session does.
"""

import os
import re
import sys
import glob
import html
import json
import math
import socket
import difflib
import hashlib
import datetime
import subprocess
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEIGHTS_PATH = os.path.join(SCRIPT_DIR, "weights.json")
ASPECTS_PATH = os.path.join(SCRIPT_DIR, "uncertainty-aspects.md")
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

    # With no ask and no inputs (the transcript was unreadable), the judge has nothing to
    # measure latitude against and every claim would read as unsupported; those two factors
    # stay null and the composite re-weights over the rest.
    if judge and (ctx.get("ask") or ctx.get("inputs")):
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
        factors.setdefault("stakes", None)
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


def _plain_text(text):
    """Strip HTML markup from a staged body, so a Gmail draft staged as `<p>...</p>` compares
    equal to the plain text the sent item reads back as."""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _read_draft_text(draft_id):
    with open(os.path.join(drafts_dir(), f"{draft_id}.txt"), "r", encoding="utf-8") as fh:
        return _plain_text(fh.read())


def _same_conversation(scored, thread_ref, recipient):
    """True when a pending row is an earlier version of the draft at (thread_ref, recipient).
    Within one thread a recipient the row or the new draft doesn't know (a reply's stage
    command carries the thread but not the addressee) still matches; outside a thread the
    recipient must match exactly."""
    if thread_ref:
        if scored.get("thread_ref") != thread_ref:
            return False
        other = scored.get("recipient")
        return other == recipient or other is None or recipient is None
    return not scored.get("thread_ref") and recipient is not None and scored.get("recipient") == recipient


def _supersede_pending(thread_ref, recipient):
    """Mark every pending draft for the same conversation superseded. A direct send (a Slack
    `--send`) went out as written, so it is never an earlier version of a later draft."""
    if not thread_ref and not recipient:
        return
    ts = datetime.datetime.now().astimezone().isoformat()
    for draft_id, d in fold_drafts().items():
        scored = d["scored"]
        if not scored or d["outcome"] is not None or scored.get("stage_kind") == "send":
            continue
        if _same_conversation(scored, thread_ref, recipient):
            _append_event(_superseded_event(draft_id, ts))


def _superseded_event(draft_id, ts):
    return {
        "event": "outcome", "v": 1, "draft_id": draft_id, "ts": ts,
        "disposition": "superseded", "edit_distance": None, "edit_nature": [],
        "reason": None, "miscalibrated": False, "direction": None,
        "implicated": [], "under_factors": [], "learn_reasons": [],
    }


def score_and_log(body, sha256, ctx):
    """Load weights.json and uncertainty-aspects.md; call score_draft; snapshot the body to
    drafts/<draft_id>.txt; append a superseded outcome for any earlier pending draft in the
    same conversation; append the scored event; return the draft_id."""
    weights = _load_weights()
    aspects, aspects_sha = _load_aspects()
    api_key = _anthropic_api_key()

    result = score_draft(body, ctx, weights=weights, aspects=aspects, api_key=api_key)

    ts = datetime.datetime.now().astimezone()
    draft_id = f"d-{sha256[:12]}-{ts.strftime('%Y%m%d%H%M%S')}"

    os.makedirs(drafts_dir(), exist_ok=True)
    with open(os.path.join(drafts_dir(), f"{draft_id}.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)

    if ctx.get("stage_kind") != "send":
        _supersede_pending(ctx.get("thread_ref"), ctx.get("recipient"))

    event = {
        "event": "scored", "v": 3, "draft_id": draft_id,
        "ts": ts.isoformat(), "machine": socket.gethostname(), "sha256": sha256,
        "source": "gate", "stage_kind": ctx.get("stage_kind"),
        "session_id": ctx.get("session_id"),
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


# --- scoring at stage time ---

STALE_JOB_SECONDS = 3600
ASK_CHAR_CAP = 6000

_TAG_BLOCK = re.compile(
    r"<(system-reminder|task-notification|command-[a-z-]+|local-command-[a-z-]+)>[\s\S]*?</\1>")


def _block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def transcript_context(path):
    """The ask, the inputs, and the turn count, read from the session transcript the hook
    payload points at. The ask is what Russell (or a background session's seed) typed: every
    human message, harness-injected tags stripped, its most recent part kept. The inputs are
    the tool results the session saw, most recent kept. turns_before_draft counts the human
    messages after the first. Returns all-None fields when the transcript can't be read."""
    out = {"ask": None, "inputs": None, "turns_before_draft": None, "session_kind": None}
    if not path or not os.path.isfile(path):
        return out
    asks, inputs = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("sessionKind") and not out["session_kind"]:
                out["session_kind"] = "background" if e["sessionKind"] == "bg" else e["sessionKind"]
            if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain") or e.get("isCompactSummary"):
                continue
            content = (e.get("message") or {}).get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        text = _block_text(b.get("content"))
                        if text.strip():
                            inputs.append(text.strip())
                continue
            text = _TAG_BLOCK.sub("", _block_text(content)).strip()
            if text:
                asks.append(text)
    if asks:
        out["ask"] = "\n\n".join(asks)[-ASK_CHAR_CAP:]
        out["turns_before_draft"] = len(asks) - 1
    if inputs:
        out["inputs"] = "\n\n".join(inputs)[-INPUTS_CHAR_CAP:]
    return out


def prior_session_count(recipient, session_id, events=None):
    """Familiarity: how many other sessions have staged a draft to this exact recipient. A
    session counts once however many revisions it staged. None when the stage named no
    recipient (a reply carries its thread, not its addressee) or the recipient is Russell."""
    if not recipient or recipient == "self":
        return None
    events = events if events is not None else load_events()
    sessions = {e.get("session_id") for e in events
                if e.get("event") == "staged" and e.get("recipient") == recipient
                and e.get("session_id") and e.get("session_id") != session_id}
    return len(sessions)


def pending_dir():
    return os.path.join(data_dir(), "pending")


def has_scored(sha256):
    return any(e.get("event") == "scored" and e.get("sha256") == sha256 for e in load_events())


def scoring_body(text, body_format):
    """The message text to score. A work-Outlook stage hashes its whole JSON payload, so the
    body (or a reply's comment) is pulled out of it and stripped of markup."""
    if body_format == "outlook-json":
        try:
            spec = json.loads(text)
            return _plain_text(spec.get("body") or spec.get("comment") or "")
        except Exception:
            return text
    return text


def _spawn_detached(args):
    """Start `args` so it outlives the hook that launched it, with no console and no handle
    on the hook's pipes. On Windows it leaves the hook's job object when the job permits
    breakaway, so the harness closing that job can't take the scorer with it."""
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            return subprocess.Popen(args, creationflags=flags, **kwargs)
    return subprocess.Popen(args, start_new_session=True, **kwargs)


def _claim_job(path, job):
    """Write the job file only if no live job holds that hash, so two quick stages of the
    same draft launch one scorer. A job left behind by a crashed scorer goes stale after an
    hour and is taken over."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fh = open(path, "x", encoding="utf-8")
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(path) < STALE_JOB_SECONDS:
                return False
        except OSError:
            return False
        fh = open(path, "w", encoding="utf-8")
    with fh:
        json.dump(job, fh)
    return True


def record_stage(text, sha256, meta, wait=False):
    """The stage gate's call for every stage it lets through: append a `staged` event, and
    when that hash has no `scored` row yet, queue a detached scorer. Returns the job path
    when a scorer launched, else None. `wait` scores in this process instead, for a caller
    that records the outcome right after (a script that sends from inside itself)."""
    ts = datetime.datetime.now().astimezone().isoformat()
    _append_event({
        "event": "staged", "v": 1, "ts": ts, "machine": socket.gethostname(),
        "sha256": sha256, "channel": meta.get("channel"), "account": meta.get("account"),
        "recipient": meta.get("recipient"), "thread_ref": meta.get("thread_ref"),
        "stage_kind": meta.get("stage_kind"), "script": meta.get("script"),
        "verb": meta.get("verb"), "session_id": meta.get("session_id"),
        "session_kind": meta.get("session_kind"),
    })
    if has_scored(sha256):
        return None
    ctx = {
        "channel": meta.get("channel"), "account": meta.get("account"),
        "recipient": meta.get("recipient"), "thread_ref": meta.get("thread_ref"),
        "iid": None, "session_kind": meta.get("session_kind"),
        "session_id": meta.get("session_id"), "stage_kind": meta.get("stage_kind"),
        "rule_coverage": meta.get("rule_coverage"), "uncertain_spots": meta.get("uncertain_spots"),
        "ask": meta.get("ask"), "inputs": (meta.get("inputs") or "")[-INPUTS_CHAR_CAP:] or None,
    }
    job = {"sha256": sha256, "body": scoring_body(text, meta.get("body_format")), "ctx": ctx,
           "transcript_path": meta.get("transcript_path")}
    path = os.path.join(pending_dir(), f"{sha256}.json")
    if not _claim_job(path, job):
        return None
    if wait:
        cmd_auto_score(path)
    else:
        _spawn_detached([sys.executable, os.path.abspath(__file__), "auto-score", path])
    return path


def cmd_auto_score(job_path):
    """The detached scorer: fill in the context the stage command couldn't carry (the ask,
    the inputs, and the turn count from the transcript; familiarity from the log), score the
    queued draft unless a row for its hash landed in the meantime, then remove the job.
    Always exits 0 - nobody is waiting on it."""
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)
        delay = os.environ.get("SEND_CONFIDENCE_AUTO_DELAY")
        if delay:
            time.sleep(float(delay))
        if not has_scored(job["sha256"]):
            ctx = dict(job["ctx"])
            if not (ctx.get("ask") or ctx.get("inputs")):
                try:
                    found = transcript_context(job.get("transcript_path"))
                except Exception:
                    found = {}
                ctx["ask"] = found.get("ask")
                ctx["inputs"] = found.get("inputs")
                ctx["turns_before_draft"] = found.get("turns_before_draft")
                ctx["session_kind"] = found.get("session_kind") or ctx.get("session_kind")
            ctx["sent_count"] = prior_session_count(ctx.get("recipient"), ctx.get("session_id"))
            score_and_log(job["body"], job["sha256"], ctx)
    except Exception:
        pass
    finally:
        try:
            os.remove(job_path)
        except OSError:
            pass
    return 0


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
    if "voice" in reasons:
        lines.append("- **Voice edit:** Russell changed the wording, tone, or structure, so a rule may "
                     "have left that choice unsettled.")
    return lines


def redline(draft_text, sent_text):
    """One merged text: words Russell removed struck through, words he added in bold italics."""
    a, b = re.findall(r"\S+|\s+", draft_text), re.findall(r"\S+|\s+", sent_text)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append("".join(a[i1:i2]))
            continue
        for tokens, mark in ((a[i1:i2], "~~"), (b[j1:j2], "***")):
            text = "".join(tokens)
            if text.strip():
                lead = text[:len(text) - len(text.lstrip())]
                tail = text[len(text.rstrip()):]
                out.append(f"{lead}{mark}{text.strip()}{mark}{tail}")
    return "".join(out)


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
        "This repo is public. Nothing below - the recipient, the draft, the sent text, the diff, or any",
        "name, amount, or detail from them - goes into a file, branch name, commit message, or PR body.",
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
    if sent_text is not None:
        lines += ["", "## Redline (removed ~~struck~~, added ***bold italic***)", redline(draft_text, sent_text).rstrip()]
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
        "",
        "## Commands",
        f"- log directory: `{data_dir()}`",
    ]
    brief_path = os.path.join(briefs_dir(), f"{draft_id}.md")
    with open(brief_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return brief_path


def record_outcome(draft_id, sent_text, *, discarded, edit_nature, reason, moot=False,
                   sweep=None, recipient=None):
    """Compute disposition + edit_distance, label the edits (unless `edit_nature` overrides),
    decide whether the send has something to teach, append the outcome event, and when it
    does, write the learning-session brief. Returns the outcome event plus "dispatch" (the
    spawn command string, or None) and "label_error".

    A learning session spawns on a voice edit only. Whether the estimate was wrong in either
    direction is still logged (`direction`, `miscalibrated`), and nothing here changes the
    score's numbers.
    `moot` (discards only - what it does and when to pass it live in learn-from-send.md).
    `recipient` names who the sent message went to when the staged draft didn't carry it (a
    reply's stage command names its thread, not its addressee); it labels the learning session.
    `sweep` is the outcome sweep's own record of the match ({"edit_distance", "sent_ref"} for a
    send, {} for a draft that never went out): the distance it measured stands in for the
    plain one, the edits stay unlabelled, no learning session spawns, and the event carries
    `source: "sweep"` so the report can tell swept outcomes from recorded ones."""
    d = fold_drafts().get(draft_id)
    if not d or not d["scored"]:
        raise ValueError(f"no scored draft found for {draft_id}")
    scored = d["scored"]
    if recipient and not scored.get("recipient"):
        scored = {**scored, "recipient": recipient}

    thresholds ={**DEFAULT_WEIGHTS["thresholds"], **_load_weights().get("thresholds", {})}
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
        if sweep is not None:
            edit_distance = sweep["edit_distance"]
        else:
            edit_distance = _edit_distance(draft_text, sent_text)
        disposition = "sent-as-is" if edit_distance <= sent_as_is_max else "edited"
        os.makedirs(drafts_dir(), exist_ok=True)
        with open(os.path.join(drafts_dir(), f"{draft_id}.sent.txt"), "w", encoding="utf-8") as fh:
            fh.write(sent_text or "")
        if disposition == "edited" and not edit_nature and sweep is None:
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

    event = {
        "event": "outcome", "v": 2, "draft_id": draft_id,
        "ts": datetime.datetime.now().astimezone().isoformat(),
        "disposition": disposition, "edit_distance": edit_distance,
        "edit_nature": edit_nature, "changes": changes, "reason": reason,
        "miscalibrated": direction is not None, "direction": direction,
        "implicated": implicated, "under_factors": under_factors,
        "learn_reasons": learn_reasons,
    }
    if sweep is not None:
        event["source"] = "sweep"
        event["sent_ref"] = sweep.get("sent_ref")
    _append_event(event)

    dispatch = None
    if learn_reasons and sweep is None:
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


# --- the outcome sweep ---
#
# Plain code, no model calls. Every scored draft with no outcome is matched against what Russell
# actually sent - Gmail and personal Outlook Sent folders and his Slack posts, read through those
# plugins' own read-only `--sent-since` listings - and the match is written as an ordinary outcome
# event. A draft whose window closes with no match is logged as discarded with no reason, the
# same as a voluntary discard with no cause given.

SWEEP_WINDOW_DAYS = 7
# A sent message can carry a timestamp a little before the draft's scored row: a direct Slack
# send posts while its scorer is still running.
SWEEP_EARLY_SLACK = datetime.timedelta(hours=1)
# Farthest a match may sit from the draft: looser when the thread or recipient confirms it is the
# same conversation, stricter when the draft carries nothing to check that against.
SWEEP_MAX_DISTANCE_CONFIRMED = 0.6
SWEEP_MAX_DISTANCE_UNCONFIRMED = 0.35

# Channel -> (plugin, script) whose `--sent-since <day> --json` lists the sent messages.
SWEEP_CLIENTS = {
    "gmail": ("gmail", "gmail.js"),
    "outlook-personal": ("ms-graph", "mail.js"),
    "slack": ("slack", "slack.js"),
}
GMAIL_NAMED_ACCOUNTS = {"christina"}

_QUOTE_MARKERS = [
    re.compile(r"^\s*On [^\n]{0,300}?(\n[^\n]{0,300}?)?wrote:\s*$", re.M),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^\s*_{10,}\s*$", re.M),
    re.compile(r"^\s*From: [^\n]+\n\s*(Sent|Date): ", re.M),
    re.compile(r"^-- $", re.M),
]
_SLACK_CHANNEL = re.compile(r"^[CDG][A-Z0-9]{6,}$")
_SLACK_ITEM_REF = re.compile(r"^slack-([CDG][A-Z0-9]{6,})-")


def _client_script(plugin, script):
    """The newest copy of a sibling plugin's script: the repo layout (plugins/<plugin>/skills/...)
    or the installed cache layout (<marketplace>/<plugin>/<version>/skills/...)."""
    patterns = [
        os.path.join(SCRIPT_DIR, "..", "..", plugin, "skills", plugin, "scripts", script),
        os.path.join(SCRIPT_DIR, "..", "..", "..", plugin, "*", "skills", plugin, "scripts", script),
    ]
    found = [p for pat in patterns for p in glob.glob(pat)]
    return max(found, key=os.path.getmtime) if found else None


def fetch_sent(channel, since_day):
    """Run one client's read-only `--sent-since` listing and return its JSON. Raises on any
    failure, so a source that can't be read leaves its drafts pending rather than unsent."""
    plugin, script = SWEEP_CLIENTS[channel]
    path = _client_script(plugin, script)
    if not path:
        raise RuntimeError(f"{plugin} client {script} not found")
    args = ["node", path, f"--sent-since={since_day}", "--json"]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"{plugin} {script} exited {r.returncode}: {(r.stderr or '').strip()[-300:]}")
    return json.loads(r.stdout or "[]")


def _source_key(scored):
    """The channel a draft would have gone out on, or None when the sweep can't read it: a
    channel with no client, or a second Gmail mailbox, since the Gmail listing reads the
    default mailbox over IMAP."""
    channel = scored.get("channel")
    if channel not in SWEEP_CLIENTS:
        return None
    if channel == "gmail" and scored.get("account") in GMAIL_NAMED_ACCOUNTS:
        return None
    return channel


def _sent_items(channel, raw):
    """A client's listing in one shape: ref, sent (aware datetime), text, and the thread and
    recipient keys a draft can be checked against."""
    items = []
    for m in raw or []:
        sent = _parse_ts(m.get("sent"))
        if sent is None:
            continue
        if channel == "slack":
            items.append({"ref": f"{m.get('channel')}/{m.get('ts')}", "sent": sent, "text": m.get("text") or "",
                          "threads": {m.get("ts"), m.get("threadTs")} - {None, ""},
                          "recipients": {m.get("channel")} - {None, ""}})
        else:
            threads = set(m.get("references") or [])
            threads |= {m.get("inReplyTo"), m.get("threadId")} - {None, ""}
            items.append({"ref": m.get("id"), "sent": sent, "text": m.get("text") or "",
                          "threads": threads,
                          "recipients": {a.lower() for a in (m.get("to") or []) + (m.get("cc") or []) if a}})
    return items


def _draft_keys(scored):
    """What a sent message is checked against: its thread ids, its recipient addresses or
    channel, and the words of a recipient given by name rather than address.

    A Gmail thread_ref is either the RFC822 Message-ID the reply threads off, which the sent
    reply's References chain carries, or the hex Gmail thread id. An Outlook thread_ref is a
    Graph message id the sent item doesn't expose, so Outlook checks recipients only. A Slack
    draft's conversation is its channel id, from the recipient or from a drainer item ref
    (`slack-<channel>-<sec>-<frac>`), and its thread is the ts that ref or a bare thread ts
    names. That ts can be a reply inside the thread rather than its root, so for Slack a
    thread match confirms and a thread miss proves nothing (`thread_vetoes`)."""
    channel = scored.get("channel")
    thread_ref = scored.get("thread_ref") or ""
    recipient = scored.get("recipient") or ""
    keys = {"threads": set(), "recipients": set(), "names": set(), "thread_vetoes": channel != "slack"}
    if channel == "slack":
        m = _SLACK_ITEM_REF.match(thread_ref)
        chan = recipient if _SLACK_CHANNEL.match(recipient) else (m.group(1) if m else None)
        keys["recipients"] = {chan} if chan else set()
        ts = re.search(r"(\d{9,})[-.](\d+)$", thread_ref)
        keys["threads"] = {f"{ts.group(1)}.{ts.group(2)}"} if ts else set()
        return keys
    if channel == "gmail" and re.match(r"^(<[^>]+@[^>]+>|[0-9a-f]{16})$", thread_ref):
        keys["threads"] = {thread_ref}
    keys["recipients"] = {a.strip().lower() for a in recipient.split(",") if "@" in a}
    if not keys["recipients"]:
        keys["names"] = {w for w in re.findall(r"[a-z]{3,}", recipient.lower())}
    return keys


def _conversation(keys, item):
    """True when the thread, recipients, or recipient name confirm the item belongs to the
    draft's conversation, False when everything both sides know disagrees, None when there is
    nothing to compare. A name confirms when two of its words are words of one recipient
    address's local part ("Rob Tuley" and rob.tuley@...), and never disagrees, since a name can
    be partial."""
    checks = []
    if keys["threads"] and item["threads"]:
        if keys["threads"] & item["threads"]:
            return True
        if keys["thread_vetoes"]:
            checks.append(False)
    if keys["recipients"] and item["recipients"]:
        checks.append(bool(keys["recipients"] & item["recipients"]))
    if keys["names"] and any(len(keys["names"] & set(re.split(r"[^a-z]+", a.split("@")[0]))) >= 2
                             for a in item["recipients"]):
        return True
    if not checks:
        return None
    return any(checks)


def _signature_text():
    """The Gmail signature gmail.js appends at send, reduced the way comparable_text reduces a
    body, so it can be cut from a sent message the staged draft never carried it in."""
    sig = os.environ.get("GMAIL_SIGNATURE_HTML")
    return comparable_text(sig) if sig else ""


def comparable_text(text, signature=""):
    """The part of a message a draft can be compared with: the quoted original and the
    signature (itself already reduced by this function) cut off, and Markdown, Slack mrkdwn,
    and links reduced to their words, so a staged `[anchor](url)` and a sent `anchor <url>`
    read the same."""
    # Slack's `<url|label>` and a mail client's `<url>` go before the markup strip, which would
    # read them as tags and drop the label with them.
    text = re.sub(r"<(?:https?|mailto):[^|>]*\|([^>]+)>", r"\1", text or "")
    text = re.sub(r"<(?:https?|mailto):[^>]*>", " ", text)
    text = _plain_text(text).replace("\r\n", "\n")
    cut = min((m.start() for m in (p.search(text) for p in _QUOTE_MARKERS) if m), default=len(text))
    text = "\n".join(line for line in text[:cut].split("\n") if not line.lstrip().startswith(">"))
    text = re.sub(r"\[image:[^\]]*\]", " ", text)
    text = re.sub(r"\[([^\]]+)\]\((?:[^)]+)\)", r"\1", text)
    text = re.sub(r"\((?:https?|mailto):[^)]*\)|(?:https?|mailto):\S+", " ", text)
    text = re.sub(r"(?m)^\s*(?:[-*+•]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_~`]+", "", text)
    text = " ".join(text.split())
    if signature:
        at = text.rfind(signature)
        if at >= 0:
            text = (text[:at] + text[at + len(signature):]).strip()
    return text


def _parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.astimezone()


def match_pending(pending, items_by_source, now, signature=""):
    """Pair pending drafts with sent messages. `pending` is [(draft_id, scored, draft_text)];
    `items_by_source` maps a source key to its sent items, or to None when that source could not
    be read. Returns {"matched", "superseded", "unsent", "waiting", "unchecked"}, each a list.

    Each draft's candidates are the messages from its own source sent between just before it
    was scored and SWEEP_WINDOW_DAYS after, whose conversation doesn't contradict it, within the
    distance limit for how well the conversation is confirmed. Pairs are taken closest first and
    each sent message settles one draft, plus any other row scored on the same body. A draft
    whose only candidates went to closer drafts is an earlier version of one of them
    (superseded). A draft with no candidate is unsent once its window has closed, and waiting
    until then."""
    window = datetime.timedelta(days=SWEEP_WINDOW_DAYS)
    pairs, checked = [], []
    out = {"matched": [], "superseded": [], "unsent": [], "waiting": [], "unchecked": []}
    for draft_id, scored, draft_text in pending:
        key = _source_key(scored)
        items = items_by_source.get(key) if key else None
        if items is None:
            out["unchecked"].append(draft_id)
            continue
        checked.append((draft_id, scored))
        staged = _parse_ts(scored.get("ts"))
        keys = _draft_keys(scored)
        draft_cmp = comparable_text(draft_text, signature)
        for i, item in enumerate(items):
            if not (staged - SWEEP_EARLY_SLACK <= item["sent"] <= staged + window):
                continue
            same = _conversation(keys, item)
            if same is False:
                continue
            limit = SWEEP_MAX_DISTANCE_CONFIRMED if same else SWEEP_MAX_DISTANCE_UNCONFIRMED
            sent_cmp = comparable_text(item["text"], signature)
            distance = _edit_distance(draft_cmp, sent_cmp)
            if distance <= limit:
                pairs.append((distance, draft_id, (key, i), item, sent_cmp, same))

    taken_drafts, taken_items = set(), set()
    for distance, draft_id, item_key, item, sent_cmp, same in sorted(pairs, key=lambda p: p[0]):
        if draft_id in taken_drafts or item_key in taken_items:
            continue
        taken_drafts.add(draft_id)
        taken_items.add(item_key)
        out["matched"].append({"draft_id": draft_id, "edit_distance": distance, "sent_ref": item["ref"],
                               "sent_ts": item["sent"].isoformat(), "sent_text": sent_cmp,
                               "confirmed": bool(same)})
    # The same body scored twice (two stages of one draft) is one draft: a send settles both rows.
    by_sha = {}
    for draft_id, scored in checked:
        by_sha.setdefault(scored.get("sha256"), []).append(draft_id)
    for m in list(out["matched"]):
        sha = next(s.get("sha256") for d, s in checked if d == m["draft_id"])
        for twin in by_sha.get(sha, []) if sha else []:
            if twin not in taken_drafts:
                taken_drafts.add(twin)
                out["matched"].append({**m, "draft_id": twin})
    # A draft whose send was claimed by a closer draft is an earlier version of that one.
    had_pair = {p[1] for p in pairs}
    for draft_id, scored in checked:
        if draft_id in taken_drafts:
            continue
        if draft_id in had_pair:
            out["superseded"].append(draft_id)
            continue
        staged = _parse_ts(scored.get("ts"))
        out["unsent" if now - staged > window else "waiting"].append(draft_id)
    return out


def sweep_outcomes(*, fetch=fetch_sent, now=None, dry_run=False):
    """Run the sweep over the whole log. Reads every source the pending drafts need once, from
    the day before the oldest pending draft, then writes an outcome for each match and each
    closed-window miss unless `dry_run`. Returns the match_pending result plus "errors" and
    "match_rate" (matched over matched plus unsent)."""
    now = now or datetime.datetime.now().astimezone()
    pending = []
    for draft_id, d in fold_drafts().items():
        scored = d["scored"]
        if not scored or d["outcome"] is not None or not _parse_ts(scored.get("ts")):
            continue
        try:
            text = _read_draft_text(draft_id)
        except OSError:
            continue
        pending.append((draft_id, scored, text))

    items_by_source, errors = {}, {}
    keys = {k for k in (_source_key(s) for _, s, _ in pending) if k}
    if pending:
        since = (min(_parse_ts(s["ts"]) for _, s, _ in pending) - datetime.timedelta(days=1)).date().isoformat()
        for key in sorted(keys):
            try:
                items_by_source[key] = _sent_items(key, fetch(key, since))
            except Exception as e:
                items_by_source[key] = None
                errors[key] = str(e)

    result = match_pending(pending, items_by_source, now, _signature_text())
    if not dry_run:
        for m in result["matched"]:
            record_outcome(m["draft_id"], m["sent_text"], discarded=False, edit_nature=[], reason=None,
                           sweep={"edit_distance": m["edit_distance"], "sent_ref": m["sent_ref"]})
        for draft_id in result["superseded"]:
            _append_event({**_superseded_event(draft_id, now.isoformat()), "source": "sweep"})
        for draft_id in result["unsent"]:
            record_outcome(draft_id, None, discarded=True, edit_nature=[], reason=None, sweep={})
    settled = len(result["matched"]) + len(result["unsent"])
    result["match_rate"] = round(len(result["matched"]) / settled, 4) if settled else None
    result["errors"] = errors
    return result


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
                                 edit_nature=edit_nature, reason=reason, moot=moot,
                                 recipient=_flag_value(argv, "--recipient"))
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


def cmd_sweep(argv):
    dry_run = "--dry-run" in argv
    result = sweep_outcomes(dry_run=dry_run)
    if "--json" in argv:
        print(json.dumps(result, indent=2, default=str))
        return 0
    rate = result["match_rate"]
    print(f"outcome sweep{' (dry run - nothing written)' if dry_run else ''}: "
          f"{len(result['matched'])} sent, {len(result['superseded'])} superseded by a later version, "
          f"{len(result['unsent'])} not sent, "
          f"{len(result['waiting'])} inside the {SWEEP_WINDOW_DAYS}-day window, "
          f"{len(result['unchecked'])} unchecked")
    print(f"match rate: {'n/a' if rate is None else f'{rate:.0%}'} of settled drafts matched a send")
    for source, error in result["errors"].items():
        print(f"could not read {source}: {error}")
    for m in result["matched"]:
        print(f"  {m['draft_id']} -> {m['sent_ref']} (edit_distance {m['edit_distance']}"
              f"{'' if m['confirmed'] else ', unconfirmed conversation'})")
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
        print("usage: send_confidence.py outcome|sweep|weights-suggest|show|auto-score ...", file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "outcome":
        return cmd_outcome(rest)
    if verb == "auto-score":
        return cmd_auto_score(rest[0]) if rest else 0
    if verb == "sweep":
        return cmd_sweep(rest)
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
