# Send-confidence pockets and the no-regression gate

## Why

The 2026-11-25 refit shipped v4 because it beat v3 on held-out log loss (0.444 against 0.453).
Re-scored under v4, none of the week's 14 reactions cleared the auto-send bar, so v4 quietly switched off reaction auto-send.
The direct cause was a scoring bug (a reaction's claim counts were drawn from the population), and that is fixed on its own in [#1567](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1567).
The deeper cause is the gate: it compares average log loss, so a model that predicts better overall can still take away a subset that was working.

This plan makes three changes:

1. A **pocket** is a subset of drafts with its own pinned scorer, so a working subset stays live no matter what the next global fit does.
2. The refit **searches for new pockets** alongside the global fit, without touching the live ones.
3. A candidate model **ships only if it loses nothing** a live pocket passes today, back-tested over every resolved draft.

The rule for written drafts is unchanged: nothing written auto-sends without Russell's say-so.
Reactions already auto-send under the current rule, and stay that way.

## What a pocket is

A pocket has four parts:

- **A predicate** that says which drafts belong to it: a kind (`kind:reaction`) or a conjunction of the literals the model already uses (`slack & few_claims & kind:thanks-ack`).
- **A pinned scorer**: one model version from `model/model-v<N>.json`, frozen for this pocket until the pocket is deliberately re-pinned.
- **A state**: `candidate`, `shadow`, `live`, or `suspended` (see "Lifecycle" below).
- **A track record**: its back-test at the time it was pinned, and every outcome since it went live.

Pockets live in a new file, `model/pockets.json`, next to the model versions:

```json
{"pockets": [
  {"name": "reaction",
   "predicate": [["kind:reaction"]],
   "scorer_version": 4,
   "state": "live",
   "since": "2026-11-26",
   "acts": "auto-send",
   "backtest": {"members": 48, "passed": 45, "passed_kept": 45},
   "live_record": {"passed": 0, "kept": 0, "undone": 0}}
]}
```

(The back-test numbers above are placeholders for the shape; the real ones come from the first run.)

`acts` is `auto-send` only for a reversible kind (reactions today).
For a written-draft pocket it is `report`: the pocket's verdict shows in the stage output and the weekly report, and Russell still sends.
Moving a written pocket to `auto-send` is his call, made per pocket, and recorded in this file by hand.

### Why the scorer is a pinned global version, not a separate fit

A per-pocket fit trained only on reactions would have 48 rows behind it and almost no variation to learn from, so its floor would sit wide and low.
The global fit already carries `kind:reaction` as its one clearly nonzero term; what a reaction needs is protection from the next fit, not a model of its own.
Pinning a whole global version gives that protection with no new fitting code.
A pocket-specific fit stays an option for later, once a pocket holds a few hundred resolved drafts, and slots into the same `scorer_version` field.

## Scoring with pockets

At stage time the scorer does what it does today and then checks pockets:

1. Score the draft under the live global model, as today; that score stays the `model` block in the log.
2. For every pocket in `shadow`, `live`, or `suspended` state whose predicate the draft matches, score it again under that pocket's pinned version.
   Each result is logged in a new `pockets` list on the scored event: name, version, floor, support, and whether it passed.
3. The draft's auto-send verdict is true when any matching `live` pocket with `acts: auto-send` passes it.
   The global model's own verdict no longer auto-sends anything by itself.

Step 3 changes one thing for reactions: the reaction pocket, not the global model, decides.
On the day this ships the reaction pocket is pinned to whichever version passes the most of its kept reactions in the back-test (v4 with the #1567 fix passes every one of this week's 14).

Support is counted inside the pocket's pinned version, as today, so the 20-draft minimum still holds per pocket.

## Lifecycle

| State | Scores drafts | Acts on them | Enters by | Leaves by |
|-------|---------------|--------------|-----------|-----------|
| `candidate` | no | no | the refit's pocket search proposes it | qualifying in a back-test, or failing twice |
| `shadow` | yes, logged | no | qualifying in the back-test | Russell turns it live, or its shadow record turns bad |
| `live` | yes, logged | auto-send (reversible kinds) or a report line (written) | Russell's say-so; the reaction pocket starts here | suspension |
| `suspended` | yes, logged | no, every draft goes to Russell | its live record turns bad | re-qualifying after a re-pin, plus Russell's say-so |

A pocket qualifies when, under its scorer, at least 20 resolved members pass and the near-as-is rate among those that pass has a lower 90% bound at or above 0.90.
That is the same bar the draft-level test uses, applied to the pocket's own history instead of one draft's posterior.

## How the refit searches for new pockets

The search runs after the global gate and never edits a live or shadow pocket.

1. **Propose.** Every kind with at least 20 resolved drafts, plus every cluster the existing screen proposes (residual cells and tree leaves), is a candidate predicate.
2. **Back-test.** Score each candidate's resolved members under each available model version (the live one, the new candidate fit, and the version any overlapping pocket is pinned to), using the full-data fit of each.
   The best version that qualifies becomes the candidate's proposed scorer.
3. **Record.** A qualifying candidate moves to `shadow` with that version pinned; a candidate that fails two weekly runs in a row is dropped, like a rejected cluster today.

Because pockets pin versions, a new global version never changes what a live pocket does.
A live pocket moves to a newer version only by **re-pinning**, which the refit proposes when a newer version qualifies on the pocket and loses none of its protected drafts (next section).
Re-pinning is automatic for reactions and reported for written pockets; that keeps reaction auto-send current without Russell tending it, and leaves every written-draft change in his hands.

## The ship gate: no regression

Today a candidate ships when its held-out log loss is within 0.01 of the live model's.
That check stays, because it is the guard against overfitting.
A second check is added, and both must pass.

**The protected set** is every resolved draft that:

- matches a `live` pocket,
- passes under that pocket's pinned scorer, and
- went out as proposed: a near-as-is outcome, or an auto-sent reaction Russell didn't undo.

**The check:** score the full-data candidate on every protected draft, with the scorer's own fixed seed.
The candidate passes when it passes every protected draft.
A candidate that drops even one is held back, and the run record lists the drafts it would have lost with their old and new floors.

This is a regression test, not an estimate of future accuracy, so it deliberately scores the fitted model on drafts it was trained on: the question is "does this model still say yes to what we already say yes to and Russell kept".
The log-loss check answers the generalization question.

The same check guards re-pinning a pocket, restricted to that pocket's protected drafts.

With pockets pinned, the global gate mostly protects the future: the global version is what new pockets get back-tested against, and what the report shows for every draft outside a pocket.
Holding it to the no-regression rule too means the report never shows a pocket's drafts getting worse under the model that's live.

### When the protected set is wrong

A draft leaves the protected set when its pocket is suspended, so the gate never pins a pocket to behavior its own outcomes have turned against.
Russell can also drop a single draft from the set (a reaction he kept by accident, say) by marking its outcome; the run record names any draft dropped that way.

## When a live pocket's outcomes turn bad

Each live pocket keeps a running record of what happened to the drafts it acted on:

- For an auto-send pocket: auto-sent and kept for 7 days, or undone.
- For a report pocket: passed and sent near as-is, or passed and edited or discarded for its wording.

**Suspension rule (proposed):** the pocket is suspended at once when either holds:

- two failures in its last ten acted-on drafts, or
- the lower 90% bound on its live success rate drops under 0.80 with at least 20 drafts behind it.

The first catches a sudden break fast (a new channel, or a kind of message the predicate didn't foresee); the second catches a slow drift.
A suspended pocket keeps scoring and logging, so its record keeps growing, but every draft it matches goes to Russell.
The weekly report lists the suspension, the drafts that triggered it, and the version it was pinned to.
It returns to `live` only after a refit re-pins it to a version that qualifies on its newest outcomes, and Russell says so.

One wrinkle for auto-send pockets: an auto-sent reaction leaves the fitting target unless it's undone, so the global fit learns little from them.
The pocket's live record counts "kept for 7 days" as a success for monitoring only.
The fit keeps its current target, so a reaction Russell never looked at is never taught to the model as an approval.

## Report changes (State of the Pod)

The floor-visibility change ships now, separately, in `personal-ai-pod`: each top-band draft's floor against the 90% bar, its kind, and whether it failed on floor or support.
Once pockets exist, the report adds a pocket section:

| Column | Content |
|--------|---------|
| Pocket | name and predicate |
| State | candidate / shadow / live / suspended, with the date it entered |
| Scorer | pinned version, and the newest version that would qualify if different |
| Back-test | members, passed, passed and kept |
| Live record | acted on, kept, failed, in the last 7 days and all-time |
| Next | what's waiting on Russell, if anything (turn a shadow pocket live, approve a re-pin, review a suspension) |

The refit section adds one line per held-back candidate naming the protected drafts it would have lost, so a "kept the live model" verdict always says why.

## Build order

1. **Reaction fix** ([#1567](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1567)): a reaction's judged attributes enter at the center in the fit and the scorer.
2. **Pockets file and scorer**: `pockets.json`, per-pocket scoring at stage time, the `pockets` list on the scored event, and the reaction pocket as the only live one.
   The auto-send verdict moves from the global model to the pocket.
3. **No-regression gate** in `refit.py`: the protected set, the check, and the run-record lines.
4. **Pocket search and lifecycle** in `refit.py`: proposal, back-test, shadow, re-pin, suspension.
5. **Report**: the pocket section and the held-back-candidate lines in `pod-review.py`.

Steps 2 and 3 alone would have prevented this week's regression; steps 4 and 5 are what find the next pocket.

## Decisions for Russell

1. **Auto-repin for reactions.**
   The plan re-pins the reaction pocket to a newer version on its own when the newer one qualifies and loses nothing.
   The alternative is to report the re-pin and wait for you, as written pockets do.
2. **Suspension thresholds.**
   Two failures in ten, or a lower bound under 0.80 over 20.
   Tighter catches breaks sooner and suspends on noise more often.
3. **Qualifying bar for a pocket.**
   The plan reuses 0.90 on the lower 90% bound of the pocket's pass-and-kept rate, with 20 drafts.
   A pocket that clears it moves to shadow, never straight to live.
