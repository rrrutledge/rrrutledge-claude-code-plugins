---
description: Explain why a command fell through to a manual approval prompt, whether it's safe, and whether safe-compounds can be taught to auto-approve it
---

Russell just had to manually approve a tool call — safe-compounds gave it no verdict at all and
let the normal permission dialog ask him. He wants to know why, whether it was actually safe,
and whether the hook can be taught to allow it next time.

This command is only for that silent-PROMPT case. It is **not** for a command safe-compounds
BLOCKed — a block already ships its own corrective message explaining exactly why and how to
rewrite it, so there's nothing left to diagnose there. If what Russell pastes turns out to be a
block (deny with a reason), say so and point him at that message instead of running the steps
below.

The command or tool call he's asking about: $ARGUMENTS

If nothing was pasted, don't ask him to paste anything — he captures a screenshot of the approval
prompt instead of copying the command. Invoke the `screenshot:screenshot` skill to load his most
recent screenshot, and treat it as the permission prompt to analyze: read the tool name and the
exact command or arguments off the dialog, then continue with the steps below. Only if the
screenshot doesn't show an approval prompt, say so and ask him to paste the command or describe the
tool call (e.g. an EnterWorktree/Write/mcp__ call) he was prompted for.

Use this command by default whenever Russell asks whether a prompted command could be auto-approved
or why it prompted, even without the slash command.

## 1. Identify the tool call shape

Figure out which branch of `plugins/safe-compounds/hook.py`'s `dispatch()` this call went
through: `Bash`, `Write`/`Edit`, an `mcp__*` tool, `Workflow`, `EnterWorktree`, or
`ExitWorktree`. Most pasted input is a Bash command; only treat it as another tool if Russell
says so or the text is clearly a tool-call description rather than a shell command.

## 2. Trace the actual decision path

Read the source for that branch and follow the real logic — don't guess from the docstrings
alone, since behavior can drift from the comments:

- **Bash** — `enforce.py` (`enforce_bash`, form validation that BLOCKs unvalidatable shapes:
  heredocs, redirection, `$()`, 3+ pipes, loops, cross-repo `cd &&`), then `shell.py`
  (`split_segments`) to break it into compound segments, then `approve.py`
  (`is_segment_trusted`) against `trust.py` (`TRUSTED_COMMANDS`, learned/settings integration)
  and `commands.py` (`SUBCOMMAND_SPECS` — trusted/conditional/specials per tool).
- **Write/Edit** — `writes.py` (`decide_write_edit`).
- **mcp__* ** — `mcp.py` (`classify_mcp_tool`).
- **Workflow** — `workflow.py` (`classify_workflow_tool` — blanket-trusted saved names vs. an
  AI safety verdict on an inline script).
- **EnterWorktree/ExitWorktree** — `worktree_tool.py` (`classify_enter_worktree`,
  `classify_exit_worktree`).

Pin down the *exact* check that failed to match — i.e. which allowlist the command *isn't* in,
or which condition made a normally-trusted form untrusted (e.g. a destructive flag, an
unregistered path, a race between two tool calls). If tracing instead turns up a BLOCK, stop and
redirect Russell to that message per the scope note above — don't run the rest of these steps.

If it's plausible the failure was a one-off ordering/timing artifact rather than a missing
allowlist entry (e.g. a path-existence check running before a preceding command finished, as can
happen with `EnterWorktree` on a custom worktree path created moments earlier), check for that
explicitly — reproduce the classifier call against current state if useful — before concluding a
code change is warranted.

## 3. Judge safety independently

Don't just report what the hook decided — form your own judgment against the two-part test in
this repo's root `CLAUDE.md`: **read-only** (can't change anything) or **surely reversible** (the
effect can be cheaply undone). State plainly whether the command met that bar, regardless of
what the hook did.

## 4. Recommend

- **If it's safe and there's a clean way to auto-approve it**, name the exact mechanism from the
  "Typical workflow: adding a new safe command" table in root `CLAUDE.md` (bare command →
  `TRUSTED_COMMANDS` in `trust.py`; subcommand → the right `*_TRUSTED_SUBCOMMANDS` set or
  `conditional` dict in `commands.py`; curl domain / private command / workflow name / trusted
  destination dir → `~/.claude/safe-compounds-config.json`), and propose the specific diff.
- **If it was a timing/ordering artifact**, say so and explain there's nothing to fix in the
  allowlists — the manual prompt was the hook correctly failing to prove safety at that moment,
  not a missing rule.
- **If it's genuinely not safe to auto-approve** (irreversible, or its safety can't be
  statically proven), say so plainly and explain what about it resists verification.

If Russell wants the fix made, implement it and open a PR through the `git-workflow` skill —
never push straight to `main`. Don't touch the allowlists on a hunch; only change what step 2
and 3 actually established.
