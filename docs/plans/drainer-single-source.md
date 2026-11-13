# drainer: one home per rule

The third and last of the single-source refactors in [`skill-single-source.md`](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/blob/plan/skill-single-source/docs/plans/skill-single-source.md), section 4.
Paths are relative to `plugins/drainer/skills/drainer/`, and line numbers are on `main` at `3fdd490c`.

## Survey scope

The survey covered every file in the skill folder (`SKILL.md`, `engine/`, `providers/` prose, adapters, and `worker-prompt.txt`, `docs/`, `templates/`), the header comments of every script in `scripts/` and every adapter, `plugin.json`, the drainer entry in `.claude-plugin/marketplace.json`, the global `CLAUDE.md`, this repo's `CLAUDE.md`, and the live config the skill reads on this machine (`personal-ai-pod`'s `.claude/drainer.local.md` and `drainer-local/context.md`).
Every reviewer round gets that same list as SKILL_FILES, with the scripts and adapters as SCRIPT_PATHS.

**drainer is Russell's own, so a rule his global `CLAUDE.md` states gets a pointer to it.**
The engine names Russell 157 times, cites his LinkedIn suspension, and searches his ISC Gmail; the plugin has no README, and the repo has no forks.
That covers four rules: staging irreversible actions, archiving and deleting freely, the LinkedIn automation ban, and PR-link formatting (through `git-workflow`).
The drainer keeps only its own scoped part of each, such as which sessions count as having Russell present.

## Names

One name for each surface, used in every file:

| Surface | Name | Retired synonyms |
|---|---|---|
| The long-running process and its two loops | **the poller** (its **feed cycle** and **dispatch pass**) | "the keeper" outside `poller-core.md`'s "The keeper" section, "the fast loop" |
| The once-a-day review session | **the daily digest** | "the slow loop", "the EOD digest" |
| A per-item session | **the worker** | "worker session", "worker tab" |
| Making a source item gone | **CLEAR** | "advance", "clear/advance" (as a separate name for the same op) |

## The shape afterward

| File | Holds |
|---|---|
| `SKILL.md` | One sentence on what the drainer is, step 1 (load settings), and an index: one line per engine file, the `providers/` folder, and the docs, each naming what the file holds. No rules of its own; its "Hard rules" block becomes pointers. |
| `docs/architecture.md` | The model and why it is built this way: the drainer loop, the two layers, config from merged main. Each section on a mechanism is a short orientation that points at the engine file holding it. |
| `engine/poller-core.md` | Everything the poller does: where AI is used, the keeper, the feed cycle, dispatch, CLEAR timing for the poller's own clears, reconcile, hold-by-correspondent, one conversation one worker, and the fail-safe list. |
| `engine/triage.md` | The four buckets, `sender`, `chaser`, and tie-breakers. |
| `engine/screen.md` | What the security screen judges, which items it skips, and what a flag does. |
| `engine/worker-core.md` | The worker's needs-you flow, including the drainer's send rule, CLEAR timing for the worker, and a new **"Close silently"** section that every silent-resolution path links to. |
| `engine/auto-handle.md` | The auto-handle branch and the four disposition values. |
| `engine/pointers.md` | Resolving a pointer, for both the worker and the daily digest. |
| `engine/digest-core.md` | The daily digest's own steps. Rules it shares with the worker (situational check, the Trello check, pointers, dispatching work) become pointers. |
| `engine/provider.md` | The provider contract: the two files, every adapter hook (by name, pointing at `provider_base.py` for behavior), and every prose section a provider may define. |
| `providers/email-base.md` | Rules shared by the email providers. It stays the model for a shared-rules home. |
| `providers/<source>-provider.md` | Only that source's mechanics. |
| `docs/writing-a-provider.md` | The walkthrough and worked examples. Its section list and checklist point at `provider.md`. |
| `docs/extending.md` | Per-machine setup and the one home for trigger gating. |
| `templates/context.example.md` | A facts-only template, with the same "this file is facts to reason with" header as the live `context.md`. Its "How to decide the ACTION" and "Preferences" sections go. |

## Rule homes

Each row is a rule stated in two or more places today.
After the refactor it lives only in the home column, and every other location points there.

| Rule | Stated today at | Home |
|---|---|---|
| What a drainer is (read, decide the action, do it, draft, CLEAR) | `SKILL.md:7-9`, `architecture.md:8-13`, `worker-core.md:19` | `architecture.md` "What a drainer is" |
| The send rule and its exceptions | `SKILL.md:3,8,40-43`, `architecture.md:8,10`, `worker-core.md:15-18,232,287`, `digest-core.md:182-185`, `email-base.md:64-67`, `pointers.md:28,50`, `zoom-provider.md:84`, `worker-prompt.txt:5`, `context.example.md:32,40-43`, `plugin.json`, `marketplace.json` | `worker-core.md` "Sending" (new heading in the opening framing), pointing at the global `CLAUDE.md` for staging (see contradiction 1) |
| Archive and delete freely, narrated | `SKILL.md:44`, `context.example.md:46-47`, `digest-core.md:187` | Global `CLAUDE.md` "Email"; `provider.md` CLEAR keeps "reversible and narrated" as the contract |
| Lead with context in the final message | `SKILL.md:49`, `architecture.md:84`, `worker-prompt.txt:9-10`, `worker-core.md` §1 | `worker-core.md` §1 |
| Waiting on someone else means a tracker card | `SKILL.md:47-48`, `architecture.md:84`, `context.example.md:16` | `worker-core.md` §6 "Waiting on someone else" |
| Learn from every send | `SKILL.md:45-46`, `context.example.md:44-45`, `email-base.md:63`, `worker-core.md:19,56` | `worker-core.md` §5 |
| Situational check before acting | `context.example.md:27-31`, `worker-core.md` §2, `email-base.md` SITUATIONAL-CHECK | `worker-core.md` §2 for the rule; `email-base.md` keeps the email steps |
| Check Drafts before composing | `context.example.md:31`, `email-base.md:31` | `email-base.md` SITUATIONAL-CHECK step 3 |
| Ask the internal knowledge source first | `context.example.md:33-35`, `worker-core.md:91` | `worker-core.md` §2 |
| Triage has four buckets | `SKILL.md:30`, `architecture.md:73`, `run-poller.py:8`, `triage.md:20` | `triage.md` |
| Where AI is used in the poller | `SKILL.md:12,31`, `architecture.md:31-32`, `run-poller.py:6-13`, `poller-core.md:9-31` | `poller-core.md` "Where AI is used" (see contradiction 4) |
| The worker buffer (`target_reviewable`, `max_concurrent`) | `SKILL.md:11`, `architecture.md:20`, `poller-core.md:57,88-92`, `drainer.local.example.md:42-47` | `poller-core.md` step 5 for the behavior; `drainer_config.py` for the defaults |
| When the poller clears an item | `SKILL.md:11`, `architecture.md:22,24-25`, `poller-core.md:96-106`, `digest-core.md:3,165`, `worker-core.md:192-193`, `gmail-provider.md:48`, `outlook-graph-provider.md:55`, `slack-provider.md:92-93`, `run-poller.py:25-29`, `provider_base.py:862-875` | `poller-core.md` steps 5-6 (see contradiction 2) |
| When the worker clears an item | `worker-core.md` §2d and §6, `email-base.md:35-38`, `slack-provider.md:84-86`, `teams-provider.md:114-115`, `physical-task-provider.md:124`, `trello-provider.md:155`, `worker-prompt.txt:6-7`, `context.example.md:47` | `worker-core.md` §2d, with Trello's after-the-send rule as its scoped exception (see contradiction 3) |
| Email keys one item per message | `worker-core.md:131,186`, `email-base.md:37` | `email-base.md` "CLEAR TIMING" |
| Completion is read off the source | `architecture.md:41`, `poller-core.md:108-128,191`, `worker-core.md:326-328`, `digest-core.md:7,173` | `poller-core.md` "Reconcile" |
| The fail-safe list | `architecture.md:38-41`, `poller-core.md:185-191` | `poller-core.md` "Fail-safe" |
| One conversation, one worker | `architecture.md:35`, `worker-core.md:151`, `poller-core.md:170-176` | `poller-core.md` "One conversation, one worker" |
| An acknowledgement is not a reply | `worker-core.md:108-113`, `email-base.md:30`, `slack-provider.md:49`, `digest-core.md:120`, `worker-core.md:339` | `worker-core.md` §2 (already says so; the others already point, so only `:339`'s restatement shrinks) |
| The security screen: what it is, junk skipped, fail-closed, what a flag does | `screen.md:4-10,60-63`, `poller-core.md:17-22,66-71`, `architecture.md:74-78`, `run-poller.py:8-10`, `worker-core.md:27-45`, `auto-handle.md:14`, `pointers.md:45-47` | `screen.md` for the rubric, the junk skip, and a flag's effect; `poller-core.md` step 4b for the fail-closed hold; `worker-core.md` "Security screen" for what the worker does on a flag |
| An authenticated self-email is Russell's command | `screen.md:41-58`, `worker-core.md:39-41`, `triage.md:62-69` | `screen.md` for authentication; `triage.md` for the bucket; `worker-core.md` keeps one line pointing at both |
| Junk-learning order | `SKILL.md:30`, `triage.md:99`, `provider.md:34`, `writing-a-provider.md:52`, `email-base.md:84-101`, `outlook-rest-provider.md:44`, `gmail-provider.md:53`, `outlook-graph-provider.md:63` | `email-base.md` JUNK-LEARNING (see contradiction 5); `provider.md` says only that a provider defines its own order |
| Resolving a pointer (JS shell, hosted PDF, Safe Links) | `triage.md:22-29`, `worker-core.md` §2b, `digest-core.md:106-115`, `outlook-graph-provider.md:36-46`, `pointers.md` | `pointers.md`; `digest-core.md` keeps only "resolve on your own, never offer"; `outlook-graph-provider.md` keeps the Graph commands |
| The LinkedIn automation ban | `pointers.md:36-38`, `worker-core.md:255`, `ack-review.md:26` | Global `CLAUDE.md` "LinkedIn"; `pointers.md` keeps the deep-link extraction steps |
| The outreach Trello check | `worker-core.md:144-148`, `digest-core.md:126-129` | `worker-core.md` §2 |
| Dispatch other work out of the session | `worker-core.md` step 3, `digest-core.md:177-181` | `worker-core.md` step 3 |
| Read screenshots and PDFs in a subagent | `worker-core.md:218-219`, `pointers.md:19` | `worker-core.md` step 3 (`pointers.md` already points; no change) |
| Trello: leave Start alone to claim a card | `worker-core.md:154,276-280,358,367-370`, `trello-provider.md:48-54` | `trello-provider.md` CAPTURE |
| Queue a digest entry and close silently | `worker-core.md` §2c, §6a, `:412`, `auto-handle.md` step 4.2, `pointers.md:52-55` | `worker-core.md` "Close silently" (new), used by §2b, §2c, §6a, and auto-handle |
| The disposition values | `auto-handle.md` step 4, `worker-core.md:383-385`, `digest-core.md:80` | `auto-handle.md` step 4 |
| Closing the session (`close-session.py`) | `auto-handle.md:44-49`, `worker-core.md:175,357-359`, `close-session.py:1-16` | `close-session.py` header; the prose names the call and when |
| Closing owned browser tabs | `auto-handle.md:42-43`, `worker-core.md:415-420` | `worker-core.md` §6 "Close your browser tabs" |
| Config read from merged main | `architecture.md:43-57`, `worker-core.md:54-55,145`, `drainer.local.example.md:27-32` | `architecture.md` "Config comes from merged main" |
| The provider contract's two files and sections | `provider.md`, `writing-a-provider.md:7-18,46-55,104-109`, `poller-core.md:193-197` | `provider.md` |
| Where a machine-local provider lives | `extending.md:35-38`, `writing-a-provider.md:13-15` | `writing-a-provider.md` "Where it lives" |
| Trigger gating syntax | `extending.md:16-28`, `writing-a-provider.md:85-97` | `extending.md` "Triggers" (new heading); `writing-a-provider.md` keeps its per-section gating note |
| Credentials never in a repo | `extending.md:5,40-43`, `drainer.local.example.md:24-25,73` | `extending.md` "Credentials" |
| Sibling skills | `SKILL.md:51`, `extending.md:45-47` | `extending.md` "Sibling skills it uses" |
| Triage skipped by rule for Trello, orphan sessions, physical tasks | `orphan-sessions-provider.md:32`, `physical-task-provider.md:89`, `run-poller.py:2253-2281` | `run-poller.py`; the provider docs keep a pointer |
| Poller cadence | `keeper.py:1-18`, `install-schedule.ps1:1-6`, `drainer.local.example.md:38-40`, `gmail-adapter.py:13` | `keeper.py` header |

## Contradictions

### Settled from a script, the platform, or the history

1. **"Never send" (`SKILL.md:41`, the marketplace entry, `worker-prompt.txt:5`, `email-base.md:64-67`, `zoom-provider.md:84`, `context.example.md:42`) vs the worker's per-message exception (`worker-core.md:17`, `plugin.json`).**
   The global `CLAUDE.md` makes the final act Russell's "unless he says to do it in that moment", and `document-authoring`'s Stage step defines that exception; a worker is a session Russell drives.
   Two more scoped sends exist in code: `slack_reaction.py` adds a reaction when send-confidence's auto-send test passes, and `ack_sweep.py send` sends acks Russell approved as a batch.
   The "Sending" home states the rule and all three scoped forms; the marketplace entry matches `plugin.json`.
2. **"The poller never clears" (`SKILL.md:11`, `architecture.md:22,24-25`) vs the code.**
   `run-poller.py` clears fyi and junk in the feed cycle (`:2439`), marks Teams fyi and junk read through a batch worker (`_spawn_teams_mark_read`, `:2461`), and clears a needs-you item right after its worker launches (`dispatch_pass`, `:2845`).
   `poller-core.md` steps 5-6 already match the code except for Teams (`:98,105` say a provider with no `clear`, naming Teams, leaves fyi and junk for the daily digest), so that home gains the Teams path and every other copy points at it.
   The `clear()` docstrings in `gmail-adapter.py:209` and `outlook-graph-adapter.py:140` say "fyi/junk" only and gain the needs-you case.
3. **When the worker clears: "as soon as the scope is accounted for" (`worker-core.md:181-188`, `email-base.md:38`) vs "only after the user confirms any send" (`worker-prompt.txt:6-7`, `trello-provider.md:155`, `context.example.md:47`).**
   Nothing loads `worker-prompt.txt` (no script or doc names it), so it goes; the template line goes with the template's rules sections.
   Trello's later CLEAR is deliberate (moving a card's Start is the only thing that brings it back), and the open [PR 1258](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1258) refines exactly that rule, so §2d states it as its one scoped exception and the refactor rebases over whichever of the two merges first.
   `slack-provider.md:84-86` says to mark read once every ask is handled, but the poller already marks a needs-you Slack conversation read at launch; that CLEAR section becomes a pointer at §2d, which already says so (`:192-193`).
   The "email providers implement `clear`" wording (`worker-core.md:192`, `poller-core.md:98,101`) becomes "a provider whose adapter implements `clear`", since `outlook-rest-adapter.py` has none.
4. **Two AI uses (`SKILL.md:12`) vs three plus a delivery call (`poller-core.md:12`, `architecture.md:31`).**
   The code also launches the Teams mark-read batch worker and four diagnostic sessions (`_spawn_diagnostic` `:1146`, `_spawn_scan_diagnostic` `:1194`, `_spawn_provider_config_diagnostic` `:1235`, `_spawn_provider_auth_diagnostic` `:1278`).
   The home lists them all, and `digest-core.md:16`'s "would never reach Russell" becomes "the daily digest is the guaranteed channel", since an auth diagnostic can reach him first.
5. **Junk-learning order: rule first (`email-base.md:87-89`) vs unsubscribe first (`triage.md:99`, `provider.md:34`, `writing-a-provider.md:52`, `SKILL.md:30`, `outlook-rest-provider.md:44`).**
   [PR 447](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/447) deliberately changed the order to rule first on 2026-07-13 and updated only `email-base.md`; the other copies predate it.
6. **Where the hard rules live: `templates/context.example.md` (`architecture.md:84`) vs the engine (`worker-core.md:52`).**
   The live `context.md` already opens by saying the generic rules live in the engine and it holds only facts, so the template follows the live file.
7. **A screen-flagged item is "never silently filed to fyi / junk" (`screen.md:62`) vs left in Junk with no noise (`outlook-graph-junk-provider.md:6,42`).**
   `split_junk_items` in `run-poller.py` (`:2323-2326`, comment included) keeps a flagged Junk-folder item in Junk on purpose: it never dispatches, so nothing acts on it.
   `screen.md` states that as its one scoped exception.
8. **Trello credentials `TRELLO_KEY` (`extending.md:41`, `drainer.local.example.md:25`) vs `TRELLO_API_KEY` (`trello_utils.py:117`).** The code wins.
9. **Trello reads through `trello-outreach` (`SKILL.md:25`, `drainer.local.example.md:12`, `writing-a-provider.md:68`, `extending.md:46`) vs the `trello` skill's `trello_utils.py` (`trello-provider.md:8`, the adapter).** The adapter wins; `trello-outreach` stays named for the funnel policy.
10. **"Add scheduling glue (overlap lock, one interval)" (`extending.md:54-55`) vs "No overlap lock" (`poller-core.md:190`) and `keeper.py`'s single-instance lock.**
    `install-schedule.ps1` registers the task, and `keeper.py` holds the lock and the cadence, so the setup step becomes "run `install-schedule.ps1`".
    `poller-core.md:190`'s "No overlap lock" is about workers, so it becomes "two workers on one item need no lock".
11. **"The plugin never contains anything that identifies the user" (`architecture.md:67`, `extending.md:3`) vs an engine that names Russell throughout.**
    What the overlays and `<local_dir>/providers/` actually keep out of the shipped files is anyone's accounts, vendors, and workspaces, so the claim is scoped to that.
12. **`close-session.py:5-7` says it resolves its helper "the same pattern as spawn-handoff.py" but checks the installed cache first, where `spawn-handoff.py:4-5` checks the dev checkout first.** The header describes what `close-session.py` does.

### Stale references, fixed in the same pass

- `SKILL.md:21-26` lists `providers/outlook-provider.md` (absent) and a browser Teams provider, and omits seven shipped providers; it becomes a pointer at the `providers/` folder. The same stale Outlook-web and browser-Teams wording goes from `architecture.md:64`, `extending.md:31,46`, `outlook-graph-provider.md:10`, and the `outlook: {}` entry in `drainer.local.example.md:9`.
- The "harvest interval" knob (`SKILL.md:15`, `extending.md:9,55`) doesn't exist.
- `poller-core.md:7` cites a spec under `docs/superpowers/` that was never committed; the line goes. `poller-core.md:197`'s "New sources (Gmail, Trello, …)" names shipped ones.
- Script and adapter headers that disagree with their own code: `slack-adapter.py:24` (a constant that doesn't exist), `trello-adapter.py:4-5,582` (the id scheme, a `seen-state.js clear` command that doesn't exist), `orphan-sessions-adapter.py:15` (the id source), `zoom_client.py:1` and `zoom-adapter.py:17` (the OAuth grant), `seen-state.js:20` ("the prose poller").
- `teams-provider.md:65` names `week-in-review-analyzer`, and `trello-provider.md:85` names `confluence-investigator`; neither ships in this marketplace.

### Found, outside this refactor

`teams-provider.md:57-58` has the worker write each meeting-recording action item to `items/*-ai<N>.json` and says the next feed cycle picks it up, but the poller only lists sources and never reads `items/`, so those action items never get a worker.
That is a behavior bug rather than a duplicate, so the refactor leaves the text alone and I'd brief it as its own fix.

The open drainer PRs [1258](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1258), [1265](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1265), [1267](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/1267), and [980](https://github.com/rrrutledge/rrrutledge-claude-code-plugins/pull/980) touch files this refactor edits; whichever lands second rebases.

## Refactor

1. Branch `drainer-single-source` from `main`.
2. Add the new headings first ("Sending" and "Close silently" in `worker-core.md`, "Triggers" in `extending.md`), so every pointer names a findable section.
3. Move each rule to its home, replace every other mention with a pointer, apply the settled contradictions, and fix the stale references.
4. Grep each rule's keywords across SKILL_FILES and confirm one stating copy remains.
5. Bump `drainer`'s minor version, since `SKILL.md`'s description, the send rule's statement, and the template change shape.
6. Run `writing-flow`: cold `writing-review` split by file, beside a cold rule-fidelity check (old files vs new, rule by rule, every pointer resolves); then `pr-code-review` for the header comments; mint; `gh pr create`; hand over the `/changes` link.

## Decision for Russell

Every contradiction above settled from code, git history, or the live config.
The one call: fix the Teams action-item gap as its own PR after this one (my recommendation), or fold it into this refactor.
