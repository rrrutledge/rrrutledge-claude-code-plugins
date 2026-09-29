# Send-confidence: scoring an outward draft

This is the procedure the drafting loop's Verify step points to for an outward message: mint with `--score-context` instead of a plain mint.
Every outward message that will have a sent version gets scored this way, on every surface - email, Slack, Teams, a Jira or GitHub comment, or anything else.
The scorer estimates how likely the draft is to need a change before Russell sends it, from the aspects of uncertainty described in `send-confidence/uncertainty-aspects.md`: how well the writing rules cover it, how much of its content the ask left open, whether its facts are backed, what's at stake, and how familiar the recipient is.
Stage 1 only measures: no draft is gated on its score, and no score reaches Russell in a reply - it only ever surfaces in the weekly rollup's trend.
Everything here is fail-open: a failed judge call or a scorer exception never blocks staging or the review receipt.
What happens once he sends or discards the draft is `learn-from-send.md`.

## Get the familiarity count

`sent_count` is a prior-sent count to this exact recipient, read before building the context below:

- **gmail**: `node gmail.js --search="to:<addr>" --folder=sent --top=20 --json` (add `--account=` for a non-default account), counting the results.
- **outlook-personal**: `node mail.js --search="to:<addr>" --folder=sent --top=20`, counting the results.
- **outlook-work, slack, teams, jira, github, linkedin, other**: no count source in Stage 1 - omit `--sent-count` below.

This lookup is the one manual step: because it calls another plugin's tool, the session runs it by hand.

## Build the context and mint

One command builds the context file and prints the exact mint command to run next - it also enforces the 12,000-character cap on `inputs`:

```
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py context \
  --channel <c> --recipient <r> --session-kind <k> \
  (--ask "<text>" | --ask-file <file>) (--inputs "<text>" | --inputs-file <file>) \
  [--account <a>] [--thread-ref <t>] [--iid <i>] [--turns-before-draft <n>] [--sent-count <n>] \
  [--rule-coverage <1-5>] [--uncertain-spots-file <file>]
```

(run the newest cached copy if several exist).

- **`--channel`** is one of `gmail`, `outlook-personal`, `outlook-work`, `slack`, `teams`, `jira`, `github`, `linkedin`, `other`.
- **`--ask`** is the originating ask, verbatim: the seed item's content, or Russell's own instruction in a live session.
- **`--inputs`** is the facts the draft may draw on: the thread, Russell's supplied facts, lookup results.
- **`--turns-before-draft`** counts Russell's turns between the ask and this mint - every human turn so far in a drainer worker, or the turns since he gave this particular ask in a live session. Defaults to `0`.
- **`--thread-ref`** is the Message-ID, Slack ts, or Teams chat id; **`--iid`** is the drainer item id, when one exists.
- **`--rule-coverage`** and **`--uncertain-spots-file`** carry the final `writing-review` round's rule coverage rating and its list of spots the rules didn't settle (write the list to a file first).
- Prefer `--ask-file`/`--inputs-file` (write them with the Write tool first) over the inline `--ask`/`--inputs` forms for anything beyond a short one-liner, the same way a staged body already goes through a file rather than a shell argument.

Run the mint command exactly as printed:

```
python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <body-file> --score-context <ctx.json>
```

This writes the receipt and, in the same step, scores and logs the draft.
It prints the draft id, never the score; keep the id for recording the outcome after the send.
