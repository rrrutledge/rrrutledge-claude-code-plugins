# The aspects of uncertainty

Send-confidence estimates how likely a drafted message is to need a change before Russell sends it.
This file describes every aspect that makes a message uncertain, with a rating scale for each one that a model rates.
The scoring judge reads the whole file for every draft, and the writing reviewer reads its Rule coverage section.

Every aspect here describes the situation a draft is in, never whether the draft follows a writing rule.
Catching a broken rule is the reviewer's job, so a draft that breaks a clear rule is a review finding, not a sign of uncertainty.

A learning session (see `learn-from-send.md`) changes this file only by sharpening an aspect's description or anchors, never by adding a past case.
Nothing here names a person, an organization, or quotes a real message.

## Rule coverage

How completely and unambiguously the writing rules settle the wording, tone, and structure choices this text makes.
The writing reviewer rates it from the text alone, after reviewing the text against those rules.
Rate what the rules settle, not whether the text complies: a choice the rules clearly settle counts as settled even when the text got it wrong, because that is a finding.
When more than one rule bears on the same choice, it counts as settled only if it's also clear which one governs: two equally-applicable rules with nothing saying which takes priority leave the choice unsettled.

- 5 - every choice the text makes is settled by a clear rule.
- 4 - a minor choice falls where the rules are silent, and any reasonable reading lands in the same place.
- 3 - one choice that matters falls where the rules are silent or can be read more than one way.
- 2 - several choices that matter are unsettled.
- 1 - most of the text's choices are unsettled, as with a kind of message the rules don't address at all.

## Content latitude

How much of *what to say* the ask left for the writer to decide or find.
Latitude grows with every piece of content the writer had to supply itself: an answer or decision the draft makes on Russell's behalf, a fact or option it looked up rather than being handed in the ask or the inputs, anything it chose to add that the ask didn't call for, and which of several supplied facts it chose to keep or drop when the ask didn't say which ones mattered.

A long message covering several separate topics or asks also carries latitude: its structure, order, and which details to keep are all the writer's choices, and a complicated subject rates 4 or higher even when every fact is supplied.

- 1 - Russell handed over the actual wording.
- 2 - the intent and every piece of content are explicit; only the wording is open.
- 3 - the intent is clear, but some content had to be worked out, looked up, or chosen.
- 4 - a goal with several plausible approaches, or the draft decides something Russell hasn't decided.
- 5 - open-ended ("handle this").

## Fact support

Whether every factual claim in the draft is backed by the ask or the inputs.
A claim is a name, date, number, link, commitment, or statement of fact, and it is supported only when the ask or the inputs state it; a claim the draft inferred or invented is unsupported.
Check each claim's tense or status separately from the fact itself: stating a completed thing as upcoming, or a pending thing as done, is unsupported even when the underlying fact is backed.

The judge lists every claim and marks each supported or unsupported; the factor is the supported share.

## Stakes

How much rides on this message landing exactly right, which makes Russell read it closely whatever the rules say.
Stakes come from the situation, not the length: a two-line message can carry the highest stakes.
A message touching money with a sponsor, funder, or other partner stacks three signals at once (money, an outside party, a relationship that matters) and rates 5 however well the text reads.

The signals of stakes:
- money, or a financial or accounting explanation, above all to an outside party;
- Russell being evaluated, as in a job search or a role;
- a commitment put in writing;
- pushback, conflict, declining, or bad news;
- a recipient who is visibly upset or emotional;
- an outside party, where the message speaks for Russell or an organization he represents;
- a relationship that matters to him.

- 1 - routine logistics or an acknowledgement, with no signal present.
- 2 - a plain informational reply, where getting a detail slightly off would cost nothing.
- 3 - one signal present in a mild form, or light tact needed.
- 4 - one signal clearly present.
- 5 - several signals together, or one in its sharpest form.

## Familiarity

How established Russell's voice already is with this recipient.
Code measures it from his prior-sent count to the recipient, on a log curve that saturates at 20.

A high count assumes an established register, so it misses warmth risk: one especially warm or appreciative reply to a familiar recipient can still need a heavier rewrite than the count predicts.
