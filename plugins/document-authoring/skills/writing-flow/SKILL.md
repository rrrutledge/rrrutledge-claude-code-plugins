---
name: writing-flow
description: The mandatory flow every piece of writing in Russell's name follows - draft, then a cold independent review, then stage or ship. Load this first whenever authoring or editing ANY prose - an email, a Slack or Teams message, a Jira or GitHub comment, a skill or repo doc, a README, a PR body, a code comment. It is the map; it routes to the detail skills for each step.
---

# The writing flow

Every piece of writing in Russell's name runs through the same three steps, in order.
This document is the map; the detail of each step lives in the skills it points to, so the flow itself stays short enough to hold in full.
The middle step is the one that gets dropped under pressure, so carry all three from the start - opening the detail skills is not a substitute for running the flow.

## The three steps

1. **Draft.**
   Compose against the rules for what you're writing.
   The shared rubric for anything that ships is `authoring-rules`; an outward message also takes the register, warmth, persona, link, and ask rules in `message-rules`.

2. **Review.**
   Dispatch `writing-review` as a fresh subagent that did not write the text, and revise against what it finds.
   This is a cold, independent pass, never a re-read of your own draft in the session that wrote it (for why a separate cold reviewer is required, see `writing-review`).
   It is mandatory: a draft reaches Russell only after the review has run.

3. **Stage or ship.**
   How the reviewed text lands depends on what it is - see the two shapes below.

## Two shapes of the same flow

- **An outward message** - email, Slack, Teams, a Jira or GitHub comment - ends in **Stage**: put the reviewed text where it will be sent and leave it there, and Russell sends it himself.
  Then **Learn**: after he sends, diff his sent version against your draft and fold any voice change back into `message-rules` (the process for doing so is `document-authoring`'s voice-learning loop).
  The message-shaped flow, step by step with its persona and staging detail, is `document-authoring`'s drafting loop.

- **Shipped prose** - a skill, a README, a repo doc, a code comment, config prose - ends in **Ship**: commit it and open a PR.
  There is no send and no Learn diff here, so a review finding you accept or reject is the only correction signal the text ever gets; `writing-review` covers how to weigh one.

## What enforces the Review step

A stage gate blocks the moment the writing would reach Russell until the review has run against that exact content (see `writing-review`'s **The stage gate**).
For an outward message that moment is the stage command; for shipped markdown - a skill, a README, a repo doc - it is the PR-opening command.
Prose the gate can't reach - a code comment, config prose, markdown it exempts - still runs the Review step every time, and this flow is what makes it run there.
A draft that skips it reads as generic in the places Russell's voice should carry, which is the whole failure the flow exists to prevent.
