---
name: git-workflow
description: Russell's git/GitHub workflow rules - branching from main, when PRs merge, closing PRs and branch cleanup, code review timing, repo deletion, gh CLI usage, PR link formatting. Use whenever creating a branch/commit/PR, merging, closing, or reviewing a PR, or doing any other git/gh operation.
---

# git-workflow

## Before opening a PR - read the target repo's own CLAUDE.md and CONTRIBUTING.md
Every repo can carry its own contribution rules on top of everything in this skill - a required screenshot, a local build/test step, a specific PR template.
Read that repo's own `CLAUDE.md`, `CONTRIBUTING.md`, and any `.github/PULL_REQUEST_TEMPLATE*` before opening a PR there, every time, even in a repo worked in before - a rule can be added after the last visit.
Follow whatever they ask in full: run the build/preview step they name, attach whatever artifact they ask for, and match their PR template if one exists.

**When the work targets a repo other than the session's working directory, root the session in that repo's own checkout.**
Claude Code auto-loads `CLAUDE.md` only from the session's own working-directory tree, so a session editing a different repo never sees that repo's rules on its own - and `CONTRIBUTING.md` and the PR template never auto-load even when the working directory is the repo.
Launch a fresh session (the handoff-launcher pattern) whose cwd is that repo's local checkout - its `~/Dev` clone, or one made per "Writing files to other repos" below - and write the seed so it tells the new session to read that repo's `CONTRIBUTING.md` and any `.github/PULL_REQUEST_TEMPLATE*`, so the reminder rides in with the launch rather than depending on memory.
This is the drainer's normal case: a worker's working directory is `personal-ai-pod`, yet a captured item can require changes in a different repo, and that is exactly when the target repo's rules would otherwise be missed.

## Auto Branch/Commit/PR
**HARD RULE: Never push directly to main. All changes go through a PR - no exceptions.**

**Russell decides when every PR merges** - by doing it himself or by saying to.
Create the branch/commit/PR, hand Russell the link, and wait.
Drive the work right up to "PR is open and green," then stop.
If review surfaces changes, push follow-up commits to the same branch.

**Closing a PR without merging it (superseded, duplicate, abandoned experiment): use `gh pr close <n> --delete-branch` as one command.**
GitHub's repo-level "auto-delete branch on merge" setting only fires on merge, never on close, so a closed PR's branch needs explicit cleanup.
`gh pr close --delete-branch` deletes it via the GitHub API as part of the close and is already on the trusted `gh pr` allowlist regardless of flags - it never prompts for approval.
A separate `git push origin --delete <branch>` after the fact does prompt every time (push carries a destructive-flag gate for `--delete`/`--force`).

**Run `/code-review` only when Russell asks for it** - it's token-heavy, so it's opt-in.
When he does request a review, run it on the local branch diff, apply the findings that make sense, and report what it found and fixed.
If a PR is already open when a review is requested, push the fixes as follow-up commits to the same branch.

**Always branch new work from main.**
Before creating a branch, check `git status` - if HEAD is not on main, create a worktree from main.
Branching from a feature branch silently inherits its unmerged commits into the new PR.

```bash
git worktree add .claude/worktrees/<name> -b <branch-name> main
```

**At the end of every turn where you've made file changes, proactively check for uncommitted work:**

1. Run `git status` to check for uncommitted changes
2. If changes exist and you've completed the user's request:
   - **High confidence** → Immediately create branch, commit, push, and PR **without asking**
   - **Lower confidence** → Say "Ready to create a PR for these changes?"

**High confidence criteria (all must be true):**
- **Specific, bounded change** - touches one concern with no ripple into unrelated code
- **Fully implemented** - all requested changes work as expected
- **No open threads** - no obvious next steps or unresolved issues
- **Focused and reviewable** - a single diff a reviewer can take in at a glance

**Never auto-create PR when:**
- **Still debugging or exploring**
- **Incomplete or experimental changes**
- **Review requested first** - Russell said "let me review first" or similar
- **More work is coming** - the conversation suggests it

**After user merges:**
- **Sync main automatically** - `git checkout main && git pull`

## GitHub
- **Repo deletion**: Always use browser chauffeur to delete repos through the GitHub UI (Settings → Delete this repository).
- **Use native `gh` subcommands; when no subcommand exists, reach for a native path rather than `gh api`.**
  `gh pr`, `gh issue`, `gh repo`, `gh run`, and the like are pre-approved by the hook and run without prompting.
  A write through `gh api` (POST/PUT/PATCH/DELETE) is flagged as dangerous and interrupts Russell for manual approval every time, which breaks the flow - so route around it.
  When a task has no `gh` subcommand, pick the native alternative that keeps moving: push a GitHub Actions workflow to do the job in CI, toggle the setting in the repo's Settings UI via browser-chauffeur, or clone-and-push for file changes (see below).
  Reserve `gh api` for read-only lookups that genuinely have no CLI equivalent.
- **GitHub Pages - enable once in the Settings UI, then let a workflow deploy.**
  The first-time enablement can't be done from CI: a workflow's ephemeral `GITHUB_TOKEN` gets `Resource not accessible by integration` when it tries to create the Pages site (even with `actions/configure-pages` `enablement: true`).
  So enable it by hand once - Settings → Pages → Source: GitHub Actions, via browser-chauffeur on the repo's settings page - then a committed Pages workflow (`actions/configure-pages` + `actions/upload-pages-artifact` + `actions/deploy-pages`) redeploys on every push to `main`.
  Watch the deploy with `gh run watch`.
- **Writing files to other repos: clone**: When a task requires modifying files in a repository other than the current working directory, clone (or sparse-checkout) that repo to `.tmp/`, make changes with normal file tools, commit, and push - rather than using `gh api` to PUT file contents directly.
  Check first whether the repo is already checked out under `~/Dev/`; if so, work there directly.
  For a contribution the repo's maintainers will review, see "Before opening a PR" above.
- **PR links point to the Files Changed tab**: When sharing a PR URL, always append `/changes` (e.g., `https://github.com/owner/repo/pull/123/changes`).
