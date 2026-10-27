"""Tests for the send-confidence model terms (send_confidence_model.py) and the weekly refit
(refit.py).

The log is pointed at a per-test temp dir through SEND_CONFIDENCE_DIR. Every refit test but the
last swaps the PyMC fit for a stub, so the gate, the screen, and the files it writes run without
PyMC; the last one runs a small real fit and skips when PyMC isn't installed.
"""
import datetime
import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import refit  # noqa: E402
import send_confidence as sc  # noqa: E402
import send_confidence_model as scm  # noqa: E402

NOW = datetime.datetime(2026, 12, 1, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEND_CONFIDENCE_DIR", str(tmp_path / "data"))


def _attrs(**overrides):
    attrs = {"claims_total": [1, 1, 2], "claims_unsupported": [0, 0, 0], "content_latitude": [2, 2, 3],
             "stakes": [2, 1, 2], "kind": {"label": "thanks-ack", "agreement": 3}, "turn_count": 0,
             "familiarity": 6, "familiarity_missing": False, "rule_coverage": 4,
             "rule_coverage_missing": False, "word_count": 40, "channel": "slack"}
    attrs.update(overrides)
    return attrs


def _model(binary_terms, beta, intercept=0.0, *, value_terms=(), values=None, latent=None,
           clear=(), support_rows=None):
    sets = len(beta)
    return {"version": 1, "binary_terms": binary_terms, "value_terms": list(value_terms),
            "values": values or {}, "latent": latent or {}, "intercept": [intercept] * sets,
            "beta": beta, "clear_binary": list(clear), "support_rows": support_rows or []}


def _seed(draft_id, ts, attrs, outcome):
    sc._append_event({"event": "scored", "v": 4, "draft_id": draft_id, "ts": ts.isoformat(),
                      "channel": "slack", "attributes": attrs})
    sc._append_event({"event": "outcome", "v": 2, "draft_id": draft_id,
                      "ts": (ts + datetime.timedelta(hours=1)).isoformat(), **outcome})


SENT = {"disposition": "sent-as-is", "edit_distance": 0.0}
EDITED = {"disposition": "edited", "edit_distance": 0.3}


# --- the target ---

@pytest.mark.parametrize("outcome,expected", [
    ({"disposition": "sent-as-is", "edit_distance": 0.0}, 1),
    ({"disposition": "edited", "edit_distance": 0.04}, 1),
    ({"disposition": "edited", "edit_distance": 0.2}, 0),
    ({"disposition": "edited", "edit_distance": None}, None),
    ({"disposition": "discarded", "reason": "the tone was off"}, 0),
    ({"disposition": "discarded", "reason": "Russell replied by phone instead"}, None),
    ({"disposition": "discarded", "reason": None, "moot": True}, None),
    ({"disposition": "discarded", "reason": None, "source": "sweep"}, None),
    ({"disposition": "superseded"}, None),
    (None, None),
])
def test_target(outcome, expected):
    assert scm.target(outcome) == expected


# --- terms ---

def test_flags_read_the_low_median_and_the_cuts():
    f = scm.flags(_attrs(claims_total=[3, 2, 2], content_latitude=[3, 2, 3]))
    assert f["few_claims"] == 1
    assert f["low_latitude"] == 0
    assert f["familiar"] == 1 and f["high_coverage"] == 1 and f["short"] == 1
    assert f["slack"] == 1 and f["familiarity_missing"] == 0


def test_missing_attribute_leaves_flag_off_and_marker_on():
    f = scm.flags(_attrs(familiarity=None, familiarity_missing=True, content_latitude=None))
    assert f["familiar"] == 0 and f["familiarity_missing"] == 1
    assert f["low_latitude"] == 0


def test_binary_row_evaluates_conjunctions_negations_and_kind():
    terms = [["slack", "short"], ["!slack"], ["kind:thanks-ack"], ["kind:decline"], ["few_claims", "!familiar"]]
    assert scm.binary_row(_attrs(), terms) == [1, 0, 1, 0, 0]


def test_default_terms_are_flags_pairs_and_kinds():
    terms = scm.default_binary_terms()
    assert len(terms) == 11 + 55 + 6


# --- scoring ---

def test_score_is_the_mean_and_floor_across_sets():
    model = _model([["slack"]], [[0.0], [2.0], [-2.0], [0.0]])
    result = scm.score(_attrs(), model)
    assert result["probability"] == 0.5
    assert result["floor"] < 0.2
    assert result["auto_send"] is False


def test_support_counts_drafts_sharing_the_clear_terms():
    model = _model([["slack"], ["short"]], [[5.0, 0.0]] * 3, clear=[0],
                   support_rows=[[0]] * 21 + [[1]] * 30)
    result = scm.score(_attrs(), model)
    assert result["support"] == 21
    assert result["auto_send"] is True
    assert scm.score(_attrs(channel="email"), model)["support"] == 51


def test_exact_value_term_is_standardized_and_missing_sits_at_center():
    values = {"word_count": {"transform": "log1p", "center": 0.0, "scale": 1.0}}
    model = _model([], [[1.0]], value_terms=["word_count"], values=values)
    import math
    assert scm.set_probabilities(_attrs(word_count=0), model) == [0.5]
    assert scm.set_probabilities(_attrs(word_count=None), model) == [0.5]
    assert scm.set_probabilities(_attrs(word_count=2), model)[0] == pytest.approx(1 / (1 + math.exp(-math.log(3))))


def test_latent_value_shrinks_toward_the_population_with_noise():
    values = {"stakes": {"transform": "raw", "center": 3.0, "scale": 1.0}}
    latent = {"stakes": {"noise": 1.0, "mu": [3.0] * 400, "sd": [1.0] * 400}}
    model = _model([], [[1.0]] * 400, value_terms=["stakes"], values=values, latent=latent)
    one = scm.set_probabilities(_attrs(stakes=[5]), model, seed=1)
    three = scm.set_probabilities(_attrs(stakes=[5, 5, 5]), model, seed=1)
    # Three readings pull the latent value further from the population mean than one does.
    assert sum(three) / 400 > sum(one) / 400 > 0.5


# --- the refit ---

def test_legacy_attributes_take_single_readings_and_word_count_from_the_snapshot():
    scored = {"v": 2, "channel": "gmail", "turns_before_draft": 1,
              "features": {"stakes_rating": 2, "latitude_rating": 3, "claims_total": 4,
                           "claims_unsupported": 1, "sent_count": None, "rule_coverage_rating": 4}}
    attrs = refit.legacy_attributes(scored, "<p>three short words</p>")
    assert attrs["stakes"] == [2] and attrs["content_latitude"] == [3] and attrs["claims_total"] == [4]
    assert attrs["familiarity_missing"] is True and attrs["rule_coverage"] == 4
    assert attrs["word_count"] == 3 and attrs["channel"] == "email" and attrs["kind"] is None


def test_gather_skips_v1_routing_discards_and_superseded():
    ts = NOW - datetime.timedelta(days=40)
    _seed("d-ok", ts, _attrs(), SENT)
    _seed("d-route", ts, _attrs(), {"disposition": "discarded", "reason": "answered directly in person"})
    _seed("d-super", ts, _attrs(), {"disposition": "superseded"})
    sc._append_event({"event": "scored", "v": 1, "draft_id": "d-v1", "ts": ts.isoformat(), "features": {}})
    sc._append_event({"event": "outcome", "draft_id": "d-v1", "ts": ts.isoformat(), **SENT})
    assert [r["draft_id"] for r in refit.gather()] == ["d-ok"]


def test_pooled_noise_needs_enough_drafts():
    events = [{"event": "scored", "attributes": _attrs(stakes=[2, 3, 4])} for _ in range(refit.NOISE_MIN_DRAFTS)]
    noise = refit.pooled_noise(events)
    assert noise["stakes"] == 1.0
    assert noise["claims_unsupported"] == 0.05
    assert refit.pooled_noise(events[:-1])["stakes"] is None


def test_split_holds_out_the_last_four_weeks_capped_at_half():
    rows = [{"ts": NOW - datetime.timedelta(days=d)} for d in (100, 90, 80, 70, 60, 20, 10, 5)]
    train, hold = refit.split(rows, NOW)
    assert len(train) == 5 and len(hold) == 3
    young = [{"ts": NOW - datetime.timedelta(days=d)} for d in (30, 20, 10, 5)]
    train, hold = refit.split(young, NOW)
    assert len(train) == 2 and len(hold) == 2


def test_tree_paths_find_a_three_flag_cluster():
    rows = []
    for i in range(84):
        hit = i < 20
        attrs = _attrs(channel="slack" if hit or i % 2 else "email", word_count=40 if hit or (i // 2) % 2 else 200,
                       claims_total=[1] if hit or (i // 4) % 2 else [6])
        rows.append({"attrs": attrs, "y": int(hit)})
    paths = refit.tree_paths(rows)
    assert paths and set(paths[0][1]) == {"slack", "short", "few_claims"}


class StubFit:
    """Stands in for the PyMC fit: every fit predicts `p` for every draft, and a cluster term gets
    `cluster_effect`."""

    def __init__(self, p, cluster_effect=None):
        self.p, self.cluster_effect, self.calls = p, cluster_effect, []

    def __call__(self, rows, terms, noise, sampler=None):
        import math
        self.calls.append((len(rows), len(terms)))
        effects = [{"term": scm.term_name(t), "mean": 0.0, "lo": -1.0, "hi": 1.0, "clear": False} for t in terms]
        for e, t in zip(effects, terms):
            if len(t) >= 3 and self.cluster_effect:
                e.update(self.cluster_effect)
        return {"n": len(rows), "binary_terms": terms, "value_terms": [], "values": {}, "latent": {},
                "intercept": [math.log(self.p / (1 - self.p))], "beta": [[0.0] * len(terms)],
                "clear_binary": [], "support_rows": [], "effects": effects, "divergences": 0}


def _seed_history():
    for i in range(30):
        ts = NOW - datetime.timedelta(days=60 - i * 2)
        _seed(f"d-{i:02d}", ts, _attrs(word_count=40 + i), SENT if i % 4 == 0 else EDITED)


def test_run_ships_a_fit_within_tolerance_and_writes_the_model():
    _seed_history()
    stub = StubFit(0.25)
    record = refit.run(now=NOW, fit_fn=stub)
    assert record["shipped"] is True and record["version"] == 1
    assert stub.calls[-1][0] == 30
    with open(refit.model_path(), encoding="utf-8") as fh:
        model = json.load(fh)
    assert model["version"] == 1 and model["holdout"]["n"] == record["n_holdout"]
    assert os.path.isfile(os.path.join(refit.model_dir(), "model-v1.json"))
    with open(refit.runs_path(), encoding="utf-8") as fh:
        assert json.loads(fh.readline())["version"] == 1


def test_run_keeps_the_live_model_when_the_fit_loses():
    _seed_history()
    record = refit.run(now=NOW, fit_fn=StubFit(0.9))
    assert record["shipped"] is False
    assert not os.path.isfile(refit.model_path())


def test_dry_run_writes_nothing():
    _seed_history()
    record = refit.run(now=NOW, fit_fn=StubFit(0.25), dry_run=True)
    assert record["shipped"] is True
    assert not os.path.exists(os.path.join(sc.data_dir(), "model"))
    assert not os.path.isfile(refit.runs_path())


def test_proposed_cluster_is_kept_only_when_clear():
    _seed_history()
    cluster = ["slack", "short", "few_claims"]
    os.makedirs(refit.model_dir(), exist_ok=True)
    with open(refit.proposals_path(), "w", encoding="utf-8") as fh:
        json.dump([cluster], fh)
    kept = refit.run(now=NOW, fit_fn=StubFit(0.25, {"lo": 0.2, "hi": 1.5, "clear": True}), dry_run=True)
    assert kept["cluster_verdicts"][0]["kept"] is True
    rejected = refit.run(now=NOW, fit_fn=StubFit(0.25), dry_run=True)
    assert rejected["cluster_verdicts"][0]["kept"] is False


def test_run_skips_the_gate_without_both_outcomes_in_training():
    for i in range(6):
        _seed(f"d-{i}", NOW - datetime.timedelta(days=60 - i), _attrs(), EDITED)
    record = refit.run(now=NOW, fit_fn=StubFit(0.25), dry_run=True)
    assert "skipped" in record and record["shipped"] is False


@pytest.mark.skipif(importlib.util.find_spec("pymc") is None, reason="PyMC not installed")
def test_real_fit_produces_a_scoreable_model():
    import random
    rng = random.Random(3)
    rows = []
    for i in range(40):
        short = rng.random() < 0.5
        attrs = _attrs(word_count=30 if short else 200, stakes=[rng.randint(1, 5)] * 3,
                       channel="slack" if rng.random() < 0.5 else "email")
        rows.append({"draft_id": f"d-{i}", "attrs": attrs, "y": int(short and rng.random() < 0.8)})
    noise = {"claims_total": None, "claims_unsupported": None, "content_latitude": None, "stakes": 0.5}
    model = refit.fit(rows, [["short"], ["slack"], ["short", "slack"]], noise,
                      sampler={"draws": 100, "tune": 200, "chains": 2})
    assert "stakes" in model["latent"]
    assert len(model["beta"]) == 200 and len(model["beta"][0]) == len(model["binary_terms"]) + len(model["value_terms"])
    short_p = scm.score(_attrs(word_count=30), model)["probability"]
    long_p = scm.score(_attrs(word_count=200), model)["probability"]
    assert short_p > long_p
