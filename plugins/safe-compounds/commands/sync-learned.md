---
description: Promote any locally-learned safe commands into the plugin source via a PR
---

Promote this machine's safe-compounds "learned" commands into the plugin source
and open (or update) the shared pull request.

This normally happens automatically at session end. Run this to do it on demand:

1. Show what's pending: `cat ~/.claude/safe-compounds-learned.json` (if it's
   missing or all lists are empty, there's nothing to promote - say so and stop).

2. Run the sync: `python "${CLAUDE_PLUGIN_ROOT}/tools/sync_learned.py"`

   It folds each learned entry into the matching allowlist in the source via the
   GitHub API, opens/updates the rolling `safe-compounds-learned` PR, and prunes
   entries that have already landed in `main` from the local store.

3. Report the PR (the script prints a summary). If it printed nothing, there was
   nothing new to promote.

Requires `gh` authenticated.
