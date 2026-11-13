# Keep every skill single-source as it changes

## The question

`apply-for-job` had 42 rules written in two or more places and 7 places where the copies already contradicted each other, and [PR 589](https://github.com/rrrutledge/personal-ai-pod/pull/589) refactored that one skill so each rule has one home.
This plan answers the general question: what stops the next skill from drifting the same way as it is created and edited.

**Short answer: the rule exists, but nothing in the flow makes the writer or the reviewer look across files.**
`authoring-rules` already has "One concept, one canonical place".
The fix is a writer step that finds a rule's home before writing it, a reviewer that searches the whole skill for every rule a change touches, and a sharper Check that names the skill-shaped forms of duplication.
The existing duplicates in drainer, browser-chauffeur, and blog-post then get one refactor each, since the new review only reports on what a change adds.

## Is the pattern bigger than one skill?

Yes.
A survey of four other large skills found duplication in all of them, and live contradictions in three.

| Skill | Rules in 2+ places | Live contradictions | Example contradiction |
|---|---|---|---|
| drainer | ~15 | 5 | `SKILL.md:30` says triage has "only three" buckets; `engine/triage.md:20` says "one of four buckets" |
| browser-chauffeur | ~20 | 5 | `SKILL.md:349` says "Wait briefly (500ms-1s)"; `SKILL.md:611` bans every fixed delay |
| blog-post | ~6 | 1 | lines 72-75 say regenerating the page destroys hand-edits; lines 103-105 say it picks them up |
| isc-ed-report | ~6 | 0 | none |

isc-ed-report stays consistent because it has one numbered flow and a closing checklist that points back to the steps instead of restating them.
drainer's `providers/email-base.md` is the other shape that holds: a named home for rules shared by several providers, which each provider links to.

## Why it happens

The handoff named three likely causes.
Checked against the current sources, two hold as stated, one holds only in part, and the survey adds a fourth.

1. **The skill describes its order in several views (confirmed, and the strongest cause).**
   Every contradiction in drainer and browser-chauffeur is a summary or second view that went stale after the canonical file changed.
   The worst spots are files that say they don't restate a thing and then restate it: drainer `SKILL.md:29`, drainer `architecture.md:71`, and browser-chauffeur `script-quality-standards.md:47`.
2. **No step sends the writer to the rule's existing home (confirmed).**
   `authoring-rules/SKILL.md:16` says an "overlap search before adding a bullet" is a writer rule living in `writing-flow` and `document-authoring`.
   Neither file has one.
   The only overlap search is `learn-from-send.md:72-78`, and it covers one target file, `message-rules`.
   The survey shows the result: blog-post's HARD RULE block and browser-chauffeur's subagent section are later insertions that restate older rules a few lines away.
3. **The reviewer misses the untouched copies (partly true).**
   `reviewer-prompt.md:37-45` already has the reviewer read each touched file in full, skim the docs it closely links to, and apply "One concept, one canonical place" across them.
   What it misses is a file the change doesn't touch and the touched file doesn't link to.
   drainer's stale "only three" summary sits in `SKILL.md`, and a change to the buckets in `triage.md` neither touches `SKILL.md` nor is linked from it in the diff.
   `writing-review`'s step 1 hands the reviewer the diff and the script paths, with no list of the skill's other files.
4. **An absolute rule outlives the exception that scoped it (new).**
   drainer `SKILL.md:41` says never send, while `worker-core.md:17` allows a send on Russell's explicit per-message say-so.
   The exception was written in its own file and the absolute statement was never revisited.
   This is the drift direction "One concept, one canonical place" already names, with the canonical spot split across two files.

## Recommendation

Four changes, all in `document-authoring`, in one PR.

### 1. Writer: find the home before writing a rule

Add to `writing-flow`'s Draft step:

> Before adding or changing a rule or step in a skill, search the skill's folder and every file it names for the concept, under its keywords and near-synonyms.
> Fold the change into the home that search finds.
> A rule that several steps apply and that has no home yet gets one, a section or file the steps link to.
> When the change alters a rule, edit every other mention the search turned up to point at the home.

This makes the pointer at `authoring-rules/SKILL.md:16` true.
`learn-from-send.md` step 3 keeps its message-rules detail (search every persona block) and links this step for the general search.

### 2. Reviewer: search the whole skill for every rule a change touches

Change `writing-review` step 1: when the diff touches skill prose, also collect the paths of every markdown file in the skill's folder and every file the skill names outside it (an initiative doc, a registry).

Change `reviewer-prompt.md` to take those paths as `[SKILL_FILES]`, and replace "skim any doc it closely links to" with:

> For each rule the diff adds or changes, search every file in [SKILL_FILES] for it and read each file that mentions it.

Findings stay anchored to the change: a new rule that restates one elsewhere, or a changed rule whose old copy elsewhere now contradicts it.
Pre-existing duplicates the change didn't cause stay out of scope, as now.

The reviewer searches instead of reading the whole skill because the skills are large: drainer's markdown is about 320 KB (roughly 80K tokens), apply-for-job about 155 KB.
A search per changed rule costs a few file reads, and it catches the drainer case, where the stale copy was in a file the change never opened.

### 3. Rubric: name the skill-shaped forms in the Check

Extend "One concept, one canonical place" in `authoring-rules`:

- **Scope.** For skill prose, "closely-linked docs" means every file in the skill's folder and every file the skill names.
- **The positive form.** A skill states its order once, in one numbered flow or run table, and any recap points back to it. A rule several steps apply has one named home the steps link to.
- **New Check evidence.** The skill's order told in more than one view (intro, chart, gates list, step summaries, a closing recap) where the views restate rather than point. A file that says it doesn't restate a thing and then restates it. An absolute rule in one file with a scoped exception to it in another.

This is the standard-layout idea as a reviewable rule instead of a template.
It leaves a small skill free to be one file, and it gives the reviewer in change 2 the words to name what it finds.

### 4. Backlog: one refactor each for the skills that already contradict themselves

The new review reports only on what a change adds, so existing duplicates stay until someone moves them.
After this PR lands, refactor one skill at a time, simplest first, on the pattern PR 589 used (inventory, home per rule, one-copy grep at the end):

1. blog-post (personal-ai-pod): one file, ~6 duplicated rules, 1 contradiction.
2. browser-chauffeur: ~80 KB across a few files, ~20 duplicated rules, 5 contradictions.
3. drainer: ~320 KB across engine, providers, and docs, ~15 duplicated rules, 5 contradictions.

Each refactor starts after the previous one's PR merges.
Its last step folds whatever it learned about the target shape into `authoring-rules` (or into this plan, when the lesson is about the refactor process), so the next skill starts from the sharper shape.

isc-ed-report needs none.
After the backlog clears, changes 1 and 2 catch new duplicates at the edit, so a periodic audit would spend tokens again on skills the review already covers.

### What the drainer refactor taught the process

The drainer refactor ([PR 1476](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1476)) closed the backlog; its rubric lesson went to `authoring-rules` in [PR 1477](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1477).
These are the process lessons:

- **The first survey undercounts.**
  This plan's skim found ~15 duplicated rules and 5 contradictions in drainer; the full survey of every file the skill reads found about 40 and 12.
  Size a refactor from the full survey, not the skim.
- **Map which session loads which file before the first review round.**
  drainer's worker, daily digest, and triage calls each load different files, and the correctness pass caught pointers a reader could never follow, such as the digest pointed into `worker-core.md`.
  Hand every reviewer that map along with SKILL_FILES, and survey the prompt strings scripts write for those sessions as prose.
- **The writing review and the correctness pass can pull opposite ways.**
  The writing review cut a rule restated beside its pointer; the correctness pass restored it because one reader never loads the home.
  Which session reads the file decides between them.
- **Rewriting a whole file can flip its line endings.**
  With `core.autocrlf=true`, a file written fresh as LF over a CRLF original diffs as a full rewrite.
  Compare against `git -c core.autocrlf=false archive origin/main`, and restage only the changed files with `git -c core.autocrlf=false add --renormalize`.
- **Write the new homes first, then split the edit.**
  Once "Sending" and "Close silently" existed in `worker-core.md`, a parallel session could repoint the provider files and script headers against them while this session did the engine, and the two halves agreed.

## Implementation, once approved

1. Branch `skill-single-source` from `main`.
2. Edit `writing-flow/SKILL.md`, `writing-review/SKILL.md`, `writing-review/reviewer-prompt.md`, `authoring-rules/SKILL.md`, and `document-authoring/learn-from-send.md` per changes 1-3.
3. Bump `document-authoring`'s minor version, since the review behavior changes.
4. Run `writing-flow`: cold `writing-review` on every changed file, mint, `gh pr create`, and hand over the `/changes` link.
5. After it merges, hand off the blog-post refactor; each later refactor launches once the one before it merges, in the order in change 4.

## Decision for Russell

Approve as written, or name the parts to change.
The one call worth a look: change 2 has the reviewer find related files by search, so a paraphrased copy that shares no keywords with the changed rule can slip past.
Reading every file of the skill instead closes that gap, at about 80K tokens a round for drainer.
