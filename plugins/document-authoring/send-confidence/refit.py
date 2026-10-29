#!/usr/bin/env python3
"""The weekly send-confidence refit - plain code, no AI judgment.

It fits a Bayesian logistic regression (PyMC, regularized horseshoe prior, non-centered) on every
resolved draft in the log, gates the new fit on held-out drafts, and when it passes writes the
coefficient file the scorer reads (`send_confidence_model.py` holds the terms and the scoring
arithmetic both sides share).

One run:

1. **Gather.** Each resolved draft's attributes and target (`send_confidence_model.target`), one
   row per draft body, so two stages of one draft count once.
   A scored event from before the `attributes` block (event v2 and v3) contributes what its
   `features` held - one judge reading per attribute, word count from the draft snapshot, no kind.
   Event v1 rated phrasing complexity and task ambiguity rather than stakes and latitude, so it
   stays out.
   Only drafts the current judge (`send_confidence.JUDGE_MODEL`) scored count, since another
   judge's readings sit on a scale of their own.
   While a live model exists and the current judge has scored fewer than GENERATION_MIN_DRAFTS
   resolved drafts, the run skips, so the first fit on a new judge's readings is graded against
   the live model rather than a base rate.
2. **Noise.** Each judged attribute's noise figure is the pooled within-draft spread of every
   scored event's three readings from the current judge, resolved or not; until
   NOISE_MIN_DRAFTS drafts carry readings for it, the attribute enters as measured.
3. **Gate.** Fit on every resolved draft except the last HOLDOUT_DAYS, while the log is young
   capped at the newest half of the drafts so the fit always trains on at least half. The new
   fit ships when its log loss is no worse than the live model's by more than GATE_TOLERANCE,
   each graded only on drafts neither was fitted on: the held-out drafts scored after the live
   model was fitted, once there are GATE_MIN_FRESH of them. Before that, and while no model is
   live, the comparison is the training split's base rate over every held-out draft.
4. **Clusters.** Proposed clusters enter a second held-out fit as named terms; each passes only
   when its effect is clearly nonzero and that fit does at least as well on the held-out drafts as
   the fit without them. A passing cluster joins the model when the fit ships, and stays proposed
   until then; a failing one is never proposed again.
5. **Ship.** A passing fit is refit on all resolved drafts and written as the next model version.
6. **Screen.** Residual cells (every three flags shared by at least SCREEN_MIN_DRAFTS training
   drafts, compared with what the live model predicts for them) and a three-level decision tree
   over the flags propose new clusters from the training split, so next week's held-out drafts
   test them on drafts they weren't chosen from.

Every run appends a record to `refit-runs.jsonl` - the split, both log losses, the verdict, each
cluster's verdict, the noise figures, and the new fit's clearly nonzero effects - for the weekly
report.

CLI:
    python refit.py [--dry-run] [--json]
"""

import datetime
import itertools
import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import send_confidence as sc  # noqa: E402
import send_confidence_model as scm  # noqa: E402

HOLDOUT_DAYS = 28
GATE_TOLERANCE = 0.01
GATE_MIN_FRESH = 10
# Enough drafts that the newest half holds GATE_MIN_FRESH for the gate to grade the live model on.
GENERATION_MIN_DRAFTS = 2 * GATE_MIN_FRESH
NOISE_MIN_DRAFTS = 10
SCREEN_MIN_DRAFTS = 15
SCREEN_MIN_Z = 2.0
TREE_DEPTH = 3
TREE_MIN_LEAF = 5
MAX_PROPOSALS = 5
MAX_SETS = 2000
# Horseshoe settings after Piironen and Vehtari (2017): the prior guess at how many terms carry
# real signal sets the global scale, and a slab caps how large any one effect can grow.
EXPECTED_NONZERO = 3
SLAB_SCALE = 2.0
SLAB_DF = 4
SAMPLER = {"draws": 500, "tune": 1000, "chains": 4, "target_accept": 0.95}


def model_dir():
    return os.path.dirname(sc.model_path())


def proposals_path():
    return os.path.join(model_dir(), "clusters.json")


def runs_path():
    return os.path.join(sc.data_dir(), "refit-runs.jsonl")


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)


# --- 1. gather ---

def legacy_attributes(scored, draft_text):
    """A v4-shaped attributes block from an event v2/v3 `features` block: one reading per judged
    attribute, word count from the draft snapshot, and no kind."""
    f = scored.get("features") or {}

    def one(key):
        value = f.get(key)
        return None if value is None else [value]

    familiarity, coverage = f.get("sent_count"), f.get("rule_coverage_rating")
    channel = scored.get("channel")
    return {
        "claims_total": one("claims_total"), "claims_unsupported": one("claims_unsupported"),
        "content_latitude": one("latitude_rating"), "stakes": one("stakes_rating"), "kind": None,
        "turn_count": scored.get("turns_before_draft"),
        "familiarity": familiarity, "familiarity_missing": familiarity is None,
        "rule_coverage": coverage, "rule_coverage_missing": coverage is None,
        "word_count": None if draft_text is None else len(sc._plain_text(draft_text).split()),
        "channel": sc.CHANNEL_CLASSES.get(channel, "other") if channel else None,
    }


def _draft_text(draft_id):
    try:
        with open(os.path.join(sc.drafts_dir(), f"{draft_id}.txt"), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def judge_model(scored):
    return scored.get("judge_model") or sc.LEGACY_JUDGE_MODEL


def gather(drafts=None):
    """[{"draft_id", "ts", "attrs", "y"}] for every resolved draft the current judge scored that
    has a target, oldest first, keeping the earliest scored row of each draft body."""
    drafts = drafts if drafts is not None else sc.fold_drafts()
    rows = []
    for draft_id, d in drafts.items():
        scored, outcome = d["scored"], d["outcome"]
        if not scored or (scored.get("v") or 1) < 2 or judge_model(scored) != sc.JUDGE_MODEL:
            continue
        y = scm.target(outcome)
        ts = sc._parse_ts(scored.get("ts"))
        if y is None or ts is None:
            continue
        attrs = scored.get("attributes") or legacy_attributes(scored, _draft_text(draft_id))
        rows.append({"draft_id": draft_id, "ts": ts, "attrs": attrs, "y": y, "sha256": scored.get("sha256")})
    rows.sort(key=lambda r: r["ts"])
    seen, out = set(), []
    for r in rows:
        if r["sha256"] and r["sha256"] in seen:
            continue
        seen.add(r["sha256"])
        out.append(r)
    return out


# --- 2. noise ---

def pooled_noise(events=None):
    """{attribute: noise sd or None}: the pooled within-draft standard deviation of the current
    judge's repeated readings, None until NOISE_MIN_DRAFTS drafts carry two or more."""
    events = events if events is not None else sc.load_events()
    sums = {name: [0.0, 0, 0] for name in scm.JUDGED}
    for e in events:
        if e.get("event") != "scored" or not e.get("attributes") or judge_model(e) != sc.JUDGE_MODEL:
            continue
        for name in scm.JUDGED:
            values = [scm.transform(name, v) for v in scm.readings(e["attributes"], name)]
            if len(values) < 2:
                continue
            mean = sum(values) / len(values)
            acc = sums[name]
            acc[0] += sum((v - mean) ** 2 for v in values)
            acc[1] += len(values) - 1
            acc[2] += 1
    out = {}
    for name, (ss, dof, n) in sums.items():
        out[name] = round(max(math.sqrt(ss / dof), 0.05), 4) if n >= NOISE_MIN_DRAFTS and dof else None
    return out


# --- 3. the fit ---

def value_specs(rows):
    """Each value term's transform, center, and scale from the drafts the fit trains on."""
    specs = {}
    for name in scm.JUDGED + scm.EXACT:
        xs = [x for x, _ in (scm.observed_value(r["attrs"], name) for r in rows) if x is not None]
        center = sum(xs) / len(xs) if xs else 0.0
        sd = math.sqrt(sum((x - center) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
        specs[name] = {"transform": scm.TRANSFORMS[name], "center": round(center, 6),
                       "scale": round(sd, 6) if sd > 1e-9 else 1.0, "n": len(xs), "varies": sd > 1e-9}
    return specs


def design(rows, binary_terms):
    """The binary terms that vary across `rows` (a constant one is the intercept or nothing),
    and their 0/1 rows."""
    matrix = [scm.binary_row(r["attrs"], binary_terms) for r in rows]
    keep = [j for j in range(len(binary_terms)) if 0 < sum(row[j] for row in matrix) < len(rows)]
    return [binary_terms[j] for j in keep], [[row[j] for j in keep] for row in matrix]


def fit(rows, binary_terms, noise, *, sampler=None, seed=1):
    """Fit the model on `rows` and return the coefficient-file body (everything but the version)."""
    import numpy as np
    import pymc as pm

    sampler = {**SAMPLER, **(sampler or {})}
    specs = value_specs(rows)
    terms, xb = design(rows, binary_terms)
    value_terms = [name for name in scm.JUDGED + scm.EXACT if specs[name]["varies"]]
    latent_terms = [name for name in value_terms if name in scm.JUDGED and noise.get(name)]
    y = np.array([r["y"] for r in rows])
    n = len(rows)
    base = min(max(y.mean(), 0.02), 0.98)

    columns = []
    with pm.Model():
        for name in value_terms:
            spec = specs[name]
            obs = [scm.observed_value(r["attrs"], name) for r in rows]
            if name in latent_terms:
                idx = np.array([i for i, (x, _) in enumerate(obs) if x is not None])
                mu = pm.Normal(f"mu_{name}", spec["center"], spec["scale"])
                sd = pm.HalfNormal(f"sd_{name}", spec["scale"])
                x = mu + sd * pm.Normal(f"u_{name}", 0, 1, shape=n)
                if len(idx):
                    k = np.array([obs[i][1] for i in idx], dtype=float)
                    pm.Normal(f"obs_{name}", mu=x[idx], sigma=noise[name] / np.sqrt(k),
                              observed=np.array([obs[i][0] for i in idx]))
                columns.append((x - spec["center"]) / spec["scale"])
            else:
                columns.append(np.array([0.0 if x is None else (x - spec["center"]) / spec["scale"]
                                         for x, _ in obs]))

        d = len(terms) + len(value_terms)
        tau0 = EXPECTED_NONZERO / max(d - EXPECTED_NONZERO, 1) * 2 / math.sqrt(n)
        intercept = pm.Normal("intercept", math.log(base / (1 - base)), 1.5)
        z = pm.Normal("z", 0, 1, shape=d)
        lam = pm.HalfCauchy("lam", 1, shape=d)
        tau = pm.HalfCauchy("tau", tau0)
        c2 = pm.InverseGamma("c2", SLAB_DF / 2, SLAB_DF * SLAB_SCALE ** 2 / 2)
        lam_tilde = pm.math.sqrt(c2 * lam ** 2 / (c2 + tau ** 2 * lam ** 2))
        beta = pm.Deterministic("beta", z * tau * lam_tilde)
        stack = [np.array(xb, dtype=float).reshape(n, len(terms))] if terms else []
        stack += [pm.math.stack(columns, axis=1)] if columns else []
        x_all = pm.math.concatenate(stack, axis=1)
        pm.Bernoulli("y", logit_p=intercept + pm.math.dot(x_all, beta), observed=y)
        idata = pm.sample(draws=sampler["draws"], tune=sampler["tune"], chains=sampler["chains"], cores=1,
                          target_accept=sampler["target_accept"], random_seed=seed, progressbar=False,
                          compute_convergence_checks=False)

    # PyMC 6 returns DataTree groups; earlier versions return xarray Datasets.
    post, stats = (g.to_dataset() if hasattr(g, "to_dataset") else g
                   for g in (idata.posterior, idata.sample_stats))
    post = post.stack(sample=("chain", "draw"))
    total = post.sizes["sample"]
    pick = np.linspace(0, total - 1, min(MAX_SETS, total)).round().astype(int)
    beta_draws = post["beta"].values.T[pick]
    model = {
        "fitted_at": datetime.datetime.now().astimezone().isoformat(),
        "n": n, "positives": int(y.sum()), "base_rate": round(float(y.mean()), 4),
        "binary_terms": terms, "value_terms": value_terms,
        "values": {name: specs[name] for name in value_terms},
        "latent": {name: {"noise": noise[name],
                          "mu": [round(float(v), 5) for v in post[f"mu_{name}"].values[pick]],
                          "sd": [round(float(v), 5) for v in post[f"sd_{name}"].values[pick]]}
                   for name in latent_terms},
        "intercept": [round(float(v), 5) for v in post["intercept"].values[pick]],
        "beta": [[round(float(v), 5) for v in row] for row in beta_draws],
        "divergences": int(stats["diverging"].sum()),
        "support_rows": [[j for j, v in enumerate(row) if v] for row in xb],
        "support_flags": [scm.flags(r["attrs"]) for r in rows],
    }
    model["effects"] = effects(model)
    model["clear_binary"] = [j for j, e in enumerate(model["effects"][:len(terms)]) if e["clear"]]
    return model


def effects(model):
    """Each term's posterior mean and 90% interval on the log-odds scale, binary terms first."""
    names = [scm.term_name(t) for t in model["binary_terms"]] + [f"value:{v}" for v in model["value_terms"]]
    out = []
    for j, name in enumerate(names):
        draws = [row[j] for row in model["beta"]]
        lo, hi = (scm.quantile(draws, q) for q in scm.EFFECT_INTERVAL)
        out.append({"term": name, "mean": round(sum(draws) / len(draws), 4), "lo": round(lo, 4),
                    "hi": round(hi, 4), "clear": lo > 0 or hi < 0})
    return out


# --- gate and screen ---

def split(rows, now):
    """(train, holdout): the holdout is every draft scored in the last HOLDOUT_DAYS, capped at the
    newest half of the drafts."""
    cut = now - datetime.timedelta(days=HOLDOUT_DAYS)
    first_held = next((i for i, r in enumerate(rows) if r["ts"] >= cut), len(rows))
    first_held = max(first_held, len(rows) // 2)
    return rows[:first_held], rows[first_held:]


def predict(model, rows):
    """Each row's mean probability under `model`."""
    out = []
    for r in rows:
        probs = scm.set_probabilities(r["attrs"], model, seed=r["draft_id"])
        out.append(sum(probs) / len(probs))
    return out


def log_loss(ps, ys):
    eps = 1e-6
    return sum(-math.log(min(max(p if y else 1 - p, eps), 1)) for p, y in zip(ps, ys)) / len(ys)


def residual_cells(rows, ps, taken):
    """Three-flag groups the predictions miss: (|z|, literals) for every group of at least
    SCREEN_MIN_DRAFTS drafts whose near-as-is count sits SCREEN_MIN_Z standard errors from the
    predicted count."""
    fv = [scm.flags(r["attrs"]) for r in rows]
    out = []
    for combo in itertools.combinations(scm.FLAG_NAMES, 3):
        members = [i for i, f in enumerate(fv) if all(f[c] for c in combo)]
        if len(members) < SCREEN_MIN_DRAFTS or tuple(sorted(combo)) in taken:
            continue
        observed = sum(rows[i]["y"] for i in members)
        expected = sum(ps[i] for i in members)
        var = sum(ps[i] * (1 - ps[i]) for i in members)
        z = (observed - expected) / math.sqrt(var) if var > 0 else 0.0
        if abs(z) >= SCREEN_MIN_Z:
            out.append((abs(z), list(combo)))
    return sorted(out, reverse=True)


def _gini(ys):
    if not ys:
        return 0.0
    p = sum(ys) / len(ys)
    return 2 * p * (1 - p)


def tree_paths(rows, depth=TREE_DEPTH):
    """Root-to-leaf paths of a greedy Gini tree over the flags, as (|leaf rate - overall rate|,
    literals) for every full-depth leaf holding at least SCREEN_MIN_DRAFTS drafts."""
    fv = [scm.flags(r["attrs"]) for r in rows]
    ys = [r["y"] for r in rows]
    overall = sum(ys) / len(ys) if ys else 0.0
    out = []

    def grow(members, path):
        if len(path) == depth:
            if len(members) >= SCREEN_MIN_DRAFTS:
                rate = sum(ys[i] for i in members) / len(members)
                out.append((abs(rate - overall), path))
            return
        best = None
        used = {lit.lstrip("!") for lit in path}
        for flag in scm.FLAG_NAMES:
            if flag in used:
                continue
            yes = [i for i in members if fv[i][flag]]
            no = [i for i in members if not fv[i][flag]]
            if len(yes) < TREE_MIN_LEAF or len(no) < TREE_MIN_LEAF:
                continue
            impurity = (len(yes) * _gini([ys[i] for i in yes]) + len(no) * _gini([ys[i] for i in no])) / len(members)
            if best is None or impurity < best[0]:
                best = (impurity, flag, yes, no)
        if best is None:
            return
        _, flag, yes, no = best
        grow(yes, path + [flag])
        grow(no, path + [f"!{flag}"])

    grow(list(range(len(rows))), [])
    return sorted(out, reverse=True)


def screen(rows, ps, terms):
    """Up to MAX_PROPOSALS new three-literal clusters, residual cells first, then tree leaves."""
    taken = {tuple(sorted(t)) for t in terms}
    proposals = []
    for _, literals in residual_cells(rows, ps, taken) + tree_paths(rows):
        key = tuple(sorted(literals))
        if key in taken or len(proposals) >= MAX_PROPOSALS:
            continue
        taken.add(key)
        proposals.append(literals)
    return proposals


def _is_cluster(term):
    return len(term) >= 3


# --- one run ---

def run(*, now=None, dry_run=False, fit_fn=None, sampler=None):
    """One weekly refit. Returns the run record, which it also appends to refit-runs.jsonl unless
    `dry_run`; writes the next model version when the new fit passes the gate."""
    fit_fn = fit_fn or fit
    now = now or datetime.datetime.now().astimezone()
    rows = gather()
    noise = pooled_noise()
    live = _read_json(sc.model_path())
    live = live if isinstance(live, dict) and live.get("binary_terms") is not None else None
    clusters = _read_json(proposals_path(), {}) or {}
    proposed, rejected = clusters.get("proposed", []), clusters.get("rejected", [])
    record = {"ts": now.isoformat(), "dry_run": dry_run, "n": len(rows),
              "positives": sum(r["y"] for r in rows), "noise": noise, "judge_model": sc.JUDGE_MODEL,
              "live_version": (live or {}).get("version"), "shipped": False, "cluster_verdicts": []}

    train, hold = split(rows, now)
    record.update(n_train=len(train), n_holdout=len(hold),
                  holdout_from=hold[0]["ts"].isoformat() if hold else None)
    if live and len(rows) < GENERATION_MIN_DRAFTS:
        record["skipped"] = (f"{sc.JUDGE_MODEL} has judged {len(rows)} resolved drafts; the first fit on its "
                             f"readings waits for {GENERATION_MIN_DRAFTS}")
    elif not hold or {r["y"] for r in train} != {0, 1}:
        record["skipped"] = "the training split needs both a near-as-is draft and another, and the holdout a draft"
    else:
        ys = [r["y"] for r in hold]
        fitted_at = sc._parse_ts((live or {}).get("fitted_at"))
        fresh = [i for i, r in enumerate(hold) if fitted_at and r["ts"] > fitted_at]
        if live and len(fresh) >= GATE_MIN_FRESH:
            gate = fresh
            comparator = predict(live, [hold[i] for i in gate])
            record["gate"] = f"live model v{live.get('version')} on held-out drafts scored after it was fitted"
        else:
            gate = list(range(len(hold)))
            comparator = [sum(r["y"] for r in train) / len(train)] * len(hold)
            record["gate"] = "training base rate on every held-out draft"
        record["n_gate"] = len(gate)
        record["live_loss"] = round(log_loss(comparator, [ys[i] for i in gate]), 4)

        confirmed = [t for t in (live or {}).get("binary_terms", []) if _is_cluster(t)]
        terms_a = scm.default_binary_terms() + confirmed
        fits = {}

        def held_out(terms):
            key = json.dumps(terms)
            if key not in fits:
                m = fit_fn(train, terms, noise, sampler=sampler)
                fits[key] = (m, predict(m, hold))
            return fits[key]

        def loss(ps, idx=None):
            idx = range(len(hold)) if idx is None else idx
            return log_loss([ps[i] for i in idx], [ys[i] for i in idx])

        _, ps_a = held_out(terms_a)
        passed = []
        if proposed:
            model_b, ps_b = held_out(terms_a + proposed)
            loss_a, loss_b = loss(ps_a), loss(ps_b)
            by_name = {e["term"]: e for e in model_b["effects"]}
            for cluster in proposed:
                effect = by_name.get(scm.term_name(cluster))
                kept = bool(effect and effect["clear"] and loss_b <= loss_a)
                record["cluster_verdicts"].append({"cluster": scm.term_name(cluster), "kept": kept, "effect": effect,
                                                   "loss_with": round(loss_b, 4), "loss_without": round(loss_a, 4)})
                (passed if kept else rejected).append(cluster)
        candidate_terms = terms_a + passed
        candidate, ps_c = held_out(candidate_terms)
        record["candidate_loss"] = round(loss(ps_c, gate), 4)
        record["candidate_divergences"] = candidate.get("divergences")
        record["effects"] = [e for e in candidate["effects"] if e["clear"]]
        record["shipped"] = record["candidate_loss"] <= record["live_loss"] + GATE_TOLERANCE
        # A cluster that passed stays proposed until a fit carrying it ships.
        proposed = [] if record["shipped"] else passed

        if record["shipped"]:
            model = fit_fn(rows, candidate_terms, noise, sampler=sampler)
            model["version"] = ((live or {}).get("version") or 0) + 1
            model["judge_model"] = sc.JUDGE_MODEL
            model["holdout"] = {"loss": record["candidate_loss"], "live_loss": record["live_loss"],
                                "n": len(gate), "gate": record["gate"], "from": record["holdout_from"]}
            record["version"] = model["version"]
            record["divergences"] = model["divergences"]
            record["effects"] = [e for e in model["effects"] if e["clear"]]
            if not dry_run:
                _write_json(os.path.join(model_dir(), f"model-v{model['version']}.json"), model)
                _write_json(sc.model_path(), model)
            live = model

    screen_rows = train or rows
    if screen_rows:
        ps = (predict(live, screen_rows) if live
              else [sum(r["y"] for r in screen_rows) / len(screen_rows)] * len(screen_rows))
        taken = (live or {}).get("binary_terms", []) + proposed + rejected
        proposed = proposed + screen(screen_rows, ps, taken)
    record["proposed"] = [scm.term_name(c) for c in proposed]
    if not dry_run:
        _write_json(proposals_path(), {"proposed": proposed, "rejected": rejected})
        os.makedirs(sc.data_dir(), exist_ok=True)
        with open(runs_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    return record


def main():
    argv = sys.argv[1:]
    record = run(dry_run="--dry-run" in argv)
    if "--json" in argv:
        print(json.dumps(record, indent=2))
        return 0
    print(f"send-confidence refit{' (dry run - nothing written)' if record['dry_run'] else ''}: "
          f"{record['n']} resolved drafts, {record['positives']} near as-is; "
          f"trained on {record['n_train']}, held out {record['n_holdout']}")
    if record.get("skipped"):
        print(f"no fit: {record['skipped']}")
    else:
        print(f"held-out log loss: new {record['candidate_loss']}, live {record['live_loss']} -> "
              f"{'shipped as v' + str(record['version']) if record['shipped'] else 'kept the live model'}")
        for v in record["cluster_verdicts"]:
            print(f"  cluster {v['cluster']}: {'kept' if v['kept'] else 'rejected'}")
        for e in record.get("effects", []):
            print(f"  clear effect {e['term']}: {e['mean']:+.2f} [{e['lo']:+.2f}, {e['hi']:+.2f}]")
    for name in record["proposed"]:
        print(f"  proposed for next week: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
