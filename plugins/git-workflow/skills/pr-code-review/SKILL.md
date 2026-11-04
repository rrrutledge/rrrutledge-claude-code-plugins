---
name: pr-code-review
description: Review a branch's code before its PR opens - the built-in /code-review correctness pass plus a cold lean pass that holds every added line to being used and the change to being the smallest that does the job - then mint the code receipt the review gate checks on `gh pr create`. Use before opening any PR whose diff changes code, and whenever the gate denies a PR for unreviewed code.
---

# PR code review

This is the code half of the pre-PR review `git-workflow` requires ("Everything in the diff is reviewed before the PR opens"); `writing-review` is the prose half.
`writing-review`'s **The stage gate** covers the gate that enforces both; the files it lists as `code:` are this skill's.
Which files count as code is the gate script's own test (`classify_changed_file`): hand-written program logic, leaving out data, config, lockfiles, and generated output.
Every size of code change takes the review; a small diff makes a small review.

The steps below call the gate script `<gate>`; `writing-review`'s mint step (How to run it, step 6) gives its path.
Pass every `<gate>` call the same `--base` and `--head` the PR will use.

## How to run it

1. Commit the work, then list what the gate would deny: `python <gate> pending`.
   The `code:` lines are this review's files; `prose:` lines go to `writing-review`.
2. **Correctness pass.**
   Invoke the `code-review` skill at `medium` effort with the PR's head branch as the target.
3. **Lean pass.**
   Dispatch a fresh subagent with `lean-reviewer-prompt.md`, giving it the base and head refs, the `code:` files, and one or two sentences on what the change is for.
   The reviewer must come to the diff cold, for the same reason `writing-review` gives for its own reviewer.
4. Apply the findings you accept from both passes and commit them.
5. Review the fixes: run steps 2 and 3 again, each with a fresh reviewer.
   The lean reviewer gets the fix commits alone, as the range from the last reviewed commit to the head; the correctness pass reviews the whole branch again, since `code-review` takes a branch target.
6. Repeat from step 4 until both passes return clean, a finding stands that you genuinely disagree with, or you have run three rounds.
   Rejecting a finding follows `writing-review`'s **Standing to disagree**.
7. Mint the code receipt on every file the gate still lists, those the fixes touched included: `python <gate> mint --kind code --pending`.
   Then open the PR.

The PR body carries one line per finding you rejected and why, and what is still contested after three rounds.
