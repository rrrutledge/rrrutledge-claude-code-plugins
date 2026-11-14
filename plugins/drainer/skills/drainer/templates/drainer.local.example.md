---
# Per-machine drainer settings. Copy to .claude/drainer.local.md in your project and fill in.
# Everything machine/user-specific lives here; the plugin (engine + providers) stays generic.

# Which providers to run, plus any config each needs. Reference a provider by name (they live in the
# plugin's providers/ dir, or in <local_dir>/providers/ for a machine-local one).
providers:
  orphan-sessions: {}         # crash-recovered Claude Code sessions (session-mgr) - no config; recent ones dispatch first
  outlook-graph: {}           # personal Outlook.com via the Microsoft Graph API (ms-graph; no browser)
  teams: {}                   # Microsoft Teams (the teams skill's REST reads; mark-read and drafts in the browser)
  trello:                     # outreach boards (via the trello skill's trello_utils.py)
    boards:
      - name: "<Board name>"
        id: "<board id>"
    skip_lists: [Abandoned, Finished, Adopted, Templates]
    label_vocab:
      channels: [Email, Teams, Slack]
      features: ["<feature label>"]
      # any label not in channels/features is treated as a contact name
  # Other shipped providers (gmail, outlook-rest, outlook-graph-junk, slack, zoom, physical-task) enable
  # the same way; each provider's prose file names any config it reads.

# Credentials never go here (docs/extending.md, "Credentials").

# A folder holding context.md (your world + standing rules). Keep it a RELATIVE path (resolved against
# the config root) and commit it in your project, so the poller reads it from the drainer's config root
# - a git worktree the poller keeps pinned to origin/main - rather than whatever branch a session left
# checked out. A relative local_dir is what makes a merged context.md/provider-overlay change take
# effect immediately. (An absolute path is still honored verbatim, for a machine-local, uncommitted
# context folder.)
local_dir: drainer-local
# Runtime state (seen/health/queue/seeds/items). Resolved against the REAL repo, not the config
# worktree, so it stays put and never re-enumerates when the config root moves to the worktree.
runtime_dir: .tmp/drainer

# The poller sets its own cadence (scripts/keeper.py), not a config value here.
# The worker buffer is tuned by the DRAINER_TARGET_REVIEWABLE and DRAINER_MAX_CONCURRENT environment
# variables, not here (engine/poller-core.md step 5; defaults in scripts/drainer_config.py).

# Worker model per item - the poller picks by triage complexity (simple -> worker_model,
# complex -> worker_model_complex). Set an EXPLICIT model so workers don't inherit whatever the
# session default happens to be. Use a family alias (opus / sonnet / haiku) - Claude Code resolves
# it to the newest model in that family, so launches move up with each release. No [1m] suffix
# needed, current models already report a 1M context window.
worker_model: sonnet              # simple items (quick replies, trivial actions)
worker_model_complex: opus        # complex items (multi-step work, code, delicate messages)
triage_model: sonnet              # the per-item triage call(s) (runs through `claude -p`, so an alias works)

# The daily digest (run-digest.py) - the once-a-day interactive review.
digest_model: opus              # the digest session (summarize fyi, group junk)
orphan_grace_minutes: 15        # reconcile's launch grace (engine/poller-core.md, "Reconcile"): how long a
                                # just-spawned worker may go without a session before it reads as dead.
digest_time: "17:00"            # wall-clock time (HH:MM, 24h) the daily digest task fires (used by the installer).
show_backlog_depth: true        # lead the digest with the per-source "N pending" backlog barometer. Set false
                                # once the queues are under control - the launcher then skips measuring it and
                                # the digest omits the section. Flip it back on if the backlog grows again.
---

# drainer.local

Free-form notes about this machine's drainer setup (which sources are live, quirks, etc.).
