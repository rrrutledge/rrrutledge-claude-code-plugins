"""Tests for send-confidence scoring (send_confidence.py).

Data (log, drafts, briefs, baseline) is pointed at a per-test temp dir through
SEND_CONFIDENCE_DIR so nothing touches ~/OneDrive/Claude/send-confidence. The judge is
stubbed off via SEND_CONFIDENCE_JUDGE=off for every test except the ones that need a
controlled rating, which monkeypatch run_judge directly.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(HERE)
MODULE_PATH = os.path.join(PACKAGE_DIR, "send_confidence.py")

sys.path.insert(0, PACKAGE_DIR)
import send_confidence as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEND_CONFIDENCE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEND_CONFIDENCE_JUDGE", "off")
    monkeypatch.delenv("ANTHROPIC_HOOK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def run_cli(args):
    return subprocess.run([sys.executable, MODULE_PATH, *args],
                           env=dict(os.environ), capture_output=True, text=True)


def _write_ctx(tmp_path, **overrides):
    ctx = {
        "channel": "gmail", "account": "isc", "recipient": "person@example.org",
        "thread_ref": "thread-1", "iid": None, "session_kind": "live",
        "ask": "Reply confirming the meeting.", "inputs": "Meeting is Tuesday at 2pm.",
        "turns_before_draft": 0, "sent_count": 5,
    }
    ctx.update(overrides)
    path = tmp_path / f"ctx-{overrides.get('thread_ref', 'default')}-{len(ctx)}-{id(overrides)}.json"
    path.write_text(json.dumps(ctx), encoding="utf-8")
    return str(path)


def _score(tmp_path, body=None, **ctx_overrides):
    body = body or ("This is a test draft with enough words to clear the stylometric "
                     "word-count gate for scoring purposes in this test today.")
    ctx_path = _write_ctx(tmp_path, **ctx_overrides)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft_id = sc.score_and_log(body, sha, ctx_path)
    return draft_id, body


def _seed_scored(draft_id, body, factors, score=None, channel="gmail", turns_before_draft=0):
    os.makedirs(sc.drafts_dir(), exist_ok=True)
    with open(os.path.join(sc.drafts_dir(), f"{draft_id}.txt"), "w", encoding="utf-8") as fh:
        fh.write(body)
    sc._append_event({
        "event": "scored", "v": 1, "draft_id": draft_id,
        "ts": "2026-09-28T10:00:00-05:00", "machine": "TEST", "sha256": "x" * 64,
        "channel": channel, "account": None, "recipient": "a@b.com", "thread_ref": "t1",
        "iid": None, "session_kind": "live", "turns_before_draft": turns_before_draft,
        "score": score, "weights_version": 1, "calibration_sha": "abc123456789",
        "factors": factors, "features": {}, "judge": None, "errors": [],
    })
    return draft_id


def _seed_outcome(draft_id, disposition):
    sc._append_event({
        "event": "outcome", "v": 1, "draft_id": draft_id,
        "ts": "2026-09-28T11:00:00-05:00", "disposition": disposition,
        "edit_distance": 0.0 if disposition == "sent-as-is" else 0.3,
        "edit_nature": [] if disposition == "sent-as-is" else ["tone-voice"],
        "reason": None, "miscalibrated": False, "direction": None,
        "implicated": [], "under_factors": [],
    })


# --- stylometric_features ---

def test_em_dash_hard_zero():
    assert sc.stylometric_features("No long dash anywhere in this plain sentence at all.")["em_dash"] == 0


def test_em_dash_counts_present():
    text = "This has — one em dash — and another — right here in the text."
    assert sc.stylometric_features(text)["em_dash"] == 3


def test_contraction_rate_positive():
    text = "I don't think that's right, but I can't be sure of it either way today."
    assert sc.stylometric_features(text)["contraction_rate"] > 0


def test_first_person_rate_positive():
    text = "I think my plan works well. I'm confident about it. Me too, honestly."
    assert sc.stylometric_features(text)["first_person_rate"] > 0


def test_sent_len_sd_needs_three_sentences():
    two = "One short sentence here now. Another one right here too."
    assert sc.stylometric_features(two)["sent_len_sd"] is None
    three = "One. Two words here. Three whole words are in this one right now."
    assert sc.stylometric_features(three)["sent_len_sd"] is not None


def test_mattr_plain_ttr_under_window():
    text = "the cat sat on the mat"
    feats = sc.stylometric_features(text)
    tokens = text.split()
    assert feats["mattr"] == round(len(set(tokens)) / len(tokens), 2)


def test_zero_words_all_null_except_em_dash():
    feats = sc.stylometric_features("—")
    assert feats["words"] == 0
    assert feats["em_dash"] == 1
    assert feats["contraction_rate"] is None
    assert feats["first_person_rate"] is None
    assert feats["sent_len_sd"] is None
    assert feats["mattr"] is None


def test_under_25_words_makes_stylometric_factor_null_in_score():
    ctx = {"channel": "gmail", "sent_count": None, "turns_before_draft": 0}
    result = sc.score_draft("Short draft under the word threshold.", ctx,
                             weights=sc.DEFAULT_WEIGHTS, baseline=None,
                             calibration_notes="", api_key=None)
    assert result["factors"]["stylometric"] is None


# --- familiarity curve ---

@pytest.mark.parametrize("n,expected", [(0, 0), (1, 23), (5, 59), (20, 100), (50, 100)])
def test_familiarity_curve(n, expected):
    ctx = {"channel": "gmail", "sent_count": n, "turns_before_draft": 0}
    result = sc.score_draft("word " * 30, ctx, weights=sc.DEFAULT_WEIGHTS, baseline=None,
                             calibration_notes="", api_key=None)
    assert result["factors"]["familiarity"] == expected


# --- composite + ambiguity turn penalty ---

def test_composite_is_weighted_mean_over_available_factors():
    ctx = {"channel": "gmail", "sent_count": 5, "turns_before_draft": 0}
    body = "word " * 30
    result = sc.score_draft(body, ctx, weights=sc.DEFAULT_WEIGHTS, baseline=None,
                             calibration_notes="", api_key=None)
    factors = result["factors"]
    assert factors["phrasing_complexity"] is None
    assert factors["task_ambiguity"] is None
    assert factors["input_completeness"] is None
    assert factors["familiarity"] == 59
    assert factors["stylometric"] is not None
    w = sc.DEFAULT_WEIGHTS["weights"]
    available = {f: s for f, s in factors.items() if s is not None}
    expected = round(sum(w[f] * s for f, s in available.items()) / sum(w[f] for f in available))
    assert result["score"] == expected


def test_ambiguity_turn_penalty_and_cap(monkeypatch):
    def fake_judge(ctx, body, notes, key):
        return {
            "phrasing_complexity": {"rating": 3, "rationale": "x"},
            "task_ambiguity": {"rating": 4, "rationale": "y"},
            "claims": [],
        }, None
    monkeypatch.setattr(sc, "run_judge", fake_judge)
    body = "word " * 30

    r0 = sc.score_draft(body, {"channel": "gmail", "sent_count": None, "turns_before_draft": 0},
                         weights=sc.DEFAULT_WEIGHTS, baseline=None, calibration_notes="", api_key=None)
    assert r0["factors"]["task_ambiguity"] == 25

    r3 = sc.score_draft(body, {"channel": "gmail", "sent_count": None, "turns_before_draft": 3},
                         weights=sc.DEFAULT_WEIGHTS, baseline=None, calibration_notes="", api_key=None)
    assert r3["features"]["ambiguity_turn_penalty"] == 15
    assert r3["factors"]["task_ambiguity"] == 10

    r10 = sc.score_draft(body, {"channel": "gmail", "sent_count": None, "turns_before_draft": 10},
                          weights=sc.DEFAULT_WEIGHTS, baseline=None, calibration_notes="", api_key=None)
    assert r10["features"]["ambiguity_turn_penalty"] == 20  # capped, not 5*10=50
    assert r10["factors"]["task_ambiguity"] == 5


# --- score_and_log ---

def test_score_and_log_writes_snapshot_and_scored_row(tmp_path):
    draft_id, body = _score(tmp_path)
    snapshot = os.path.join(sc.drafts_dir(), f"{draft_id}.txt")
    assert os.path.isfile(snapshot)
    with open(snapshot, encoding="utf-8") as fh:
        assert fh.read() == body

    scored = [e for e in sc.load_events() if e["event"] == "scored" and e["draft_id"] == draft_id]
    assert len(scored) == 1
    assert scored[0]["channel"] == "gmail"


def test_score_and_log_supersedes_pending_same_thread_recipient(tmp_path):
    id1, _ = _score(tmp_path, thread_ref="thread-x", recipient="a@b.com",
                     body="First draft of this reply, before it got revised further today.")
    id2, _ = _score(tmp_path, thread_ref="thread-x", recipient="a@b.com",
                     body="Revised draft of this same reply, written a bit differently now.")
    drafts = sc.fold_drafts()
    assert drafts[id1]["outcome"]["disposition"] == "superseded"
    assert drafts[id2]["outcome"] is None


# --- outcome / calibration dispatch ---

def test_outcome_sent_as_is(tmp_path):
    draft_id, body = _score(tmp_path)
    result = sc.record_outcome(draft_id, body, discarded=False, edit_nature=[], reason=None)
    assert result["disposition"] == "sent-as-is"
    assert result["edit_distance"] <= 0.01
    assert result["dispatch"] is None


def test_outcome_factual_correction_implicates_input_completeness(tmp_path):
    draft_id = _seed_scored("d-test-factual", "Original draft body text here today please now.",
                             {"input_completeness": 80, "familiarity": 50, "phrasing_complexity": 50,
                              "task_ambiguity": 50, "stylometric": 50}, score=56)
    result = sc.record_outcome(draft_id, "Corrected draft body text here right now please.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["disposition"] == "edited"
    assert result["implicated"] == ["input_completeness"]
    assert result["direction"] == "over"
    assert result["dispatch"] is not None
    assert os.path.isfile(os.path.join(sc.briefs_dir(), f"{draft_id}.md"))


def test_outcome_tone_voice_implicates_stylometric_and_familiarity(tmp_path):
    draft_id = _seed_scored("d-test-tone", "Original draft body with enough words to not matter here.",
                             {"stylometric": 80, "familiarity": 90, "phrasing_complexity": 50,
                              "task_ambiguity": 50, "input_completeness": 50}, score=70)
    result = sc.record_outcome(draft_id, "Rewritten in a rather different voice entirely.",
                                discarded=False, edit_nature=["tone-voice"], reason=None)
    assert set(result["implicated"]) == {"stylometric", "familiarity"}


def test_outcome_structural_rewrite_implicates_ambiguity_and_complexity(tmp_path):
    draft_id = _seed_scored("d-test-structural", "Original draft body text right here today.",
                             {"task_ambiguity": 75, "phrasing_complexity": 90, "familiarity": 50,
                              "input_completeness": 50, "stylometric": 50}, score=71)
    result = sc.record_outcome(draft_id, "Completely restructured reply body text today.",
                                discarded=False, edit_nature=["structural-rewrite"], reason=None)
    assert set(result["implicated"]) == {"task_ambiguity", "phrasing_complexity"}


def test_outcome_scope_recipient_implicates_ambiguity(tmp_path):
    draft_id = _seed_scored("d-test-scope", "Original draft body text right here today please.",
                             {"task_ambiguity": 80, "familiarity": 50, "phrasing_complexity": 50,
                              "input_completeness": 50, "stylometric": 50}, score=62)
    result = sc.record_outcome(draft_id, "Different recipient wording used in this one.",
                                discarded=False, edit_nature=["scope-recipient"], reason=None)
    assert result["implicated"] == ["task_ambiguity"]


def test_outcome_composite_high_fallback(tmp_path):
    draft_id = _seed_scored("d-test-composite", "Original draft body text for the fallback test today.",
                             {"task_ambiguity": 50, "familiarity": 50, "phrasing_complexity": 50,
                              "input_completeness": 50, "stylometric": 50}, score=80)
    result = sc.record_outcome(draft_id, "Edited body text with one small tweak added in.",
                                discarded=False, edit_nature=["factual-correction"], reason=None)
    assert result["implicated"] == ["composite"]
    assert result["direction"] == "over"


def test_outcome_discarded(tmp_path):
    draft_id = _seed_scored("d-test-discard", "Original draft to be discarded entirely today please.",
                             {"input_completeness": 30, "familiarity": 30, "phrasing_complexity": 30,
                              "task_ambiguity": 30, "stylometric": 30}, score=30)
    result = sc.record_outcome(draft_id, None, discarded=True, edit_nature=[], reason="stale info")
    assert result["disposition"] == "discarded"
    assert result["edit_distance"] is None
    assert result["reason"] == "stale info"
    assert result["direction"] is None
    assert result["dispatch"] is None


def test_outcome_under_confidence_recorded_without_brief(tmp_path):
    draft_id = _seed_scored("d-test-under", "Body text used for the under-confidence test today.",
                             {"familiarity": 20, "phrasing_complexity": 90, "task_ambiguity": 90,
                              "input_completeness": 90, "stylometric": 90}, score=76)
    result = sc.record_outcome(draft_id, "Body text used for the under-confidence test today.",
                                discarded=False, edit_nature=[], reason=None)
    assert result["disposition"] == "sent-as-is"
    assert result["under_factors"] == ["familiarity"]
    assert result["direction"] is None
    assert result["dispatch"] is None
    assert not os.path.isfile(os.path.join(sc.briefs_dir(), f"{draft_id}.md"))


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


def test_cli_outcome_body_file_lookup(tmp_path):
    draft_id, body = _score(tmp_path)
    staged = tmp_path / "staged.md"
    staged.write_text(body, encoding="utf-8")

    r = run_cli(["outcome", "--body-file", str(staged), "--discarded", "--reason", "no longer needed"])
    assert r.returncode == 0
    assert draft_id in r.stdout


# --- weights-suggest ---

def test_weights_suggest_below_min_n_proposes_nothing():
    for i in range(19):
        draft_id = _seed_scored(f"d-below-{i}", "Body text here for the below-min-n test today please.",
                                 {"familiarity": 50, "phrasing_complexity": 50, "task_ambiguity": 50,
                                  "input_completeness": 50, "stylometric": 50}, score=50)
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
        factors = {"familiarity": 50, "task_ambiguity": 50, "input_completeness": 50, "stylometric": 50,
                   "phrasing_complexity": 100 if untouched else 0}
        draft_id = _seed_scored(f"d-fit-{i}", "Body text here for the weights fitting test today.",
                                 factors, score=50)
        _seed_outcome(draft_id, "sent-as-is" if untouched else "edited")

    result = sc.suggest_weights()
    assert result["n"] == 20
    assert result["correlations"]["phrasing_complexity"] == 1.0
    for f in ("familiarity", "task_ambiguity", "input_completeness", "stylometric"):
        assert result["correlations"][f] == 0.0
    proposed = result["proposed"]["weights"]
    assert result["proposed"]["version"] == 4
    assert proposed["phrasing_complexity"] == pytest.approx(0.2941, abs=1e-3)
    for f in ("familiarity", "task_ambiguity", "input_completeness", "stylometric"):
        assert proposed[f] == pytest.approx(0.1765, abs=1e-3)


def test_weights_suggest_step_cap_and_clamp(monkeypatch):
    fixed = {"version": 1, "weights": {"familiarity": 0.38, "phrasing_complexity": 0.02,
             "task_ambiguity": 0.2, "input_completeness": 0.2, "stylometric": 0.2},
             "thresholds": sc.DEFAULT_WEIGHTS["thresholds"]}
    monkeypatch.setattr(sc, "_load_weights", lambda: fixed)

    for i in range(20):
        untouched = i % 2 == 0
        factors = {"phrasing_complexity": 50, "task_ambiguity": 50, "input_completeness": 50,
                   "stylometric": 50, "familiarity": 100 if untouched else 0}
        draft_id = _seed_scored(f"d-clamp-{i}", "Body text here for the weights clamp test today.",
                                 factors, score=50)
        _seed_outcome(draft_id, "sent-as-is" if untouched else "edited")

    result = sc.suggest_weights()
    proposed = result["proposed"]["weights"]
    # familiarity's step is capped at +0.05 (0.38 -> 0.43, clamped to 0.40) before renormalizing;
    # phrasing_complexity's post-step value (~0.0185) is floored at 0.05 by the clamp.
    assert proposed["familiarity"] == pytest.approx(0.4444, abs=1e-3)
    assert proposed["phrasing_complexity"] == pytest.approx(0.0556, abs=1e-3)
    for f in ("task_ambiguity", "input_completeness", "stylometric"):
        assert proposed[f] == pytest.approx(0.1667, abs=1e-3)
