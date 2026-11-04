---
name: pr-code-review
description: Review a branch's code before its PR opens - the built-in /code-review correctness pass plus a cold lean pass that holds every added line to being used and the change to being the smallest that does the job - then mint the code receipt the review gate checks on `gh pr create`. Use before opening any PR whose diff changes code, and whenever the gate denies a PR for unreviewed code.
---

# PR code review

This is the code half of the pre-PR review `git-workflow` requires ("Everything in the diff is reviewed before the PR opens"); `writing-review` is the prose half, and one gate enforces both.

## The gate

`document-authoring`'s `hooks/verify_gate.py` fires on `gh pr create`, computes the PR diff, and denies the PR once, listing every changed file that lacks a fresh receipt for its current content.
It classifies each file by its own test (`classify_changed_file` in that script): markdown that ships takes `writing-review`, hand-written program logic takes this skill, and data, config, lockfiles, and generated output pass through.
A receipt binds to the file's content, so an edit after the review needs a fresh review and mint, while re-running `gh pr create` on reviewed content costs nothing.
Every size of code change takes the review; a small diff makes a small review.

The steps below call the gate script `<gate>`; `writing-review`'s mint step (How to run it, step 6) gives its path.

## How to run it

1. Commit the work, then list what the gate would deny: `python <gate> pending`, with the same `--base` and `--head` the PR will use.
   The `code:` lines are this review's files; `prose:` lines go to `writing-review`.
2. **Correctness pass.**
   Invoke the `code-review` skill at `medium` effort with the current branch as the target.
3. **Lean pass.**
   Dispatch a fresh subagent with `lean-reviewer-prompt.md`, giving it the base and head refs (`HEAD` unless the PR names another `--head`), the `code:` files, and one or two sentences on what the change is for.
   The reviewer must come to the diff cold, for the same reason `writing-review` gives for its own reviewer.
4. Apply the findings you accept from both passes and commit them.
5. Review the fixes: run steps 2 and 3 again, each with a fresh reviewer.
   The lean reviewer gets the fix commits alone, as the range from the last reviewed commit to `HEAD`; the correctness pass reviews the whole branch again, since `code-review` takes a branch target.
6. Repeat from step 4 until both passes return clean, a finding stands that you genuinely disagree with, or you have run three rounds.
   Rejecting a finding follows `writing-review`'s **Standing to disagree**.
7. Run `pending` again, so files the fixes touched are included, and mint every `code:` path it prints: `python <gate> mint --kind code <path> <path> ...`.
   Then open the PR.

The PR body carries one line per finding you rejected and why, and what is still contested after three rounds.
