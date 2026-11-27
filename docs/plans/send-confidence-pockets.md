# Send-confidence: a ship gate that keeps what works

## Why

The 2026-11-25 refit shipped v4 because it beat v3 on held-out log loss (0.444 against 0.453).
v3 had auto-sent 5 of that week's 14 reactions, and Russell kept all 5.
v4 passed none of the 14, so it switched reaction auto-send off.

Two things caused the loss of reaction auto-send.
A scoring bug let a claims effect learned on written drafts lower every reaction's probability.
The ship gate compares average log loss, so a model that predicts better overall can still take away a subset that works.

The goal is that a new model keeps everything that works and ships only if it makes nothing worse, back-tested against past drafts.

## Design

The model makes one decision on its own, auto-sending a reaction, and every written draft waits for Russell.
One live model scores every draft, and a candidate replaces it only after passing the back-test below.

### Reaction fix

The [reaction scoring fix PR](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1567) makes a reaction's judged attributes enter at the center in the fit and the scorer.
With it, v4 passes all 14 of that week's reactions and v3 passes 8.

### Back-test in the ship gate

The log-loss check stays, as the guard against overfitting.
A candidate that clears it is fitted on all resolved drafts, then back-tested before it's written as the live model.

The back-test re-scores every resolved draft, auto-sent ones included, under both the live model and the full-data candidate.
Each draft body counts once.
Both models score with today's scorer and the seed stage-time scoring uses, the body's hash (the log-loss predictions seed by draft id), so the live model's verdicts match what it would do on that draft today.

- **Every protected draft must still pass.**
  A draft is protected when the live model passes it and Russell kept it: it went out near as-is, or it was auto-sent and he left it.
- **Rejected drafts gain no new pass.**
  The candidate passes no draft the live model declines that Russell discarded for its wording, edited past near-as-is (a changed emoji included), or sent back with feedback.
- **Tolerance starts at zero lost drafts.**
  If candidates start getting held back over only a few lost drafts, Russell revisits the tolerance.

A held-back run keeps the live model, like a run that fails on log loss.
Its record lists each lost or newly passed draft with the old and new floors and support.

Run on the 2026-11-25 refit with that day's scorer, this holds v4 back: it loses the 5 reactions v3 auto-sent.

### Per-kind roll-up

The refit record and the weekly report show, for each kind and each confirmed cluster, how many resolved drafts the live model passes and how many of those Russell kept.
A new subset clearing the 90% auto-send bar shows up as a row.
The report also lists what a held-back run would have lost or newly passed.

A separate [report PR showing each top-band draft's floor](https://github.com/rrrutledge/personal-ai-pod/pull/632) against that bar covers floor visibility.

## When an auto-sent reaction is wrong

`slack_reaction.py undo` takes the reaction off and logs a discard, which counts as a miss.
The next fit learns from it, and the draft leaves the protected set.
Once a shipped model declines that draft, the back-test blocks any later candidate that would pass it again.
Open item 1 covers the time before that model ships.

## Build order

The work lands in three steps.

- **A. Merge the reaction scoring fix.**
  The PR linked under [Reaction fix](#reaction-fix), as it stands.
- **B. Add the back-test to the gate.**
  About 40 lines in `refit.run`, plus the run docstring and `send-confidence.md`.
- **C. Add the roll-up and report.**
  Build the [per-kind roll-up](#per-kind-roll-up): the run record first, then the weekly report (`personal-ai-pod`).

A and B land before the next weekly refit (Wednesday 2026-12-02, about 10:15 PM Central), which otherwise ships on log loss alone.

## Left out on purpose

"Pockets" (subsets of drafts, each pinned to its own model version) are left out: with one decision to protect, the back-test alone keeps a working subset working.

| Left out | What covers it |
|----------|----------------|
| `pockets.json` | The live model file is the one record of what auto-sends. |
| Candidate, shadow, live, and suspended states | Written drafts all wait for Russell, so a new subset only needs to be visible, in the roll-up. |
| Per-pocket scoring at stage time | One live model scores every draft. |
| A separate pocket search | The cluster screen proposes subsets already. |
| Re-pinning | A candidate that passes the gate is the update, for every subset at once. |
| Suspension thresholds | An undo counts as a miss in the next fit. |
| A six-column pocket report | The roll-up's passed and kept counts say what a subset is doing. |

## Deferred

If the gate holds back two refits in a row only because of reactions while log loss clearly improves, add a per-kind version override, such as `{"reaction": 3}`.

## Open items

Each item is a decision for Russell.

1. **An undo waits for a ship.**
   The live model keeps its verdicts until a new model ships.
   A candidate that grows warier of reactions is likely to drop some protected reaction too, which holds it back.
   Recommended: build B as written, and revisit the protected set when a held-back run follows an undo.
   The alternative is one more condition: after an undo since the live model was fitted, reactions leave the protected set.
2. **Version numbers repeat after a rollback.**
   A shipped fit takes the live version plus one.
   v3 is live after the 2026-11-26 rollback, so the next shipped fit rewrites `model-v4.json`.
   The 2026-11-25 v4 is saved as `model-v4-shipped-2026-11-25.json`.
   Recommended: B numbers a shipped fit one above the highest `model-v<N>.json` on disk.
3. **A judge change mixes two scales.**
   The fit uses only the current judge's drafts, while the back-test as designed covers every resolved draft.
   Recommended: the back-test covers the current judge's written drafts and every reaction, since a reaction has no judged readings.
4. **Research pass for new attributes.**
   It reads the drafts that went out as-is and asks what they share that the model doesn't measure yet, output traits like length and complexity included.
   The weekly screen only combines existing flags.
   Recommended: launch it as its own session.
