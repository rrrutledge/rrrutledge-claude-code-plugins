"""The send-confidence model's terms, its target, and stage-time scoring - plain Python, no PyMC.

The weekly refit (`refit.py`) fits a Bayesian logistic regression over these terms and writes the
coefficient file; the scorer reads that file and scores a draft with the arithmetic here, so the
fit and the scorer build every term from one definition.

A draft's terms come from its scored event's `attributes` block:

- **Value terms.** Each numeric attribute, transformed and standardized with the center and scale
  the fit stored. The four judged attributes (both claim counts, content latitude, stakes) are
  errors-in-variables terms: the fit treats each draft's true value as a latent observed through
  its readings with the attribute's pooled noise, and the scorer draws it the same way.
- **Binary terms.** Each is a conjunction of literals: a flag (`few_claims`), a negated flag
  (`!slack`), or a kind (`kind:scheduling`). The defaults are every flag, every pair of flags, and
  every kind; the cluster screen adds named three-literal conjunctions.

A flag reads the low median of a judged attribute's readings, so it sits on the integer scale the
judge rates on; a missing attribute leaves its flag at 0 and the "no value" markers carry it.

A reaction (an emoji added to a message) is a draft of kind `reaction`. It has no text, so its judged
attributes and its word count are missing: they enter at the center, its `short` flag stays 0, and the
`kind:reaction` term alone carries what sets a reaction apart from a written draft.
"""

import itertools
import math
import random
import re
import statistics

# The target: near as-is is at most 5% of characters changed.
NEAR_AS_IS_MAX_DISTANCE = 0.05

# A discard where Russell answered some other way - another channel, a reaction, a call, in person -
# is a routing outcome: Russell chose a different route, so the discard says nothing about the wording.
ROUTE_DISCARD_RE = re.compile(
    r"\binstead\b|\bdirectly\b|\bphone\b|in person|\blive\b|reaction|portal|channel|switched|himself|"
    r"already (?:met|replied|handled|answered|sent)", re.IGNORECASE)

# The auto-send test: the floor (5th percentile across coefficient sets) clears AUTO_SEND_FLOOR and
# at least AUTO_SEND_MIN_SUPPORT resolved drafts share the terms behind the prediction.
FLOOR_QUANTILE = 0.05
AUTO_SEND_FLOOR = 0.90
AUTO_SEND_MIN_SUPPORT = 20

# A term's effect is clearly nonzero when its 90% interval excludes zero.
EFFECT_INTERVAL = (0.05, 0.95)

JUDGED = ("claims_total", "claims_unsupported", "content_latitude", "stakes")
EXACT = ("turn_count", "familiarity", "rule_coverage", "word_count")
TRANSFORMS = {"claims_total": "raw", "claims_unsupported": "raw", "content_latitude": "raw",
              "stakes": "raw", "turn_count": "log1p", "familiarity": "log1p",
              "rule_coverage": "raw", "word_count": "log1p"}

# (flag, attribute, comparison, cut)
NUMERIC_FLAGS = (
    ("few_claims", "claims_total", "<=", 2),
    ("no_unsupported", "claims_unsupported", "<=", 0),
    ("low_latitude", "content_latitude", "<=", 2),
    ("low_stakes", "stakes", "<=", 2),
    ("no_turns", "turn_count", "<=", 0),
    ("familiar", "familiarity", ">=", 5),
    ("high_coverage", "rule_coverage", ">=", 4),
    ("short", "word_count", "<=", 60),
)
FLAG_NAMES = [f for f, _, _, _ in NUMERIC_FLAGS] + ["slack", "familiarity_missing", "rule_coverage_missing"]
# The judge labels a written draft's kind from its text; the stager sets a reaction's, since an
# emoji has no text to read.
MESSAGE_KINDS = ("scheduling", "thanks-ack", "info-request", "decline", "nudge", "substantive-reply")
STAGER_KINDS = ("reaction",)


# --- the target ---

def is_routing_discard(outcome):
    """A discard that says nothing about the wording: Russell resolved the matter some other way,
    or the outcome sweep found no send at all in the draft's own source, which can't tell a
    rewrite from another route. An outcome that records `moot` says which it was; one logged
    before outcomes carried `moot` is read from its reason."""
    if outcome.get("disposition") != "discarded":
        return False
    if outcome.get("source") == "sweep":
        return True
    if "moot" in outcome:
        return bool(outcome["moot"])
    return bool(ROUTE_DISCARD_RE.search(outcome.get("reason") or ""))


def target(outcome):
    """1 when the draft went out near as-is, 0 when it was edited past that or discarded for its
    wording, None when the outcome leaves the target (routing discards, superseded drafts, an
    outcome with no measured distance, and a reaction the model sent on its own, which Russell
    never ruled on)."""
    disposition = (outcome or {}).get("disposition")
    if disposition in ("sent-as-is", "edited"):
        distance = outcome.get("edit_distance")
        return None if distance is None else int(distance <= NEAR_AS_IS_MAX_DISTANCE)
    if disposition == "discarded":
        return None if is_routing_discard(outcome) else 0
    return None


# --- attributes to terms ---

def readings(attrs, name):
    values = attrs.get(name)
    if values is None:
        return []
    values = values if isinstance(values, list) else [values]
    return [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def flag_value(attrs, name):
    """The value a flag reads: a judged attribute's low median reading, or the exact value."""
    if name in JUDGED:
        values = readings(attrs, name)
        return statistics.median_low(values) if values else None
    value = attrs.get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def flags(attrs):
    out = {}
    for flag, name, op, cut in NUMERIC_FLAGS:
        value = flag_value(attrs, name)
        out[flag] = 0 if value is None else int(value <= cut if op == "<=" else value >= cut)
    out["slack"] = int(attrs.get("channel") == "slack")
    out["familiarity_missing"] = int(bool(attrs.get("familiarity_missing")))
    out["rule_coverage_missing"] = int(bool(attrs.get("rule_coverage_missing")))
    return out


def _kind_label(attrs):
    kind = attrs.get("kind")
    return kind.get("label") if isinstance(kind, dict) else kind


def literal_value(literal, flag_values, kind):
    if literal.startswith("kind:"):
        return int(kind == literal[5:])
    if literal.startswith("!"):
        return 1 - flag_values[literal[1:]]
    return flag_values[literal]


def binary_row(attrs, terms):
    """Each binary term's 0/1 value for one draft, in `terms` order."""
    flag_values, kind = flags(attrs), _kind_label(attrs)
    return [int(all(literal_value(lit, flag_values, kind) for lit in term)) for term in terms]


def default_binary_terms():
    singles = [[f] for f in FLAG_NAMES]
    pairs = [list(p) for p in itertools.combinations(FLAG_NAMES, 2)]
    kinds = [[f"kind:{k}"] for k in MESSAGE_KINDS + STAGER_KINDS]
    return singles + pairs + kinds


def term_name(term):
    return " & ".join(term)


def transform(name, value):
    return math.log1p(value) if TRANSFORMS[name] == "log1p" else float(value)


def observed_value(attrs, name):
    """(mean of the transformed readings, how many) for a judged attribute, or (value, 1) for an
    exact one; (None, 0) when it has no value."""
    if name in JUDGED:
        values = readings(attrs, name)
        if not values:
            return None, 0
        return sum(transform(name, v) for v in values) / len(values), len(values)
    value = flag_value(attrs, name)
    return (None, 0) if value is None else (transform(name, value), 1)


# --- stage-time scoring from the coefficient file ---

def _sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def quantile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def set_probabilities(attrs, model, seed=0):
    """The draft's probability of going out near as-is under each coefficient set.

    Exact values and judged values with no noise figure enter as measured, a missing one at the
    center. A judged value with a noise figure is drawn per set from its posterior given that
    set's population mean and spread, the readings' mean, and the noise shrunk by the number of
    readings; with no readings it is drawn from the population alone."""
    rng = random.Random(seed)
    xb = binary_row(attrs, model["binary_terms"])
    active = [j for j, v in enumerate(xb) if v]
    n_binary = len(model["binary_terms"])
    values = model["values"]
    fixed, latent = [], []
    for j, name in enumerate(model["value_terms"]):
        spec = values[name]
        obs, k = observed_value(attrs, name)
        if name in model.get("latent", {}):
            latent.append((n_binary + j, spec, obs, k, model["latent"][name]))
        else:
            fixed.append((n_binary + j, 0.0 if obs is None else (obs - spec["center"]) / spec["scale"]))

    out = []
    for s, (intercept, beta) in enumerate(zip(model["intercept"], model["beta"])):
        lin = intercept + sum(beta[j] for j in active) + sum(beta[j] * x for j, x in fixed)
        for j, spec, obs, k, lat in latent:
            mu, sd, noise = lat["mu"][s], lat["sd"][s], lat["noise"]
            if obs is None:
                x = rng.gauss(mu, sd)
            else:
                shrink = sd * sd / (sd * sd + noise * noise / k)
                x = rng.gauss(mu + shrink * (obs - mu), math.sqrt(shrink * noise * noise / k))
            lin += beta[j] * (x - spec["center"]) / spec["scale"]
        out.append(_sigmoid(lin))
    return out


def support(attrs, model):
    """How many resolved drafts the model was fitted on share every clearly nonzero binary term
    this draft has; with none behind its prediction, how many share every one of its flags."""
    xb = binary_row(attrs, model["binary_terms"])
    clear = set(model.get("clear_binary", []))
    behind = {j for j, v in enumerate(xb) if v and j in clear}
    if behind:
        return sum(1 for row in model["support_rows"] if behind <= set(row))
    own = flags(attrs)
    return sum(1 for row in model.get("support_flags", []) if row == own)


def score(attrs, model, seed=0):
    """{"version", "probability", "floor", "support", "auto_send"} for one draft: the mean
    probability across coefficient sets, its 5th percentile, the support behind it, and whether
    it passes the auto-send test."""
    probs = set_probabilities(attrs, model, seed)
    mean = sum(probs) / len(probs)
    floor = quantile(probs, FLOOR_QUANTILE)
    n_support = support(attrs, model)
    return {"version": model.get("version"), "probability": round(mean, 4), "floor": round(floor, 4),
            "support": n_support,
            "auto_send": floor >= AUTO_SEND_FLOOR and n_support >= AUTO_SEND_MIN_SUPPORT}
