---
name: ship-plugin
description: Merge a rrrutledge-claude-code-plugins PR and roll it out to this machine in one command - handles the merge, the repo's auto-bump on merge, the local pull, the marketplace + plugin cache update (at the right scope), and plugin-specific post-steps. Use when Russell says "ship it", "/ship-plugin", "merge and pull this", or otherwise wants a merged plugin change live in a new Claude session without spelling out every step.
---

# ship-plugin

Invoking this skill **is** Russell's authorization to merge the PR and mark it ready if it's a draft.

## The goal

A merged plugin change is live on this machine in a new Claude session.
Two things stand between a merged PR and that outcome.
The plugin cache is version-pinned, so a session keeps loading the cached version until the version number moves and the cache is refreshed.
The version number moves in an auto-bump commit that the repo's workflow pushes to `main` after the merge.

## What the script does

`scripts/ship.py`, under this skill's base directory, runs the whole chain; its docstring lists every step.
In concept it lands the PR, then brings this machine current with the merged result, then reports the new versions and how they go live.

The session handles the two calls a script can't make: leaving a worktree through the harness, and resolving a conflict.

## Steps

1. **Run the script.**
   `python "<base directory>/scripts/ship.py" --pr <n>`, or with no `--pr` for the open PR on the current branch.
   Add `--admin` only when Russell explicitly says to override branch protection.
2. **On exit code 3, leave the worktree and rerun.**
   The script has printed `EXIT_WORKTREE remove` or `EXIT_WORKTREE keep` (docstring steps 2 and 5 say why).
   Call `ExitWorktree` with that action; for `remove`, also pass `discard_changes: true`, because the squash merge gives the branch's commit a new hash and the tool reports it as unmerged.
   Then run the `then rerun:` command it printed, which resumes after the merge.
3. **On exit code 1, handle the `ERR` line.**
   A merge conflict is resolved by merging `origin/main` into the branch, fixing the conflicts, and pushing; then rerun.
   Any other `ERR` goes to Russell as printed.
4. **Relay the result.**
   Pass on the script's `PR`, `version`, `live`, and `note` lines.

A plugin PR merged outside this skill leaves the cache stale; `python "<base directory>/scripts/ship.py" --sync-only` refreshes it.
