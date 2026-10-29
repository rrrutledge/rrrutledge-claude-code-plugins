# Send-confidence: scoring an outward draft

The stage gate scores every outward draft it lets through; the drafting session supplies the reviewer's rule coverage and uncertain spots at mint, below.
The scorer estimates how likely the draft is to need a change before Russell sends it, from the aspects of uncertainty described in `send-confidence/uncertainty-aspects.md`: how well the writing rules cover it, how much of its content the ask left open, whether its facts are backed, what's at stake, and how familiar the recipient is.
A written draft is only measured: none is gated on its score, and no score reaches Russell in a reply - it only ever surfaces in the weekly rollup's trend.
A reaction is the one draft the model's verdict acts on (Reactions, below).
Everything here is fail-open: a failed judge call or a scorer exception never blocks staging or the review receipt.
What happens once he sends or discards the draft is `learn-from-send.md`.

## How the gate scores a draft

The gate logs every stage it lets through and scores any draft whose content has no score yet, in a background process, so the stage goes through immediately.
That process builds the whole context itself:

- **From the stage command:** the channel, recipient, thread, and account.
- **From the review receipt:** the final `writing-review` round's rule coverage rating and the spots the rules didn't settle.
- **From the session transcript:** the ask (every message Russell or the seed typed), the inputs (the tool results the session saw), and the count of his turns after the first.
- **From the send-confidence log:** familiarity, as the number of other sessions that staged a draft to the same recipient.

The scored row's draft id stays in the log, so the session records the outcome by the staged body file, per `learn-from-send.md`.
A script that sends from inside its own process, where no PreToolUse hook fires, hands its stage to the gate with `verify_gate.py stage <body-file> --meta <meta.json>`.
The meta file carries the channel, recipient, and thread, plus the script's own ask and inputs in place of a transcript; the command scores before returning, so the script can record the outcome right after.
The drainer's acknowledgement sweep does this for every ack.
A message that reaches Russell through no stage command has nothing for the gate to see, so it goes unscored and has no row for `learn-from-send.md`'s outcome step to record against.

## Reactions

A proposed Slack emoji reaction is a draft too, with message type `reaction` where every staged body has type `words`.
The drainer's `slack_reaction.py` stages it, since a reaction has no body file for the gate and no prose for a writing review (the drainer's Slack provider, REACTION-PROPOSAL, has the commands).
Its kind is `reaction`, set by the stager rather than judged from text, and the snapshot names the emoji and the message it goes on.
No judge runs, so the stage scores at once and the proposer gets the auto-send verdict back before it decides whether to ask Russell.
His approve or discard is the outcome: as proposed counts as sent as-is, a different emoji as a full edit.
A reaction the model sent on its own stays out of the fit, since Russell never ruled on it; undoing one records a discard.

## The model and its weekly refit

Each scored draft carries a `model` block, its score under the live coefficient file; `score` in `send-confidence/send_confidence_model.py` defines the block's fields and the auto-send test.
The coefficient file comes from the weekly refit, a plain-code job that fits the model on every resolved draft and ships a new version only when it does as well on the last four weeks' drafts as the live one:

```
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/refit.py [--dry-run] [--json]
```

The refit needs PyMC (`pip install pymc`); scoring reads the file with plain arithmetic and needs nothing beyond Python.
`refit.py`'s docstring holds the full run, and every run appends its record to `refit-runs.jsonl` in the send-confidence log directory for the weekly report.

## The session's part: rate coverage at mint

The one input only the session holds is the reviewer's rule coverage rating and uncertain spots, so they ride on the mint that already ends the review loop:

```
python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <body-file> --rule-coverage <1-5> --uncertain-spots-file <file>
```

(run the newest cached copy if several exist).
`--rule-coverage` is the final round's rule coverage rating, and `--uncertain-spots-file` holds its list of spots the rules didn't settle (write the list to a file first).
The gate reads both off the receipt when the draft is staged.
