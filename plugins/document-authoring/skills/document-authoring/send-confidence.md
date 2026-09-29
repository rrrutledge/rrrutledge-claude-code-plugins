# Send-confidence: scoring an outward draft

This is the procedure the drafting loop's Verify step points to for an outward message: mint with `--score-context` instead of a plain mint, then record the outcome once Russell has acted on the draft.
The scorer estimates whether a draft will go out untouched, across five factors - familiarity with the recipient, how complex and how ambiguous the ask was, whether the draft's claims are backed by what Russell or the thread actually said, and how closely the wording matches his usual voice.
Stage 1 only measures: no draft is gated on its score, and no score reaches Russell in a reply - it only ever surfaces in the weekly rollup's trend.
Everything here is fail-open: a missing baseline, a failed judge call, or a scorer exception never blocks staging or the review receipt.

## Get the familiarity count

`sent_count` is a prior-sent count to this exact recipient, read before building the context below:

- **gmail**: `node gmail.js --search="to:<addr>" --folder=sent --top=20 --json` (add `--account=` for a non-default account), counting the results.
- **outlook-personal**: `node mail.js --search="to:<addr>" --folder=sent --top=20`, counting the results.
- **outlook-work, slack, teams, linkedin, other**: no count source in Stage 1 - omit `--sent-count` below.

This lookup is the one manual step: because it calls another plugin's tool, the session runs it by hand.

## Build the context and mint

One command builds the context file and prints the exact mint command to run next - it also enforces the 12,000-character cap on `inputs`:

```
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py context \
  --channel <c> --recipient <r> --session-kind <k> \
  (--ask "<text>" | --ask-file <file>) (--inputs "<text>" | --inputs-file <file>) \
  [--account <a>] [--thread-ref <t>] [--iid <i>] [--turns-before-draft <n>] [--sent-count <n>]
```

(run the newest cached copy if several exist).

- **`--channel`** is one of `gmail`, `outlook-personal`, `outlook-work`, `slack`, `teams`, `linkedin`, `other`.
- **`--ask`** is the originating ask, verbatim: the seed item's content, or Russell's own instruction in a live session.
- **`--inputs`** is the facts the draft may draw on: the thread, Russell's supplied facts, lookup results.
- **`--turns-before-draft`** counts Russell's turns between the ask and this mint - every human turn so far in a drainer worker, or the turns since he gave this particular ask in a live session. Defaults to `0`.
- **`--thread-ref`** is the Message-ID, Slack ts, or Teams chat id; **`--iid`** is the drainer item id, when one exists.
- Prefer `--ask-file`/`--inputs-file` (write them with the Write tool first) over the inline `--ask`/`--inputs` forms for anything beyond a short one-liner, the same way a staged body already goes through a file rather than a shell argument.

Run the mint command exactly as printed:

```
python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <body-file> --score-context <ctx.json>
```

This writes the receipt and, in the same step, scores and logs the draft.
It prints the draft id, never the score.

## Record the outcome

After Russell sends the draft (edited or as-is) or discards it outright, record the outcome (the one exception is below):

```
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --sent-file <file> --edit-nature <a,b>
python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --discarded --reason "<text>" [--moot]
```

If the session no longer has the draft id handy, `--body-file <staged body>` finds it by rehashing that file against the log.

**Classify the edit into exactly one of these four categories** - a finer breakdown of the Voice learning loop's own Information fix / Voice change split:

- **`factual-correction`** - the Voice loop's Information fix: a corrected fact, name, link, date, number, or scope detail.
- **`tone-voice`** - the Voice loop's Voice change, where phrasing, filler, or altitude shifted but the sentence structure stayed intact.
- **`structural-rewrite`** - the Voice loop's Voice change, where wording was reordered or reshaped.
- **`scope-recipient`** - the edit changed who the entire message is addressed to, or swapped out the entire subject it's about, rather than correcting one detail inside an otherwise-unchanged message; this sits outside the Voice loop's two buckets, since it redirects the message as a whole rather than fixing content within it.

**Record `--discarded` when Russell discards a staged draft outright** (unless a replacement for the same thread and recipient is about to be scored - see below), with `--reason` naming why in one phrase.

**Skip `--discarded` when you're about to score a replacement for the same thread and recipient - the facts moved, not the draft.**
Just build context and mint the replacement: scoring it auto-supersedes the still-pending original, which correctly leaves the original not flagged as over-confident.
Calling `--discarded` on the stale draft first flags it as over-confident by the same mechanical rule that catches a genuinely bad draft, triggering a calibration session for a phrasing or ambiguity lesson that doesn't exist.

**Add `--moot` when Russell resolved the matter somewhere other than by sending this draft.**
There's no replacement to score here, so the supersede path above doesn't apply and `--discarded` is still the right call; `--moot` tells the outcome command to skip the factor/composite check that would otherwise flag a well-scored draft as over-confident just because it never got the chance to go out.

## Run the calibration dispatch

The outcome command prints a spawn command as its last line whenever the recorded outcome shows the draft was over-confident - the case a Stage 2 auto-send lane would need to avoid.
Run that command as printed, and name the new session's short id in your reply so Russell can find it.
No printed command means no dispatch is needed for this outcome.

## The calibration session's own PR

The spawned session works from the brief `_write_brief` wrote - the judge rationales, the claims, the implicated factors, and the draft-to-sent diff.
Fold the generalized pattern into `calibration.md` per that file's own header instructions, run `weights-suggest`, and run the `writing-review` loop against `authoring-rules` before opening the PR.

**A calibration session also runs the Voice learning loop when the edit warrants it.**
The brief's `edit_nature` line carries the same Information-fix/Voice-change classification `document-authoring`'s Voice learning loop's own classify step assigns: `tone-voice` and `structural-rewrite` are a Voice change and warrant the loop below.
A Voice change label triggers the loop's step 3 on the same draft-to-sent diff, then step 4 to open the PR - in this same PR, alongside the `calibration.md` diagnosis.

**Quote the original draft and the sent text verbatim in the PR body.**
Russell runs many sessions in parallel and can lose track of which item a PR came from within minutes, and the local brief and draft files that made the diagnosis live outside the repo - they won't be there when he looks, so the PR is the only surviving record of what actually happened.
