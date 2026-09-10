---
name: writing-review
description: Review authored prose against Russell's authoring rules - code comments, docstrings, SKILL.md and skill prose, READMEs, repo docs, config prose, Confluence pages, email, Teams and Slack messages. Use after writing or editing any of these, before handing the work over, and when asked to check a diff or document for voice and style compliance.
---

# Writing review

Runs prose through review-and-revise rounds against the shared rubric in `authoring-rules`, until the writing satisfies both the writer and the reviewer.
This is the counterpart to code review: same posture, different rubric.

## Why this exists as a separate agent

A writer holds two things at once - the rule and the goal it conflicts with.
The goal is active and the rule is passive, so under pressure the goal wins.
A docstring author working on "explain why this script exists" writes "in place of the snippets it used to author fresh into `.tmp/`" while holding a rule against exactly that, because the rule is not what they are thinking about.

A reviewer has no competing goal.
Reviewing is the whole job, so there is nothing for the rule to lose to.
This is the same reason code review catches what the author read past.

**Dispatch the reviewer as a subagent, always.**
Running the check inline, in the session that wrote the prose, reproduces the exact blindness the review exists to correct.
The reviewer must come to the text cold.

## When to run it

This is the **Review** step of `writing-flow`, the cold pass every piece of writing runs before it stages or ships.

- After authoring or editing any shipped prose, before handing the work to Russell.
- On a diff, when asked to check a branch or PR.
- On a single document, when asked to check it directly.

## How to run it

1. Collect the text under review.
   For a diff: `git diff <base>..<head>` restricted to prose-bearing files.
   For a document: the file itself.
2. Skip `.tmp/`.
   Plans, specs, handoffs, and staged commit messages live there, and they are change-explanations rather than shipped artifacts.
   Different rules apply to them, so reviewing them against this rubric produces false findings.
3. Dispatch a subagent using `reviewer-prompt.md`, giving it the text and the rubric path.
4. Revise against the findings you accept, then dispatch a **fresh** reviewer on the revised text.
   A reviewer that has already seen an earlier draft reads its own prior findings rather than the words in front of it.
5. Repeat from step 3 until the reviewer returns clean, a finding stands that you genuinely disagree with, or you have run three rounds.
6. Mint the review receipt on the exact file the gate will check, so it lets the work through:
   `python ~/.claude/plugins/cache/*/document-authoring/*/hooks/verify_gate.py mint <file>` (run the newest if several are cached).
   For an outward message, mint the body file the stage command passes as `--body-file` / `--json`.
   For shipped prose, mint each changed markdown file the PR will carry, so `gh pr create` passes.
   Mint after the loop above has converged on the text you're keeping; any later edit to that file needs a fresh review and a fresh mint, since the receipt binds to the file's content.

**The loop finishes before Russell sees anything.**
He gets the work, not the findings, and not the rounds it took.

What reaches him depends on how the loop ended:
- **Clean** - the work alone.
- **Ended on a disagreement** - the work, plus one line naming the finding and why you rejected it.
- **Three rounds without converging** - the work, plus what is still contested. Text that will not converge usually means the rule and the writing are both defensible, and that is worth his attention.

## The stage gate

The Verify step is contractual, not advisory: a PreToolUse hook (`hooks/verify_gate.py` in this plugin) blocks the moment a piece of writing would reach Russell until a fresh receipt exists for that exact content.
It guards the two surfaces that reach him - an outward message when a stage command puts it where he sends it, and shipped prose when a PR opens for him to read on the Files Changed tab.
The receipt attests that a review ran against this exact text; it never judges the verdict, so the standing-to-disagree rules below still hold - text you reviewed and chose to keep over a finding mints the same way a clean one does.

**Outward messages.**
The gate blocks the mail-staging commands until a receipt exists for the body file's content.
It covers personal Gmail and personal Outlook (`--reply` / `--draft-new` / `--send-self`, keyed on `--body-file`), work Outlook (`create-draft` / `create-reply`, keyed on `--json`), and the Teams and Slack browser composers (a browser-chauffeur `--cdp-port` run that declares its body via `--body-file`), and it denies the native Gmail connector's compose and send verbs with a redirect to the `gmail` skill's `gmail.js`.
A body with no letters (a bare emoji reaction) carries no prose and is exempt.
The Teams and Slack composers type into a web page rather than a command argument, so `message-draft` routes their body through a reviewed `.tmp` file and drives the composer with `--body-file` on the browser-chauffeur run, which is how the gate reaches them; a composer run that declares no body file is a screenshot or a scrape, not a stage, and passes straight through.

**Shipped prose.**
A skill, a README, or a repo doc reaches Russell when a PR opens, so the gate fires on `gh pr create` - the moment the prose lands on the Files Changed tab (a draft PR counts; Russell reviews drafts there).
It does not fire on `gh pr ready` or `gh pr merge`, which act on an already-open PR whose prose he has already seen - those are the time-to-merge step, not a fresh prose-reaches-Russell moment.
The hook computes the PR diff itself and blocks unless every changed markdown file that ships has a fresh receipt for its current content.
Markdown under `.tmp/` and a top-level `handoffs/` is exempt - those are change-explanations, not shipped artifacts - and a code-only PR passes straight through.
Mint each changed prose file after its review, so `gh pr create` lets the PR through; the voice-learning loop's independent-reviewer step already runs the review, and mints there so it composes with this gate rather than fighting it.

When a stage or PR is blocked, the block message names the exact file(s) and the mint command to run, so the path through the gate is always one command away.

## Standing to disagree

You may reject a finding, and sometimes you should: the rule can be tighter than Russell's actual practice, or keyed on a condition that does not fit this text.

Rewriting to satisfy a finding you believe is wrong is the worse failure.
It degrades the text and it hides the fact that a rule needs adjusting, so the same bad finding returns forever.

The guard runs the other way too.
Revising is work, and "the reviewer is wrong" is the cheapest way out of it.
Before rejecting, state which of the two applies - the rule is tighter than practice, or its condition does not fit here - in one sentence.
A rejection you cannot put in those terms is avoidance, so revise instead.

## Learning from rejected findings

A rejected finding says the rule is tighter than practice or its condition is too narrow.
Both are worth folding back into the rule, but the two sources carry different weight.

**Russell rejects one** - strong signal, act on it. Distil which of the two it was and fold it into the rule as a PR against this repo.

**The writer rejects one** - weak signal on its own, because the writer is the party being criticized and has an interest in the finding being wrong. Worth acting on when the same rejection recurs across independent sessions, which no single session can see. Note it in the PR that carries the work, so the pattern is visible later.

`document-authoring`'s voice-learning loop covers outward messages, where a draft-versus-sent diff exists.
Shipped artifacts have no such diff, so a rejected finding is the only correction signal they get.

The goal is convergence, and the success signal is the same shape: over time, the loop should end clean on the first round.

## Scope

`authoring-rules` is the rubric for anything that ships.
When the prose is an outward message, the reviewer loads `message-rules` for its message-specific rules as well, and `reviewer-prompt.md` tells it how.

Rules belong in those two files, never in the prompt and never here.
A rule stated in more than one place drifts, and the copy the reviewer reads is the one that goes stale.
