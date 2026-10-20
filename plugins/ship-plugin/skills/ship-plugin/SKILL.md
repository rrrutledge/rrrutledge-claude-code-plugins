---
name: ship-plugin
description: Merge a rrrutledge-claude-code-plugins PR and roll it out to this machine in one command - handles the merge, the repo's auto-bump on merge, the local pull, the marketplace + plugin cache update (at the right scope), and plugin-specific post-steps. Use when Russell says "ship it", "/ship-plugin", "merge and pull this", or otherwise wants a merged plugin change live in a new Claude session without spelling out every step.
---

# ship-plugin

One command takes a plugin change from open PR to running on this machine.
Invoking this skill **is** Russell's authorization to merge the named PR: confirm which PR, then drive the whole chain without asking for step-by-step approval.

Run gh commands with `--repo rrrutledge/rrrutledge-claude-code-plugins`; the marketplace is `rrrutledge-claude-code-plugins`.
The rollout script is `scripts/ship.py` under this skill's base directory, the path shown when the skill loads.

## Why every step is needed

The plugin cache is **version-pinned**: a new Claude session keeps loading the cached version until the version number bumps *and* the local cache is refreshed.
A merge on its own changes nothing on this machine, so the skill closes both gaps.

## Steps

1. **Identify the PR.**
   Use the PR number or URL if given; otherwise resolve the open PR for the current branch (`gh pr view --json number,headRefName,files,title`).
   State the PR title and which `plugins/<name>` it touches, so the target is unambiguous before anything merges.

2. **Make the branch mergeable.**
   - If `main` has advanced past the PR's base, merge `origin/main` into the branch and push, so the merge itself meets no conflicts.
   - **Ship the version the PR already carries.**
     The `version-bump.yml` workflow patch-bumps every changed plugin in a `chore: auto-bump` commit straight to `main` once the PR merges, and leaves a plugin alone whose version the PR already moved, so shipping needs no edit to `plugin.json`.
   - Wait for any required checks to go green (`gh pr checks <n> --watch`).
     A plugin-content PR usually reports none, so an empty check list is expected.

3. **Merge.**
   `gh pr merge <n> --squash --delete-branch`.
   If branch protection reports the PR isn't mergeable until checks finish, use `--auto`; the script in step 5 waits for it to land.
   Reserve `--admin` for when Russell explicitly says to override.

4. **Return to the main checkout.**
   The script pulls `main` in the repo's main checkout, and the harness refuses a worktree-isolated session's writes there.
   So a session isolated in a worktree it entered first calls `ExitWorktree` with `action: "remove"` and `discard_changes: true`, which returns it to the main checkout.
   The discard is safe and needed: the squash merge gives the branch's commit a new hash, so the tool reports it as unmerged even though its content is on `main`.
   A worktree the session did not enter is removed with `git -C <repo> worktree remove <path> --force`.
   The remote branch was deleted at merge.

5. **Roll out.**
   From the main checkout, run `python "<base directory>/scripts/ship.py" --pr <n>`; pass `--repo <path>` when the working directory isn't a checkout of the plugins repo.
   The script waits for the merge and rolls the result out to the cache; its docstring lists the steps.
   Read its output and surface every line starting `ERR`.

6. **Report and hand off.**
   State what merged and the new version the script printed for each touched plugin.
   The change goes live on a **restart of Claude Code or `/reload-plugins`** - the update lands the new cache, and a fresh session loads it.
   If a `local` or `project` scoped plugin was updated, mention that it takes effect only in that project.

## Notes

- **The merge is the one shared-repo action.**
  The auto-bump commit follows it onto `main` on its own, and the rollout in steps 4-6 is reversible machine state, so it runs without asking.
- **This skill is the rollout trigger.**
  A plugin PR merged outside this skill leaves the cache stale until it's refreshed: run `python "<base directory>/scripts/ship.py" --sync-only`, or `/ship-plugin` on that PR, to catch up.
