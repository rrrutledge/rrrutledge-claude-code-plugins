---
name: ship-plugin
description: Ship a PR in any GitHub repo in one command - merge it and fast-forward that repo's main checkout, leaving uncommitted work there in place. For a rrrutledge-claude-code-plugins PR it also waits for the auto-bump on merge and updates the marketplace and plugin cache at the right scope, with plugin-specific post-steps. Use when Russell says "ship it", "ship PR <n>", "/ship-plugin", "merge and pull this", or otherwise wants a PR merged and his local checkout current without spelling out every step.
---

# ship-plugin

Invoking this skill **is** Russell's authorization to merge the PR, mark it ready if it's a draft, and leave the session's worktree to sync the main checkout.

## The goal

The PR is merged and the repo's main checkout on this machine holds the merged result.
For a PR in the plugins repo, the goal also covers a new Claude session loading the change.
Two things stand between a merged plugin PR and that outcome.
The plugin cache is version-pinned, so a session keeps loading the cached version until the version number moves and the cache is refreshed.
The version number moves in an auto-bump commit that the repo's workflow pushes to `main` after the merge.

## What the script does

`scripts/ship.py`, under this skill's base directory, runs the whole chain; its docstring lists every step.
In concept it lands the PR, then brings this machine current with the merged result, then, for the plugins repo, reports the new versions and how they go live.

The session handles the two calls a script can't make: leaving a worktree through the harness, and resolving a conflict.

## Steps

1. **Run the script.**
   `python "<base directory>/scripts/ship.py" --pr <n>`, or with no `--pr` for the open PR on the current branch.
   Add `--repo <path to a local checkout>` when the PR lives in a repo other than the one holding the session's working directory.
   Add `--admin` only when Russell explicitly says to override branch protection.
2. **On exit code 3, leave the worktree and rerun.**
   The script has printed `EXIT_WORKTREE` (docstring step 5 says why).
   Call `ExitWorktree` with `action: keep`; this call is part of shipping, covered by the authorization above.
   Then run the `then rerun:` command it printed, which resumes after the merge.
3. **On exit code 1, handle the `ERR` line.**
   A merge conflict is resolved by merging `origin/main` into the branch, fixing the conflicts, and pushing; then rerun.
   Any other `ERR` goes to Russell as printed, including a sync git refused because a pull would overwrite uncommitted work in the main checkout.
4. **Relay the result.**
   Pass on the script's `PR`, `pulled`, `version`, `live`, and `note` lines.

A PR Russell merged himself still gets its main checkout synced, and for a plugin PR its cache refreshed, by running step 1 on it.
`python "<base directory>/scripts/ship.py" --sync-only` refreshes the plugin cache alone.
