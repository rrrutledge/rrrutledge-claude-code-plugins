# Send-confidence: a ship gate that keeps what works

## Why

The 2026-11-25 refit shipped v4 because it beat v3 on held-out log loss (0.444 against 0.453).
v3 had auto-sent 5 of that week's 14 reactions, and Russell kept all 5.
v4 passed none of the 14, so shipping v4 switched reaction auto-send off.
v3 has been live again since 2026-11-26.

Two things caused the loss of reaction auto-send.
A scoring bug let a claims effect learned on written drafts lower every reaction's probability.
The ship gate compares average log loss, so a model that predicts better overall can still take away a subset that works.

The goal is a model that keeps everything that works, ships a new version only when it makes nothing worse on past drafts, and keeps finding new attributes to measure.

## Design

The model makes one decision on its own, auto-sending a reaction, and every written draft waits for Russell.

### Reaction fix

The [reaction scoring fix](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1567) makes a reaction's judged attributes enter at the center in the fit and the scorer.
With it, v4 passes all 14 of that week's reactions and v3 passes 8.

### Back-test in the ship gate

A candidate first clears the log-loss check, the guard against overfitting.
It is then fitted on all resolved drafts and back-tested before it's written as the live model.

The back-test re-scores past drafts under both the live model and the full-data candidate.
It covers every resolved reaction and every resolved written draft the current judge scored, auto-sent ones included, each draft body once.
Both models score with today's scorer and the seed stage-time scoring uses, the body's hash, so the live model's verdicts match what it would do on that draft today.

A candidate ships when it meets both of these conditions.

- **Every protected draft must still pass.**
  A draft is protected when the live model passes it and Russell kept it: it went out near as-is, or it was auto-sent and he left it.
- **Rejected drafts gain no new pass.**
  A draft is rejected when Russell discarded it for its wording, edited it past near-as-is (a changed emoji included), or sent it back with feedback.
  The candidate passes no rejected draft that the live model declines.

A candidate that breaks either condition, even on a single draft, is held back, and the run keeps the live model.
The run record lists each lost or newly passed draft with the old and new floors and support.

A shipped model takes the number one above the highest model version on disk, so every version file stays.

On the 2026-11-25 refit with that day's scorer, this back-test holds v4 back: v4 loses the 5 reactions v3 auto-sent.

### Undoing an auto-sent reaction

An undo takes the reaction off and counts as a miss in the next fit, and the draft leaves the protected set.
The live model keeps its verdicts until a new model ships.
Once a shipped model declines that draft, the back-test blocks any later candidate that would pass it again.

### Weekly report

The refit record and the State of the Pod report show, for each kind and each confirmed cluster, how many resolved drafts the live model passes and how many of those Russell kept, so a subset that starts clearing the 90% auto-send bar is visible.
The report also lists what a held-back run would have lost or newly passed, and [each top-band draft's floor](https://github.com/rrrutledge/personal-ai-pod/pull/632) against the bar.

### Weekly research pass for new attributes

Each State of the Pod run also looks for attributes the model should measure.

1. The report script gathers the week's resolved written drafts in two groups: those that went out near as-is, and the [rejected ones](#back-test-in-the-ship-gate).
2. The State of the Pod session hands both groups, with the list of current attributes, to a subagent.
   The subagent finds traits the as-is drafts share, the other group mostly lacks, and the model leaves unmeasured, output traits included.
3. In its send-confidence walk-through, the session presents each candidate with the count of drafts in each group that show it and a few examples.
4. Russell picks which candidates become attributes, and the session opens a PR for each.
   The next refit fits the new attribute, and the back-test guards the ship.
5. The session records the candidates he turns down, and later runs propose other ones.

## Build order

The work lands in four steps.

- **A. Merge the reaction scoring fix.**
  This is the [reaction fix](#reaction-fix) above.
- **B. Add the back-test to the gate.**
  The change covers the refit, its docstring, and `send-confidence.md`.
- **C. Extend the State of the Pod report.**
  The per-kind and per-cluster pass and kept counts go in the run record first, then in the report in `personal-ai-pod`.
- **D. Add the weekly research pass.**
  The gathering goes in the report script and the walk-through step in the State of the Pod session's prompt, both in `personal-ai-pod`.

A and B land before the refit on the night of Wednesday 2026-12-02, which otherwise ships on log loss alone.
