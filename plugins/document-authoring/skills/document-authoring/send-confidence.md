# Send-confidence: scoring an outward draft

This is the procedure the drafting loop's Verify step points to for an outward message: mint with `--score-context` instead of a plain mint, then record the outcome once Russell has acted on the draft.
The scorer estimates whether a draft will go out untouched, across five factors - familiarity with the recipient, how complex and how ambiguous the ask was, whether the draft's claims are backed by what Russell or the thread actually said, and how closely the wording matches his usual voice.
Stage 1 only measures: no draft is gated on its score, and no score reaches Russell in a reply - it only ever surfaces in the weekly rollup's trend.
Everything here is fail-open: a missing baseline, a failed judge call, or a scorer exception never blocks staging or the review receipt.

## Write the context file

Before minting, write a JSON context file next to the staged body (for example `.tmp/<slug>-score-ctx.json`):

```json
{
  "channel": "gmail",
  "account": "isc",
  "recipient": "someone@example.org",
  "thread_ref": "<Message-ID or Slack ts or Teams chat id>",
  "iid": "<drainer item id, or null in a live session>",
  "session_kind": "drainer-worker",
  "ask": "<the originating ask, verbatim: the seed item's content, or Russell's own instruction in a live session>",
  "inputs": "<facts the draft may draw on: the thread, Russell's supplied facts, lookup results; capped at 12,000 chars>",
  "turns_before_draft": 0,
  "sent_count": 5
}
```

- **`channel`** is one of `gmail`, `outlook-personal`, `outlook-work`, `slack`, `teams`, `linkedin`, `other`.
- **`turns_before_draft`** counts Russell's turns between the ask and this mint - every human turn so far in a drainer worker, or the turns since he gave this particular ask in a live session.
- **`sent_count`** is the familiarity proxy below, or `null` where the channel has no count source.

## Get the familiarity count

`sent_count` comes from a prior-sent count to this exact recipient, read before writing the context file:

- **gmail**: `node gmail.js --search="to:<addr>" --folder=sent --top=20 --json` (add `--account=` for a non-default account), counting the results.
- **outlook-personal**: `node mail.js --search="to:<addr>" --folder=sent --top=20`, counting the results.
- **outlook-work, slack, teams, linkedin, other**: `null` - Stage 1 has no count source for these channels.

## Mint with `--score-context`

Mint the review receipt exactly as `writing-review` describes, adding the context file:

```
python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <body-file> --score-context <ctx.json>
```

(run the newest cached copy if several exist).
This writes the receipt and, in the same step, scores and logs the draft.
It prints the draft id, never the score.

## Record the outcome

After Russell sends the draft (edited or as-is) or decides not to send it, record the outcome:

```
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --sent-file <file> --edit-nature <a,b>
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --discarded --reason "<text>"
```

If the session no longer has the draft id handy, `--body-file <staged body>` finds it by rehashing that file against the log.

**Classify the edit into exactly one of these four categories** - a finer breakdown of the Voice learning loop's own Information fix / Voice change split:

- **`factual-correction`** - the Voice loop's Information fix: a corrected fact, name, link, date, number, or scope detail.
- **`tone-voice`** - the Voice loop's Voice change, where phrasing, filler, or altitude shifted but the sentence structure stayed intact.
- **`structural-rewrite`** - the Voice loop's Voice change, where wording was reordered or reshaped.
- **`scope-recipient`** - the edit changed who the entire message is addressed to, or swapped out the entire subject it's about, rather than correcting one detail inside an otherwise-unchanged message; this sits outside the Voice loop's two buckets, since it redirects the message as a whole rather than fixing content within it.

**Record `--discarded` when Russell decides not to send a staged draft at all**, with `--reason` naming why in one phrase.

## Run the calibration dispatch

The outcome command prints a spawn command as its last line whenever the recorded outcome shows the draft was over-confident - the case a Stage 2 auto-send lane would need to avoid.
Run that command as printed, and name the new session's short id in your reply so Russell can find it.
No printed command means no dispatch is needed for this outcome.
