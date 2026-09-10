---
name: authoring-rules
description: Russell's medium-independent rules for any text that ships - code comments, docstrings, SKILL.md and other skill prose, READMEs, repo docs, config prose, PR bodies, commit messages, Confluence pages, email, Teams and Slack messages. Load when authoring or editing any of these, and when reviewing authored text against the rules.
---

# Authoring rules (shared rubric)

Every rule here is an **artifact rule**: you can tell whether it was followed by reading the finished text alone.
That is what makes this set shared.
The writer loads it to compose against; a reviewer loads the same file to check against, so the two can never drift.
These rules are the Draft-step rubric of the three-step flow in `writing-flow` (draft, review, then stage or ship).

Each rule states the behavior, then a **Check** giving the surface forms that usually indicate it was broken, and where those forms are innocent.
The forms are evidence, not the rule: one appearing is not automatically a violation, and a violation that uses none of them is still a violation.

Rules that constrain *how you get there* rather than what lands (draft then revisit, verify line by line, run the overlap search before adding a bullet) are writer-only and live in `document-authoring`.
A reviewer holding only the output cannot evaluate them.

Medium-specific rules - register, warmth, asks, links, personas, emoji, sign-offs, nudges - live in `message-rules`, the companion rubric.
They are reviewable, but only for outward messages, so a reviewer loads `message-rules` alongside this file for a message and this file alone for anything else that ships.

---

## The rules

**Present tense in shipped artifacts; before/after only in change-explanations.**
Where the text lands decides which applies, not how useful the history feels.
- *Ships and stays* (docs, `SKILL.md`, README, code comments, docstrings, config prose): every sentence answers "how does this work today".
- *Explains a change* (chat with Russell, a PR body, a commit message): before/after is the point.

Rationale is in scope; provenance is not.
"Deletes the entry so a later run doesn't re-litigate it" is a why with no history in it.
This bars a second kind of provenance too: the artifact narrating **its own earlier drafts**. The reader has never seen a prior version, so "an earlier draft summed these to $11,000" or "this section previously said" or "now corrected to" describes something invisible to them. A correction lands as the current fact, stated once, as if the text had always read that way - not as a diff against a draft only the author saw.
**Check:** does the sentence parse without knowing a previous state - of the system, or of this document? Watch for "used to", "no longer", "instead of the old", "we tried X", "this replaces", "formerly", "previously", "by hand", "what X did before", and the self-referential forms "an earlier/previous draft", "an earlier version", "this used to say", "now corrected/fixed/updated", "we've since revised".
Innocent: "used to" as passive voice, where *used* means employed - "the key used to sign the token", "a script used to validate input". The test is whether the sentence needs a previous state *of the system or of the document itself* to parse.

**State guidance positively.**
Describe the desired behavior directly; a correction names only what to do, not the rejected alternative alongside it.
Reserve a negative for a real, tempting failure mode a positive instruction won't prevent on its own, and write it as a single standalone guardrail.
A single-valued fact forecloses its alternatives the moment it's named - stating which branch a PR targets already rules out every other branch, so state it once and let it stand alone.
**Check:** a "don't do X, do Y" couplet where "do Y" alone would carry it; a definition that says what something is for and then appends what it is not for ("A is for X; not for Y", "goes on X instead, not here"), where the positive half stands alone; a prohibition where a recipe would bind better.

**Frame in the positive affirmative.**
Describe a subject by what it is and what it did, not by what it lacked or fell short of - "I had to learn it" carries what "I was behind" only concedes, and "it spread because it worked" says positively what "a thing that doesn't work dies" states as the absence of failure.
The affirmative names the action taken or the fact established; the deficit framing describes the same reality as a shortfall, which reads smaller and pulls the eye to what was missing.
**Check:** a sentence built on a lack or a shortfall - "was behind", "didn't know", "never managed to", "failed to", "couldn't", "a thing that doesn't work" - where the same point states as an action taken or a fact established.
Innocent: a scar deliberately anchoring a posture, and an honest limitation or gap stated plainly, where recasting it positively would misrepresent it (a cover letter's owned domain-gap, a risk called out to leadership).

**Make the general statement carry it; a wanted example means it's too weak.**
When writing guidance meant to be reasoned from - a classifier rubric, a SKILL.md, an agent prompt, a review checklist, any doc - state the general condition that decides new cases, strongly enough to stand on its own.
Reaching for an example to make the point land is the signal the general statement is too weak: sharpen the statement until it needs none, rather than illustrating it.
The general condition is what catches the next case; a named example only ever covers the one that already came up.
A real, named organization, repo, or person carries this cost even when the general statement is already strong enough to stand alone without any example: the reader applying the rule later brings full runtime judgment to whatever situation is actually in front of them, so naming an actual instance still narrows the rule to the one case that prompted it and goes stale the moment that instance's own practice changes - illustrate with the category of case.
**Check:** an illustrative example offered to clarify or ground a rule - "e.g. X", "such as A, B, C", "for example …", a named sample case - standing in for a stronger general statement; a real, named organization, repo, or person embedded as illustration, present even when the general statement is otherwise strong.
Innocent: the Check lines' own surface-form evidence, which is a reviewer's detection cue rather than guidance being illustrated; a genuinely closed, finite set with no further members possible; and a change-explanation (see above), where naming the actual instance is what lets the reader evaluate the specific decision in front of them.

**No helper tail.**
Stop the moment the point is made.
Treat any sentence after the main point as guilty until proven necessary: cut reassurance, offers, hedges, restated context, and invitations to react.
**Check:** does this sentence hand the reader something they don't already have? Watch for a close starting "happy to", "let me know if you'd like", "if you'd rather", "hope this helps", "feel free to"; a final paragraph restating what was already said; unrequested reassurance.
Innocent: a genuinely open decision, asked as one direct question and stopped there.

**One concept, one canonical place; everywhere else points to it.**
A concept gets a single home that states it in full; other sections of the same document, and closely-linked docs, reference that home rather than restating it.
When you name a canonical source - a registry, a config file, another doc - let the pointer carry it; don't also summarize the content you delegated there.
When a concept the document already covers is asserted again somewhere else - a second full explanation, or a partial re-listing that states the same facts in its own words - unify the two and have one cross-reference the other, so a later change touches one place instead of leaving copies that drift apart.
The test is whether the second mention can drift from the first: a pointer or a recap that defers to the canonical spot can't, an independent restatement can.
**Check:** the same concept asserted independently in two sections, or across two closely-linked docs, with neither deferring to the other - including two lists or definitions of the same thing whose wording already differs; or a sentence naming a source as authoritative for X ("see Y for Z", "Y is the source of truth for Z") followed by an inline restatement of that same X.
Innocent: a brief cross-reference or a one-line recap that points to the canonical explanation rather than re-deriving it; naming in a few words what a source covers; quoting one specific value the reader needs right now while pointing to the source for the rest.

**No em dash.**
Use a spaced hyphen ` - ` instead.
**Check:** the character U+2014 anywhere in authored text.
Innocent: text quoted verbatim from someone else, where changing it would misquote them.

**Anchor every link on descriptive text.**
Write "see the [incident report](URL)", never a bare `https://…` in prose.
**Check:** a bare URL outside a code block.

**Name the specific thing, not the category.**
"token cost" not "cost", "the deploy" not "it".
**Check:** can you point at what each noun refers to without reading around it?

**Say it plainly.**
**Check:** staged oppositions - "it's not just X, it's Y", a wrong or easy path set up only to reject it for the right one, or two balanced clauses held in tension; rule-of-three flourishes; vivid metaphors and set-phrase idioms; an amplifier stacked on a noun where the plain noun would do ("great", "such an important") - reserve "great" for a standalone exclamation or genuine singular praise.
Sentence length and rhythm are drafting judgment rather than a decidable check, so they stay in `document-authoring`.

**State facts without editorializing.**
**Check:** cheerful labels ("the good news is", "you're all set"); unsolicited reassurance; hedging something already confirmed ("looks like", "turns out").

**Bold lead-ins on bullets.**
Start each bullet with a bold 4-7 word key phrase, then the detail.
Put an intro sentence above a list, with a blank line before the list.

**Plain words over corporate and AI filler.**
**Check:** "leverage" as a verb, "streamline", "as per", "kindly", "delve", "furthermore", "moreover", "I wanted to reach out", "I hope this message finds you well", "please don't hesitate to", "I'm excited to share", "honestly" as a hedge opener.

**Semantic line breaks in markdown source.**
Within a paragraph or a multi-sentence bullet, start each sentence on a new line.
Rendered output is identical, but a one-sentence edit then touches one line instead of marking the whole paragraph changed in the diff.
**Check:** markdown source only. Never applies to a message composed in a web UI, where there is no source to diff.

**Never reference coffee, alcohol, or drinks.**
Not Russell's own, and not as a suggestion to someone else - pick a neutral alternative or omit.
**Check:** coffee, alcohol, beer, wine, cocktails, or "grabbing a drink" named as Russell's activity or offered to another person.

---

## Where the rules pull against each other

These tensions are real, not drafting mistakes.
Each is resolved by a stated boundary rather than by judgment in the moment, so the writer isn't left arbitrating mid-sentence.

| Tension | Boundary that resolves it |
|---|---|
| "Explain the why" vs present-tense-only | Rationale (why it behaves this way now) is in scope. Provenance (what it replaced) is not. |
| "Be useful" vs no helper tail | Usefulness goes in the substance. A closing offer adds nothing the reader doesn't have. |
| "Be precise about the failure" vs state guidance positively | A negative is allowed when the failure mode is real and tempting, and then only as one standalone guardrail. |
| Concrete examples aid clarity vs make the general statement carry it | Strengthen the general statement until it needs no example; a wanted example is the signal it's still too weak. The Check lines' surface-form evidence is a reviewer's detection cue, not an example illustrating guidance. |

When a rule and an active writing goal pull opposite ways, the goal usually wins by default, because the goal is what you're holding in mind while composing.
That is the failure this rubric exists to make checkable, and the reason a reviewer reading cold catches what the author stared past.
