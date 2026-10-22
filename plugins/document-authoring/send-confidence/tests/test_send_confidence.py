"""Tests for send-confidence scoring (send_confidence.py).

Data (log, drafts, briefs) is pointed at a per-test temp dir through SEND_CONFIDENCE_DIR so
nothing touches ~/OneDrive/Claude/send-confidence. The judge and the edit labeller are
stubbed off via SEND_CONFIDENCE_JUDGE=off for every test except the ones that need a
controlled answer, which monkeypatch run_judge or label_edits directly.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(HERE)
MODULE_PATH = os.path.join(PACKAGE_DIR, "send_confidence.py")

sys.path.insert(0, PACKAGE_DIR)
import send_confidence as sc  # noqa: E402

NEUTRAL = {"familiarity": 50, "stakes": 50, "content_latitude": 50, "fact_support": 50, "rule_coverage": 50}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEND_CONFIDENCE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEND_CONFIDENCE_JUDGE", "off")
    monkeypatch.delenv("ANTHROPIC_HOOK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def run_cli(args):
    return subprocess.run([sys.executable, MODULE_PATH, *args],
                           env=dict(os.environ), capture_output=True, text=True)


def _ctx(**overrides):
    ctx = {
        "channel": "gmail", "account": "isc", "recipient": "person@example.org",
        "thread_ref": "thread-1", "iid": None, "session_kind": "live",
        "ask": "Reply confirming the meeting.", "inputs": "Meeting is Tuesday at 2pm.",
        "turns_before_draft": 0, "sent_count": 5,
    }
    ctx.update(overrides)
    return ctx


def _score(tmp_path, body=None, **ctx_overrides):
    body = body or "This is a test draft confirming the meeting on Tuesday at 2pm."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft_id = sc.score_and_log(body, sha, _ctx(**ctx_overrides))
    return draft_id, body


def _seed_scored(draft_id, body, factors, score=None, channel="gmail", turns_before_draft=0,
                 uncertain_spots=None):
    os.makedirs(sc.drafts_dir(), exist_ok=True)
    with open(os.path.join(sc.drafts_dir(), f"{draft_id}.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)
    sc._append_event({
        "event": "scored", "v": 2, "draft_id": draft_id,
        "ts": "2026-09-28T10:00:00-05:00", "machine": "TEST", "sha256": "x" * 64,
        "channel": channel, "account": None, "recipient": "a@b.com", "thread_ref": "t1",
        "iid": None, "session_kind": "live", "turns_before_draft": turns_before_draft,
        "score": score, "weights_version": 2, "aspects_sha": "abc123456789",
        "factors": factors, "features": {}, "judge": None,
        "uncertain_spots": uncertain_spots, "errors": [],
    })
    return draft_id


def _seed_outcome(draft_id, disposition):
    sc._append_event({
        "event": "outcome", "v": 2, "draft_id": draft_id,
        "ts": "2026-09-28T11:00:00-05:00", "disposition": disposition,
        "edit_distance": 0.0 if disposition == "sent-as-is" else 0.3,
        "edit_nature": [] if disposition == "sent-as-is" else ["tone-voice"],
        "reason": None, "miscalibrated": False, "direction": None,
        "implicated": [], "under_factors": [], "learn_reasons": [],
    })


def _brief(draft_id):
    return os.path.join(sc.briefs_dir(), f"{draft_id}.md")


# --- familiarity curve ---

@pytest.mark.parametrize("n,expected", [(0, 0), (1, 23), (5, 59), (20, 100), (50, 100)])
def test_familiarity_curve(n, expected):
    ctx = {"channel": "gmail", "sent_count": n, "turns_before_draft": 0}
    result = sc.score_draft("word " * 30, ctx, weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert result["factors"]["familiarity"] == expected


# --- rule coverage ---

@pytest.mark.parametrize("rating,expected", [(1, 0), (3, 50), (5, 100)])
def test_rule_coverage_maps_reviewer_rating(rating, expected):
    ctx = {"channel": "slack", "sent_count": None, "rule_coverage": rating}
    result = sc.score_draft("Short one.", ctx, weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert result["factors"]["rule_coverage"] == expected
    assert result["features"]["rule_coverage_rating"] == rating


@pytest.mark.parametrize("rating", [None, 0, 6, "4"])
def test_rule_coverage_absent_or_invalid_is_null(rating):
    ctx = {"channel": "slack", "sent_count": None, "rule_coverage": rating}
    result = sc.score_draft("Short one.", ctx, weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert result["factors"]["rule_coverage"] is None


# --- composite + latitude turn penalty ---

def test_composite_is_weighted_mean_over_available_factors():
    ctx = {"channel": "gmail", "sent_count": 5, "turns_before_draft": 0, "rule_coverage": 5}
    result = sc.score_draft("word " * 30, ctx, weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    factors = result["factors"]
    assert factors["stakes"] is None
    assert factors["content_latitude"] is None
    assert factors["fact_support"] is None
    assert factors["familiarity"] == 59
    assert factors["rule_coverage"] == 100
    assert "stylometric" not in factors
    assert result["score"] == round((59 + 100) / 2)


def test_judge_ratings_and_latitude_turn_penalty(monkeypatch):
    def fake_judge(ctx, body, aspects, key):
        return {
            "stakes": {"rating": 3, "rationale": "x"},
            "content_latitude": {"rating": 4, "rationale": "y"},
            "claims": [{"text": "a", "supported": True}, {"text": "b", "supported": False}],
        }, None
    monkeypatch.setattr(sc, "run_judge", fake_judge)
    body = "word " * 30

    r0 = sc.score_draft(body, {"channel": "gmail", "ask": "Reply.", "sent_count": None,
                               "turns_before_draft": 0},
                         weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert r0["factors"]["stakes"] == 50
    assert r0["factors"]["content_latitude"] == 25
    assert r0["factors"]["fact_support"] == 50

    r3 = sc.score_draft(body, {"channel": "gmail", "ask": "Reply.", "sent_count": None,
                               "turns_before_draft": 3},
                         weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert r3["features"]["latitude_turn_penalty"] == 15
    assert r3["factors"]["content_latitude"] == 10

    blind = sc.score_draft(body, {"channel": "gmail", "ask": None, "inputs": None},
                            weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert blind["factors"]["stakes"] == 50
    assert blind["factors"]["content_latitude"] is None
    assert blind["factors"]["fact_support"] is None
    assert blind["score"] == 50

    r10 = sc.score_draft(body, {"channel": "gmail", "ask": "Reply.", "sent_count": None,
                                "turns_before_draft": 10},
                          weights=sc.DEFAULT_WEIGHTS, aspects="", api_key=None)
    assert r10["features"]["latitude_turn_penalty"] == 20  # capped, not 5*10=50
    assert r10["factors"]["content_latitude"] == 5


def test_judge_prompt_carries_the_aspects_file(monkeypatch):
    seen = {}

    def fake_call(system, user, api_key, max_tokens):
        seen["system"] = system
        return None, "stubbed"
    monkeypatch.setattr(sc, "_call_model", fake_call)
    sc.run_judge({"channel": "gmail"}, "body", "## Stakes\nHow much rides on it.", "key")
    assert "## Stakes\nHow much rides on it." in seen["system"]


def test_aspects_file_exists_and_names_every_aspect():
    text, _ = sc._load_aspects()
    for heading in ("## Rule coverage", "## Content latitude", "## Fact support", "## Stakes", "## Familiarity"):
        assert heading in text


# --- score_and_log ---

def test_score_and_log_writes_snapshot_and_scored_row(tmp_path):
    draft_id, body = _score(tmp_path, rule_coverage=4, uncertain_spots="the closing line")
    snapshot = os.path.join(sc.drafts_dir(), f"{draft_id}.txt")
    with open(snapshot, encoding="utf-8") as fh:
        assert fh.read() == body

    scored = [e for e in sc.load_events() if e["event"] == "scored" and e["draft_id"] == draft_id]
    assert len(scored) == 1
    assert scored[0]["channel"] == "gmail"
    assert scored[0]["factors"]["rule_coverage"] == 75
    assert scored[0]["uncertain_spots"] == "the closing line"


def test_score_and_log_supersedes_pending_same_thread_recipient(tmp_path):
    id1, _ = _score(tmp_path, thread_ref="thread-x", recipient="a@b.com",
                     body="First draft of this reply, before it got revised further today.")
    id2, _ = _score(tmp_path, thread_ref="thread-x", recipient="a@b.com",
                     body="Revised draft of this same reply, written a bit differently now.")
    drafts = sc.fold_drafts()
    assert drafts[id1]["outcome"]["disposition"] == "superseded"
    assert drafts[id2]["outcome"] is None


def test_reply_with_unknown_recipient_supersedes_earlier_draft_in_same_thread(tmp_path):
    id1, _ = _score(tmp_path, thread_ref="thread-y", recipient="a@b.com",
                     body="First draft of the reply, staged with its addressee known.")
    id2, _ = _score(tmp_path, thread_ref="thread-y", recipient=None,
                     body="Revision of that reply, staged with only the thread on the command.")
    drafts = sc.fold_drafts()
    assert drafts[id1]["outcome"]["disposition"] == "superseded"
    assert drafts[id2]["outcome"] is None


def test_new_message_to_unknown_recipient_supersedes_nothing(tmp_path):
    id1, _ = _score(tmp_path, thread_ref=None, recipient=None,
                     body="A brand-new message with no recipient on the command line.")
    _score(tmp_path, thread_ref=None, recipient=None,
           body="Another brand-new message with no recipient on the command line.")
    assert sc.fold_drafts()[id1]["outcome"] is None


def test_direct_send_is_never_superseded(tmp_path):
    id1, _ = _score(tmp_path, thread_ref=None, recipient="C123", stage_kind="send",
                     body="Posted straight to the channel, first message of the day.")
    _score(tmp_path, thread_ref=None, recipient="C123",
           body="A later draft to the same channel, a different message entirely.")
    assert sc.fold_drafts()[id1]["outcome"] is None


def test_direct_send_supersedes_nothing(tmp_path):
    id1, _ = _score(tmp_path, thread_ref=None, recipient="C123",
                     body="A pending draft to the channel that hasn't gone out yet.")
    _score(tmp_path, thread_ref=None, recipient="C123", stage_kind="send",
           body="An unrelated message posted straight to the same channel.")
    assert sc.fold_drafts()[id1]["outcome"] is None


def test_fold_renames_legacy_factors_and_drops_stylometric():
    _seed_scored("d-legacy", "Old body.", {"familiarity": 10, "phrasing_complexity": 20,
                                            "task_ambiguity": 30, "input_completeness": 40,
                                            "stylometric": 50}, score=30)
    factors = sc.fold_drafts()["d-legacy"]["scored"]["factors"]
    assert factors == {"familiarity": 10, "stakes": 20, "content_latitude": 30, "fact_support": 40}


# --- outcome: labelling ---

def test_edited_outcome_labels_with_the_model(monkeypatch):
    monkeypatch.setattr(sc, "label_edits", lambda d, s, k: (
        ["tone-voice"], [{"label": "tone-voice", "summary": "softened the close"}], None))
    draft_id = _seed_scored("d-label", "Original draft body text here today please now.", NEUTRAL, score=50)
    result = sc.record_outcome(draft_id, "A rather different body text here today.",
                                discarded=False, edit_nature=[], reason=None)
    assert result["edit_nature"] == ["tone-voice"]
    assert result["changes"][0]["summary"] == "softened the close"
    assert result["label_error"] is None


def test_edit_nature_overrides_the_model(monkeypatch):
    def boom(*a):
        raise AssertionError("labeller should not run")
    monkeypatch.setattr(sc, "label_edits", boom)
    draft_id = _seed_scored("d-override", "Original draft body text here today please now.", NEUTRAL, score=50)
    result = sc.record_outcome(draft_id, "A rather different body text here today.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["edit_nature"] == ["factual-correction"]


def test_labelling_failure_is_reported_not_raised():
    draft_id = _seed_scored("d-label-fail", "Original draft body text here today please now.", NEUTRAL, score=50)
    result = sc.record_outcome(draft_id, "A rather different body text here today.",
                                discarded=False, edit_nature=[], reason=None)
    assert result["edit_nature"] == []
    assert "disabled" in result["label_error"]


def test_label_edits_keeps_only_known_labels(monkeypatch):
    monkeypatch.setattr(sc, "_call_model", lambda *a: ({"changes": [
        {"label": "tone-voice", "summary": "a"}, {"label": "made-up", "summary": "b"},
        {"label": "tone-voice", "summary": "c"}, {"label": "structural-rewrite", "summary": "d"},
    ]}, None))
    labels, changes, error = sc.label_edits("x", "y", "key")
    assert labels == ["tone-voice", "structural-rewrite"]
    assert len(changes) == 3
    assert error is None


# --- outcome: when a learning session spawns ---

def test_sent_as_is_with_middling_score_spawns_nothing(tmp_path):
    draft_id, body = _score(tmp_path, rule_coverage=3)
    result = sc.record_outcome(draft_id, body, discarded=False, edit_nature=[], reason=None)
    assert result["disposition"] == "sent-as-is"
    assert result["edit_distance"] <= 0.01
    assert result["learn_reasons"] == []
    assert result["dispatch"] is None


def test_html_staged_draft_sent_as_plain_text_is_sent_as_is(tmp_path):
    draft_id, body = _score(tmp_path, rule_coverage=3)
    path = os.path.join(sc.drafts_dir(), f"{draft_id}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<p>{body}</p>")
    result = sc.record_outcome(draft_id, body, discarded=False, edit_nature=[], reason=None)
    assert result["disposition"] == "sent-as-is"
    assert result["learn_reasons"] == []


def test_factual_correction_implicates_fact_support(tmp_path):
    factors = {**NEUTRAL, "fact_support": 80}
    draft_id = _seed_scored("d-factual", "Original draft body text here today please now.", factors, score=56)
    result = sc.record_outcome(draft_id, "Corrected draft body text here right now please.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["disposition"] == "edited"
    assert result["implicated"] == ["fact_support"]
    assert result["learn_reasons"] == ["over"]
    assert result["dispatch"] is not None
    assert os.path.isfile(_brief(draft_id))


def test_factual_correction_implicates_content_latitude(tmp_path):
    factors = {**NEUTRAL, "content_latitude": 85}
    draft_id = _seed_scored("d-latitude", "Original draft body text here today please now.", factors, score=57)
    result = sc.record_outcome(draft_id, "Corrected draft body text here right now please.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["implicated"] == ["content_latitude"]


def test_tone_voice_implicates_rule_coverage_and_familiarity(tmp_path):
    factors = {**NEUTRAL, "rule_coverage": 80, "familiarity": 90}
    draft_id = _seed_scored("d-tone", "Original draft body with enough words to not matter here.", factors, score=64)
    result = sc.record_outcome(draft_id, "Rewritten in a rather different voice entirely.",
                                discarded=False, edit_nature=["tone-voice"], reason=None)
    assert set(result["implicated"]) == {"rule_coverage", "familiarity"}
    assert result["learn_reasons"] == ["over", "voice"]


def test_structural_rewrite_implicates_rule_coverage(tmp_path):
    factors = {**NEUTRAL, "rule_coverage": 90}
    draft_id = _seed_scored("d-structural", "Original draft body text right here today.", factors, score=58)
    result = sc.record_outcome(draft_id, "Completely restructured reply body text today.",
                                discarded=False, edit_nature=["structural-rewrite"], reason=None)
    assert result["implicated"] == ["rule_coverage"]


def test_voice_edit_spawns_even_when_the_estimate_was_right(tmp_path):
    factors = {**NEUTRAL, "rule_coverage": 25}
    draft_id = _seed_scored("d-voice-right", "Original draft body text right here today.", factors, score=45)
    result = sc.record_outcome(draft_id, "Reworded draft body text right here today.",
                                discarded=False, edit_nature=["tone-voice"], reason=None)
    assert result["direction"] is None
    assert result["miscalibrated"] is False
    assert result["learn_reasons"] == ["voice"]
    assert result["dispatch"] is not None


def test_factual_correction_the_estimate_saw_coming_spawns_nothing(tmp_path):
    factors = {**NEUTRAL, "fact_support": 30}
    draft_id = _seed_scored("d-fact-right", "Original draft body text right here today.", factors, score=46)
    result = sc.record_outcome(draft_id, "Corrected draft body text right here today.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["learn_reasons"] == []
    assert result["dispatch"] is None


def test_composite_high_fallback(tmp_path):
    draft_id = _seed_scored("d-composite", "Original draft body text for the fallback test today.",
                             NEUTRAL, score=80)
    result = sc.record_outcome(draft_id, "Edited body text with one small tweak added in.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["implicated"] == ["composite"]
    assert result["direction"] == "over"


def test_under_confident_sent_as_is_spawns(tmp_path):
    factors = {"familiarity": 20, "stakes": 30, "content_latitude": 30, "fact_support": 90, "rule_coverage": 20}
    body = "Body text used for the under-confidence test today."
    draft_id = _seed_scored("d-under", body, factors, score=38)
    result = sc.record_outcome(draft_id, body, discarded=False, edit_nature=[], reason=None)
    assert result["disposition"] == "sent-as-is"
    assert result["direction"] == "under"
    assert result["miscalibrated"] is True
    assert result["learn_reasons"] == ["under"]
    assert set(result["under_factors"]) == {"familiarity", "stakes", "content_latitude", "rule_coverage"}
    assert result["dispatch"] is not None
    with open(_brief(draft_id), encoding="utf-8") as fh:
        assert "Under-confident" in fh.read()


def test_sent_as_is_above_composite_low_logs_under_factors_without_spawning(tmp_path):
    factors = {"familiarity": 20, "stakes": 90, "content_latitude": 90, "fact_support": 90, "rule_coverage": 90}
    body = "Body text used for the under-factor test today."
    draft_id = _seed_scored("d-under-factor", body, factors, score=76)
    result = sc.record_outcome(draft_id, body, discarded=False, edit_nature=[], reason=None)
    assert result["under_factors"] == ["familiarity"]
    assert result["direction"] is None
    assert result["dispatch"] is None
    assert not os.path.isfile(_brief(draft_id))


def test_discarded_low_score_spawns_nothing(tmp_path):
    factors = {f: 30 for f in NEUTRAL}
    draft_id = _seed_scored("d-discard", "Original draft to be discarded entirely today please.", factors, score=30)
    result = sc.record_outcome(draft_id, None, discarded=True, edit_nature=[], reason="stale info")
    assert result["disposition"] == "discarded"
    assert result["edit_distance"] is None
    assert result["reason"] == "stale info"
    assert result["direction"] is None
    assert result["dispatch"] is None


def test_moot_discard_skips_flagging(tmp_path):
    factors = {f: 95 for f in NEUTRAL}
    draft_id = _seed_scored("d-moot", "Original draft that never got sent through this channel.", factors, score=94)
    result = sc.record_outcome(draft_id, None, discarded=True, edit_nature=[],
                                reason="replied directly in the portal instead", moot=True)
    assert result["disposition"] == "discarded"
    assert result["direction"] is None
    assert result["implicated"] == []
    assert result["miscalibrated"] is False
    assert result["dispatch"] is None
    assert not os.path.isfile(_brief(draft_id))


def test_cli_outcome_moot_flag_suppresses_dispatch(tmp_path):
    factors = {f: 95 for f in NEUTRAL}
    draft_id = _seed_scored("d-moot-cli", "Original draft that never got sent through this channel.", factors, score=94)
    r = run_cli(["outcome", "--draft-id", draft_id, "--discarded", "--moot",
                 "--reason", "handled in the portal directly"])
    assert r.returncode == 0
    assert "discarded" in r.stdout
    assert "miscalibrated" not in r.stdout


# --- the brief ---

def test_redline_strikes_removed_words_and_bolds_added_ones():
    merged = sc.redline("Hi Pooi - great news.\n\nEnjoy!", "Hi Pooi - great news.\n\nWe are on it now.\n\nEnjoy!")
    assert "***We are on it now.***" in merged
    assert "~~" not in merged
    swapped = sc.redline("sounds good", "sounds great")
    assert swapped == "sounds ~~good~~***great***"


def test_brief_names_the_procedure_and_carries_both_texts(tmp_path):
    factors = {**NEUTRAL, "rule_coverage": 90}
    draft_id = _seed_scored("d-brief", "Hi there.\nThe draft line.", factors, score=60,
                             channel="slack", uncertain_spots="- the second line")
    result = sc.record_outcome(draft_id, "Hi there.\nThe sent line.",
                                discarded=False, edit_nature=["tone-voice"], reason=None)
    with open(_brief(draft_id), encoding="utf-8") as fh:
        brief = fh.read()
    assert brief.startswith("# Learn from send: slack to a@b.com")
    assert "This repo is public." in brief
    assert f"Follow `{sc.PROCEDURE_PATH}`" in brief
    assert "The draft line." in brief and "The sent line." in brief
    assert "Over-confident" in brief and "Voice edit" in brief
    assert "- the second line" in brief
    assert "authoring-rules/SKILL.md" in brief
    assert "uncertainty-aspects.md" in brief
    assert '--title "Learn from send: slack to a@b.com"' in result["dispatch"]


def test_procedure_file_exists():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(PACKAGE_DIR)))
    assert os.path.isfile(os.path.join(repo_root, sc.PROCEDURE_PATH))


# --- CLI ---

def test_body_file_lookup_finds_draft_id(tmp_path):
    draft_id, body = _score(tmp_path)
    body_file = tmp_path / "staged.md"
    body_file.write_text(body, encoding="utf-8")
    assert sc._find_draft_id_by_body_file(str(body_file)) == draft_id


def test_cli_outcome_and_show_roundtrip(tmp_path):
    draft_id, body = _score(tmp_path)
    sent_file = tmp_path / "sent.md"
    sent_file.write_text(body, encoding="utf-8")

    r = run_cli(["outcome", "--draft-id", draft_id, "--sent-file", str(sent_file)])
    assert r.returncode == 0
    assert "sent-as-is" in r.stdout

    r2 = run_cli(["show", draft_id])
    assert r2.returncode == 0
    parsed = json.loads(r2.stdout)
    assert parsed["scored"]["draft_id"] == draft_id
    assert parsed["outcome"]["disposition"] == "sent-as-is"


def test_cli_outcome_reports_labelling_failure(tmp_path):
    draft_id = _seed_scored("d-cli-label", "Original draft body text here today please now.", NEUTRAL, score=50)
    sent_file = tmp_path / "sent.md"
    sent_file.write_text("A rather different body text here today.", encoding="utf-8")
    r = run_cli(["outcome", "--draft-id", draft_id, "--sent-file", str(sent_file)])
    assert r.returncode == 0
    assert "labelling failed" in r.stdout
    assert "--edit-nature" in r.stdout


def test_cli_outcome_rejects_unknown_label(tmp_path):
    draft_id = _seed_scored("d-cli-bad", "Original draft body.", NEUTRAL, score=50)
    sent_file = tmp_path / "sent.md"
    sent_file.write_text("Changed body.", encoding="utf-8")
    r = run_cli(["outcome", "--draft-id", draft_id, "--sent-file", str(sent_file),
                 "--edit-nature", "scope-recipient"])
    assert r.returncode == 2
    assert "unknown" in r.stderr


def test_cli_outcome_body_file_lookup(tmp_path):
    draft_id, body = _score(tmp_path)
    staged = tmp_path / "staged.md"
    staged.write_text(body, encoding="utf-8")

    r = run_cli(["outcome", "--body-file", str(staged), "--discarded", "--reason", "no longer needed"])
    assert r.returncode == 0
    assert draft_id in r.stdout


# --- transcript context + familiarity ---

def _write_transcript(tmp_path, entries):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def _human(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _tool_result(text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


def test_transcript_context_reads_ask_inputs_and_turns(tmp_path):
    path = _write_transcript(tmp_path, [
        {"type": "system", "sessionKind": "bg"},
        _human("Reply to Dana confirming Tuesday."),
        _tool_result("From: Dana\nCan we meet Tuesday at 2pm?"),
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Drafting."}]}},
        _human([{"type": "text", "text": "Base directory for this skill: ..."}], isMeta=True),
        _human("<system-reminder>hook context</system-reminder>Make it shorter."),
        _human("<task-notification>agent done</task-notification>"),
    ])
    found = sc.transcript_context(path)
    assert found["ask"] == "Reply to Dana confirming Tuesday.\n\nMake it shorter."
    assert found["inputs"] == "From: Dana\nCan we meet Tuesday at 2pm?"
    assert found["turns_before_draft"] == 1
    assert found["session_kind"] == "background"


def test_transcript_context_caps_inputs_keeping_the_latest(tmp_path):
    path = _write_transcript(tmp_path, [_human("Go."), _tool_result("old " * 5000), _tool_result("newest result")])
    found = sc.transcript_context(path)
    assert len(found["inputs"]) == sc.INPUTS_CHAR_CAP
    assert found["inputs"].endswith("newest result")


def test_transcript_context_missing_file_is_all_none():
    assert sc.transcript_context("nope.jsonl") == {
        "ask": None, "inputs": None, "turns_before_draft": None, "session_kind": None}
    assert sc.transcript_context(None)["ask"] is None


def test_prior_session_count_counts_other_sessions_once_each():
    events = [
        {"event": "staged", "recipient": "a@b.com", "session_id": "s1"},
        {"event": "staged", "recipient": "a@b.com", "session_id": "s1"},
        {"event": "staged", "recipient": "a@b.com", "session_id": "s2"},
        {"event": "staged", "recipient": "a@b.com", "session_id": "now"},
        {"event": "staged", "recipient": "c@d.com", "session_id": "s3"},
    ]
    assert sc.prior_session_count("a@b.com", "now", events) == 2
    assert sc.prior_session_count("new@x.com", "now", events) == 0
    assert sc.prior_session_count(None, "now", events) is None
    assert sc.prior_session_count("self", "now", events) is None


# --- weights-suggest ---

def test_weights_suggest_below_min_n_proposes_nothing():
    for i in range(19):
        draft_id = _seed_scored(f"d-below-{i}", "Body text here for the below-min-n test today please.",
                                 NEUTRAL, score=50)
        _seed_outcome(draft_id, "sent-as-is" if i % 2 == 0 else "edited")
    result = sc.suggest_weights()
    assert result["n"] == 19
    assert result["proposed"] is None


def test_weights_suggest_fits_and_renormalizes(monkeypatch):
    fixed = {"version": 3, "weights": dict(sc.DEFAULT_WEIGHTS["weights"]),
             "thresholds": sc.DEFAULT_WEIGHTS["thresholds"]}
    monkeypatch.setattr(sc, "_load_weights", lambda: fixed)

    for i in range(20):
        untouched = i % 2 == 0
        factors = {**NEUTRAL, "stakes": 100 if untouched else 0}
        draft_id = _seed_scored(f"d-fit-{i}", "Body text here for the weights fitting test today.",
                                 factors, score=50)
        _seed_outcome(draft_id, "sent-as-is" if untouched else "edited")

    result = sc.suggest_weights()
    assert result["n"] == 20
    assert result["correlations"]["stakes"] == 1.0
    for f in ("familiarity", "content_latitude", "fact_support", "rule_coverage"):
        assert result["correlations"][f] == 0.0
    proposed = result["proposed"]["weights"]
    assert result["proposed"]["version"] == 4
    assert proposed["stakes"] == pytest.approx(0.2941, abs=1e-3)
    for f in ("familiarity", "content_latitude", "fact_support", "rule_coverage"):
        assert proposed[f] == pytest.approx(0.1765, abs=1e-3)


def test_weights_suggest_step_cap_and_clamp(monkeypatch):
    fixed = {"version": 1, "weights": {"familiarity": 0.38, "stakes": 0.02,
             "content_latitude": 0.2, "fact_support": 0.2, "rule_coverage": 0.2},
             "thresholds": sc.DEFAULT_WEIGHTS["thresholds"]}
    monkeypatch.setattr(sc, "_load_weights", lambda: fixed)

    for i in range(20):
        untouched = i % 2 == 0
        factors = {**NEUTRAL, "familiarity": 100 if untouched else 0}
        draft_id = _seed_scored(f"d-clamp-{i}", "Body text here for the weights clamp test today.",
                                 factors, score=50)
        _seed_outcome(draft_id, "sent-as-is" if untouched else "edited")

    result = sc.suggest_weights()
    proposed = result["proposed"]["weights"]
    # familiarity's step is capped at +0.05 (0.38 -> 0.43, clamped to 0.40) before renormalizing;
    # stakes' post-step value (~0.0185) is floored at 0.05 by the clamp.
    assert proposed["familiarity"] == pytest.approx(0.4444, abs=1e-3)
    assert proposed["stakes"] == pytest.approx(0.0556, abs=1e-3)
    for f in ("content_latitude", "fact_support", "rule_coverage"):
        assert proposed[f] == pytest.approx(0.1667, abs=1e-3)


def test_weights_suggest_leaves_an_unfitted_factor_at_its_current_share(monkeypatch):
    fixed = {"version": 2, "weights": dict(sc.DEFAULT_WEIGHTS["weights"]),
             "thresholds": sc.DEFAULT_WEIGHTS["thresholds"]}
    monkeypatch.setattr(sc, "_load_weights", lambda: fixed)

    for i in range(20):
        untouched = i % 2 == 0
        factors = {**NEUTRAL, "rule_coverage": None, "stakes": 100 if untouched else 0}
        draft_id = _seed_scored(f"d-unfit-{i}", "Body text here for the unfitted-factor test.",
                                 factors, score=50)
        _seed_outcome(draft_id, "sent-as-is" if untouched else "edited")

    result = sc.suggest_weights()
    assert "rule_coverage" not in result["correlations"]
    proposed = result["proposed"]["weights"]
    assert proposed["stakes"] > proposed["familiarity"]
    assert proposed["rule_coverage"] > proposed["familiarity"]


# --- record_stage + the detached scorer ---

STAGE_META = {"channel": "gmail", "account": None, "recipient": None, "thread_ref": "M1",
              "stage_kind": "draft", "script": "gmail.js", "verb": "--reply",
              "session_id": "s1", "session_kind": "live", "body_format": "text"}


def _capture_spawns(monkeypatch):
    spawns = []
    monkeypatch.setattr(sc, "_spawn_detached", lambda args: spawns.append(args))
    return spawns


def test_record_stage_logs_and_queues_one_scorer(monkeypatch):
    spawns = _capture_spawns(monkeypatch)
    body = "Thanks - Tuesday works."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    job = sc.record_stage(body, sha, STAGE_META)
    assert job and os.path.isfile(job)
    assert spawns == [[sys.executable, os.path.abspath(sc.__file__), "auto-score", job]]
    staged = [e for e in sc.load_events() if e["event"] == "staged"]
    assert len(staged) == 1 and staged[0]["sha256"] == sha and staged[0]["session_id"] == "s1"

    assert sc.record_stage(body, sha, STAGE_META) is None
    assert len(spawns) == 1
    assert len([e for e in sc.load_events() if e["event"] == "staged"]) == 2


def test_record_stage_takes_over_a_stale_job(monkeypatch):
    spawns = _capture_spawns(monkeypatch)
    body = "A draft whose earlier scorer crashed."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    job = sc.record_stage(body, sha, STAGE_META)
    old = os.path.getmtime(job) - sc.STALE_JOB_SECONDS - 10
    os.utime(job, (old, old))
    assert sc.record_stage(body, sha, STAGE_META) == job
    assert len(spawns) == 2


def test_record_stage_skips_an_already_scored_hash(tmp_path, monkeypatch):
    spawns = _capture_spawns(monkeypatch)
    draft_id, body = _score(tmp_path)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert sc.record_stage(body, sha, STAGE_META) is None
    assert spawns == []


def test_auto_score_job_scores_and_cleans_up(monkeypatch):
    _capture_spawns(monkeypatch)
    body = "Confirmed for Tuesday."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    job = sc.record_stage(body, sha, STAGE_META)
    assert run_cli(["auto-score", job]).returncode == 0
    assert not os.path.exists(job)
    scored = [e for e in sc.load_events() if e["event"] == "scored"]
    assert len(scored) == 1
    assert (scored[0]["source"], scored[0]["session_id"], scored[0]["thread_ref"]) == ("gate", "s1", "M1")


def test_auto_score_builds_context_from_transcript_receipt_and_log(tmp_path, monkeypatch):
    _capture_spawns(monkeypatch)
    seen = {}

    def fake_judge(ctx, body, aspects, key):
        seen.update(ctx)
        return {"stakes": {"rating": 2, "rationale": "x"},
                "content_latitude": {"rating": 2, "rationale": "y"},
                "claims": [{"text": "Tuesday", "supported": True}]}, None
    monkeypatch.setattr(sc, "run_judge", fake_judge)
    transcript = _write_transcript(tmp_path, [
        _human("Reply to Dana confirming Tuesday."),
        _tool_result("Dana: does Tuesday at 2pm work?"),
        _human("Shorter, please."),
    ])
    sc._append_event({"event": "staged", "recipient": "dana@x.com", "session_id": "earlier"})
    meta = {**STAGE_META, "recipient": "dana@x.com", "transcript_path": transcript,
            "rule_coverage": 4, "uncertain_spots": "- the sign-off"}
    body = "Tuesday at 2pm works - see you then."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    job = sc.record_stage(body, sha, meta)
    assert sc.cmd_auto_score(job) == 0
    assert seen["ask"] == "Reply to Dana confirming Tuesday.\n\nShorter, please."
    assert seen["inputs"] == "Dana: does Tuesday at 2pm work?"
    scored = [e for e in sc.load_events() if e["event"] == "scored"][0]
    assert scored["turns_before_draft"] == 1
    assert scored["features"]["sent_count"] == 1
    assert scored["factors"]["rule_coverage"] == 75
    assert scored["factors"]["fact_support"] == 100
    assert scored["factors"]["content_latitude"] == 75 - 5
    assert scored["uncertain_spots"] == "- the sign-off"


def test_auto_score_skips_when_a_row_landed_meanwhile(tmp_path, monkeypatch):
    _capture_spawns(monkeypatch)
    body = "This is a test draft confirming the meeting on Tuesday at 2pm."
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    job = sc.record_stage(body, sha, STAGE_META)
    _score(tmp_path, body=body)
    assert sc.cmd_auto_score(job) == 0
    assert len([e for e in sc.load_events() if e["event"] == "scored"]) == 1
    assert not os.path.exists(job)


def test_auto_score_with_a_missing_job_exits_0():
    assert sc.cmd_auto_score("does-not-exist.json") == 0


def test_scoring_body_extracts_outlook_json():
    assert sc.scoring_body(json.dumps({"body": "<p>Hi &amp; bye</p>"}), "outlook-json") == "Hi & bye"
    assert sc.scoring_body(json.dumps({"comment": "Plain reply"}), "outlook-json") == "Plain reply"
    assert sc.scoring_body("not json", "outlook-json") == "not json"
    assert sc.scoring_body("Plain body", "text") == "Plain body"


def test_spawn_detached_outlives_its_parent(tmp_path):
    marker = tmp_path / "marker.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import sys, time\ntime.sleep(1)\nopen(sys.argv[1], 'w').write('done')\n", encoding="utf-8")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import sys\nsys.path.insert(0, sys.argv[1])\nimport send_confidence as sc\n"
        "sc._spawn_detached([sys.executable, sys.argv[2], sys.argv[3]])\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(parent), PACKAGE_DIR, str(child), str(marker)],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
    assert not marker.exists()
    deadline = time.time() + 15
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.2)
    assert marker.read_text() == "done"
