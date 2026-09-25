---
# Per-machine drainer settings. Copy to .claude/drainer.local.md in your project and fill in.
# Everything machine/user-specific lives here; the plugin (engine + providers) stays generic.

# Which providers to run, plus any config each needs. Reference a provider by name (they live in the
# plugin's providers/ dir). All sources are harvested every run on one schedule.
providers:
  orphan-sessions: {}         # crash-recovered Claude Code sessions (session-mgr) - no config; always dispatches first
  outlook: {}                    # work Outlook on the web (browser) - no config, just sign in
  outlook-graph: {}           # personal Outlook.com via the Microsoft Graph API (ms-graph; no browser)
  teams: {}                      # Microsoft Teams on the web (browser) - no config, just sign in
  trello:                        # outreach boards (via the trello-outreach skill)
    boards:
      - name: "<Board name>"
        id: "<board id>"
    skip_lists: [Abandoned, Finished, Adopted, Templates]
    label_vocab:
      channels: [Email, Teams, Slack]
      features: ["<feature label>"]
      # any label not in channels/features is treated as a contact name
  # slack:                       # example of another config-bearing provider (future)
  #   workspace: your-workspace  # the <workspace>.slack.com subdomain

# Credentials never go here - keep them in your OS credential store / environment
# (e.g. TRELLO_KEY / TRELLO_TOKEN for the trello provider).

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

# Actual poll cadence is the DrainerKeeper scheduled task's own repeat interval, not a config
# value here - the poller has no internal cadence knob.

# The continuous-keeper (run-poller.py) has no per-cycle work cap: every cycle enumerates everything
# eligible from every source. The worker buffer - tuned via the DRAINER_TARGET_REVIEWABLE (default 5)
# and DRAINER_MAX_CONCURRENT (default 18) environment variables, not here - is the only thing that
# throttles how much of it actually gets dispatched at once: each cycle tops the workers waiting for
# review up toward DRAINER_TARGET_REVIEWABLE, never exceeding DRAINER_MAX_CONCURRENT total live
# workers; anything held simply retries next cycle.

# Worker model per item - the poller picks by triage complexity (simple -> worker_model,
# complex -> worker_model_complex). Set an EXPLICIT model so workers don't inherit whatever the
# session default happens to be. Use the plain model id - no [1m] suffix needed, current models
# already report a 1M context window.
worker_model: claude-sonnet-5              # simple items (quick replies, trivial actions)
worker_model_complex: claude-opus-5-5      # complex items (multi-step work, code, delicate messages)
triage_model: claude-sonnet-5              # the per-cycle batched triage call (also pinned)

# EOD digest (run-digest.py) - the once-a-day interactive slow loop.
digest_model: claude-opus-5-5   # the digest session (summarize fyi, group junk)
orphan_grace_minutes: 15        # the poller re-queues any item whose source object is still unhandled with
                                # no live worker session on it - there is no time limit on an open session.
                                # This grace only stops a just-spawned worker (session not up yet) from being
                                # misread as dead.
digest_time: "17:00"            # wall-clock time (HH:MM, 24h) the daily digest task fires (used by the installer).
---

# drainer.local

Free-form notes about this machine's drainer setup (which sources are live, quirks, etc.).
Credentials never go here - keep them in your OS credential store / environment.
