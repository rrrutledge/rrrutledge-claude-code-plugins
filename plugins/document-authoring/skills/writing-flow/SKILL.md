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
   Load the rubric before writing the first line, a one-line edit to an existing doc included, so the rules are in context at the moment each sentence forms.
   After a removal, re-read every touched file and every doc that names the removed thing against the removal test in `authoring-rules`' present-tense rule.

2. **Review.**
   Dispatch `writing-review` as a fresh subagent that did not write the text, and revise against what it finds.
   This is a cold, independent pass, never a re-read of your own draft in the session that wrote it (for why a separate cold reviewer is required, see `writing-review`).
   It is mandatory: a draft reaches Russell only after the review has run.

3. **Stage or ship.**
   How the reviewed text lands depends on what it is - see the two shapes below.

## Two shapes of the same flow

- **An outward message** - email, Slack, Teams, a Jira or GitHub comment - ends in **Stage**: put the reviewed text where it will be sent and leave it there, and Russell sends it himself.
  Staging follows the review immediately and precedes showing the text: the reply shows the staged draft and where it lives, and the only question for Russell is whether to send.
  **A draft that carries a file shows Russell the file in that same reply.**
  When the staged draft carries any attachment or inline image, the reply that presents it also sends each of those files with the `SendUserFile` tool, so he judges what the recipient will see from whatever device he reads the session on.
  The Gmail, personal Outlook, and Slack stage commands print one `attachment: <absolute path>` line per file they staged; on any other staging path, the files are the ones the session attached.
  The tool renders images, PDFs, HTML, and SVG inline, and those files go as they are.
  A Word document, slide deck, or other file a phone can't render goes as a PDF or PNG export of it, and the draft keeps the original file.
  One `SendUserFile` call carries the whole set.
  `SendUserFile` is a deferred tool, so load it with `ToolSearch("select:SendUserFile")` before the first call.
  It sends files from the local disk, and the session offers it while Russell can reach the session from another device; a session without it lists each file's absolute path beside the draft.
  Then **Learn**: after he sends, record how his sent version differs from your draft, and a learning session folds any lesson back into the rules (the process for doing so is `document-authoring`'s `learn-from-send.md`).
  The message-shaped flow, step by step with its persona and staging detail, is `document-authoring`'s drafting loop.

- **Shipped prose** - a skill, a README, a repo doc, a code comment, config prose - ends in **Ship**: commit it and open a PR.
  There is no send and no Learn diff here, so a review finding you accept or reject is the only correction signal the text ever gets; `writing-review` covers how to weigh one.

## What enforces the Review step

A stage gate blocks the moment the writing would reach Russell until the review has run against that exact content (see `writing-review`'s **The stage gate**).
For an outward message that moment is the stage command; for shipped markdown - a skill, a README, a repo doc - it is the PR-opening command.
Prose the gate can't reach - a code comment, config prose, markdown it exempts - still runs the Review step every time, and this flow is what makes it run there.
A draft that skips it reads as generic in the places Russell's voice should carry, which is the whole failure the flow exists to prevent.
