# Learn from the send

Every draft Russell sends or discards answers one question: what did the difference between the draft and what he actually sent teach us?
It has two possible answers, both read off the same diff:

- **The rules had a gap.** They were silent on a choice the draft had to make, or could be read more than one way, and his edit shows how he resolves it; the lesson fills that gap in `message-rules`, `authoring-rules`, or this skill's drafting loop.
- **The estimate misread the situation.** Send-confidence rated the draft likely to go out untouched when the situation was uncertain, or uncertain when it wasn't; the lesson sharpens an aspect's description in `send-confidence/uncertainty-aspects.md`.

Both come out of one diagnosis in one learning session and land in one PR.
The drafting session's part is short and mechanical; the learning session, spawned right away while the send is fresh, does the thinking.

## The drafting session's part

After Russell sends a draft (he edited it in the app and clicked Send, or said "sent" or "learn from that") or discards it outright:

1. **Read the actually-sent version from the source** - with a fresh tool call every time, from Jira via the API, Teams or Slack by reading the chat (`slack.js --show`), email from the sent item - and write its literal text to a file.
   Never reconstruct that text from anything the session already believes, remembers, or infers, even when a different version seems like the obviously correct one: a plausible guess recorded as the sent text fabricates the diff this whole procedure runs on.
2. **Record the outcome:**

   ```
   python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --sent-file <file>
   python ~/.claude/plugins/cache/*/document-authoring/*/send-confidence/send_confidence.py outcome --draft-id <id> --discarded --reason "<text>" [--moot]
   ```

   (run the newest cached copy if several exist).
   If the session no longer has the draft id handy, `--body-file <staged body>` finds it by rehashing that file against the log.
   The command labels each change itself, as `factual-correction`, `tone-voice`, or `structural-rewrite` (defined in its labelling prompt), and decides whether the send has anything to teach.
   If it prints that labelling failed, rerun it with `--edit-nature` naming the labels yourself.
3. **Run the spawn command it prints**, exactly as printed, and name the new session's short id in your reply so Russell can find it.
   No printed command means nothing to learn from this send.

**Record `--discarded` when Russell discards a staged draft outright**, with `--reason` naming why in one phrase - except in the replacement case below.

**Skip `--discarded` when you're about to score a replacement for the same thread and recipient - the facts moved, not the draft.**
Just build the context and mint the replacement: scoring it auto-supersedes the still-pending original, which correctly leaves the original unflagged.
Calling `--discarded` on the stale draft first flags it as over-confident by the same mechanical rule that catches a genuinely bad draft, spawning a learning session for a lesson that doesn't exist.

**Add `--moot` when Russell resolved the matter somewhere other than by sending this draft.**
There's no replacement to score, so `--discarded` is still the right call; `--moot` tells the command to skip the estimate check that would otherwise flag a well-scored draft as over-confident just because it never got the chance to go out.

## When a learning session spawns

The outcome command decides this in code:

- **The estimate was wrong, in either direction** - it was confident the draft would go out untouched and Russell edited or discarded it, or it expected an edit and he sent the draft as-is.
- **Russell made a voice edit** (`tone-voice` or `structural-rewrite`), even when the estimate was right: a low score on a draft that then got a voice edit means the estimate correctly sensed a gap in the rules, and that gap stays open for the next draft until it's filled.

Nothing spawns for a draft sent as-is that was expected to go out untouched, a factual correction the estimate saw coming, or a discard that was moot or superseded.

## The learning session

The session runs rooted in the plugins repo clone, from the brief the outcome command wrote: why it spawned, each labelled change, what the estimate thought (every factor, the judge's rationales and claims, and the reviewer's rule coverage with the spots it found the rules didn't settle), the draft and the sent text in full, and the diff.

1. **Read the brief, the draft, and the sent text.**
2. **Diagnose each change by asking which rule governed that choice:**
   - **A clear rule the draft broke** - the reviewer missed it, so the lesson is sharper rule wording a reviewer can't read past.
     There's no estimate lesson here (see `send-confidence/uncertainty-aspects.md` on what the aspects measure).
   - **The rules were silent or ambiguous on it** - a rule gap to fill.
     A change that lands on a spot the reviewer listed as unsettled is this case exactly.
     If the reviewer rated rule coverage high anyway, the Rule coverage description may need sharpening too.
   - **The rules were fine, but an aspect outside the wording called for care** - the stakes, what the ask left open, an unbacked fact.
     If the estimate rated that aspect as settled, sharpen that aspect's description.
   - **For an under-confident spawn** - name which aspect read as uncertain and why it turned out settled, and sharpen that aspect's description in the other direction.
3. **Voice facet - fill each rule gap.**
   Distill the underlying **rule**, not the transcript.
   It almost always belongs in `message-rules` (the message artifact rubric); a rule about *how you compose* rather than what lands belongs in `document-authoring`'s drafting loop instead.
   **Search the whole target file for overlap before writing a word of new text - this is the step most often skipped, and skipping it is what produces a duplicate bullet.**
   Grep the entire `message-rules` `SKILL.md` for the concept (register keywords, the behavior, near-synonyms), across **every** section and **every** persona block, not just the one persona the message you're learning from happens to match.
   The closest existing bullet is very often sitting in a sibling persona.
   Search `authoring-rules` too when the instinct is medium-independent.
   Fold the rule into whatever bullet that search turns up - expand its scope, sharpen its language, generalize it to a cross-persona Core-voice bullet if the instinct is universal, or add a sub-case - rather than adding a new one.
   A generic existing bullet that technically covers the edit still leaves the specific pattern unnamed enough to go unapplied, so fold in or sharpen a bullet even when a broader rule already brushes against the same case.
   Add a new top-level bullet **only** once that whole-file search has genuinely come up empty.
   **State the rule crisply as a single imperative bullet: a bold lead phrase plus one sentence, no before/after quote.**
   Add a short concrete pointer only when the rule is genuinely unclear without one, built from invented names and details; default to none.
   The goal is fewer, broader, crisper rules.
4. **Estimate facet - sharpen a description, never add a case.**
   When step 2 found an aspect whose description failed to recognize the situation, sharpen that aspect's definition or anchors in `uncertainty-aspects.md`, per that file's header.
   Most sessions need no edit here: a rule gap the voice facet fills raises rule coverage on the next similar draft by itself.
   Then run `python plugins/document-authoring/send-confidence/send_confidence.py weights-suggest`; when it proposes new weights, copy its `proposed` block into `weights.json`, keeping the thresholds as they are.
5. **Open one PR carrying both facets.**
   Branch from `main` in the clone, never editing the installed snapshot under `~/.claude/plugins/`, which is thrown away on the next plugin update.
   Before opening the PR, run the `writing-review` loop on every changed prose file - a fresh reviewer that reads the *whole* file, confirming the new text isn't already covered elsewhere and obeys the rules in step 3 - then mint each file's receipt, since `gh pr create` is gated on it.
   **This repo is public, so no real correspondence reaches it - not the files, the branch name, the commit messages, or the PR body.**
   The draft, the sent text, the diff, the recipient, and any name, amount, or org detail from the exchange stay in the local brief and this terminal session.
   The PR body describes each change as the pattern it captures, then states the brief's file name (its draft id) so Russell can trace the PR back to the send locally.
   Also state, for each voice rule, the overlap search's outcome ("folded into `<bullet>` in `<section>`" or "searched every section and persona - no overlap, new bullet"), and name which facets produced changes and why any expected facet produced none.
   A session whose diagnosis finds nothing to learn still reports that finding, and a session that finds the process itself at fault fixes the process instead.
6. **Report in one line** what was learned and changed, with the PR link.
   The terminal session is where Russell sees the draft and sent text side by side, so quote them here when the change needs them to make sense.

## Convergence

The goal is convergence on both facets: over time, Russell's edits should become information-only, and the estimate's confident drafts should go out untouched.
A send whose only differences are factual corrections the estimate saw coming spawns nothing, and that is the success signal.
