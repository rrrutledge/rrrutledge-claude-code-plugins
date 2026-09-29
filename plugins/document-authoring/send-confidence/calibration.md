# Send-confidence calibration notes

The diagnosis-calibration notes for send-confidence scoring, the scoring-judgment counterpart of `message-rules/SKILL.md`.
A calibration session folds a generalized pattern into the relevant section below after every over-confident draft; nothing here names a person, an organization, or quotes a real message.
The judge prompt reads only the first three sections; the last two collect observations toward a possible formula change.

## Phrasing complexity

Patterns in what makes a message read simpler, or more delicate, than it actually turned out to be.

- **Financial confirmations outrank their word count.**
  Rate a message confirming a financial or accounting root cause to an outside party above the "plain informational reply" band, even when it's a single plain acknowledgment and fact, stated directly.
  Putting a specific financial explanation in writing to an external party is itself the sensitive content, independent of how few words it takes to say.
- **A quietly-answered ask outranks a plain question.**
  When a draft has the quietly-answered-question problem described under Task ambiguity, rate it above the plain-question band; the offer is what makes it delicate.
- **A structurally-compressible recap reads simpler than it is.**
  A routine status update can still carry a promissory sentence about a later action that never gets revisited, or an avoidable paragraph split between two points on the same topic - both patterns `message-rules`' brevity and one-paragraph-per-topic rules already trim, independent of how plain the content is.
  Rate phrasing complexity above the plain band whenever the draft's own structure already carries one of these known-edit patterns.

## Task ambiguity

Patterns in what makes an ask read more settled, or more open-ended, than it actually turned out to be.

- **A quietly-answered question isn't settled.**
  A draft that offers or decides a specific option instead of asking for one has answered the question itself, even when the intent and topic were fully specified.
  Rate this a 3 or higher whenever the draft could be read as deciding the answer rather than requesting it.
- **Rate 1 only when Russell dictated the wording, not just the content.**
  A drainer-generated ask that specifies what to report still leaves the phrasing and structure open, including the known-edit patterns described under Phrasing complexity.
  That's a 2, not a 1; reserve 1 for an ask that hands over the actual sentence.

## Input completeness

Patterns in what makes a draft's supporting facts look sufficient when a claim was actually inferred or invented.

- **A claim's tense or status can be invented even when the underlying fact is supported.**
  Stating a completed fact as still in progress or upcoming invents a claim the inputs don't support, even though the same fact stated as already-done would be fully supported.
  Check a claim's tense/status wording against the inputs separately from checking the fact itself.

## Familiarity

Observations on whether the Sent-folder count proxy tracks how carefully a recipient's messages actually need to be read.

- **A high count misses warmth risk.**
  A familiar recipient's Sent-folder count assumes low tone risk, but one especially warm or appreciative reply can still need a heavier stylistic rewrite than the count predicts.
  Familiarity may track how carefully content needs review.

## Stylometric fidelity

Observations on whether the z-scored stylometric features track an edited voice.

- **The five features miss warmth and enthusiasm.**
  A flat, minimal acknowledgment and an exclamation-heavy, appreciative one can score identically across em-dash count, contraction rate, first-person rate, sentence-length SD, and MATTR.
  An exclamation-mark rate or a superlative-word rate could be a useful sixth feature.
