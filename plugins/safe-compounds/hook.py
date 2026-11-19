#!/usr/bin/env python
"""PreToolUse hook entrypoint: orchestrates enforcement + approval.

Decision order (preserved from the original single-file hook):
  PowerShell tool  -> deny (use the Bash tool)
  Write / Edit     -> writes.decide_write_edit
  mcp__*           -> mcp.classify_mcp_tool
  Workflow         -> workflow.classify_workflow_tool (blanket-approved saved names,
                       or an AI safety verdict on an inline script's agent() prompts)
  EnterWorktree    -> worktree_tool.classify_enter_worktree (approved only when creating
                       fresh with no path; any explicit path is denied with a redirect to
                       work the worktree directly instead, since Claude Code's own
                       permission-root confirmation for any relocation can't be suppressed
                       by any hook)
  ExitWorktree     -> worktree_tool.classify_exit_worktree (keep, or a remove the
                       tool itself won't force past unsaved work; a forced
                       discard is denied with a redirect to git worktree remove)
  Bash             -> enforce.enforce_bash (deny), then per-segment trust (allow)
  anything else    -> defer (no output)

Output uses the current hook schema: an allow/deny permissionDecision, or no
output at all to defer to the normal permission flow (a manual prompt).

Library modules live in safe_compounds/. Set SAFE_COMPOUNDS_DISABLE_AI=1 to
disable Haiku fallbacks; SAFE_COMPOUNDS_TRUSTED_JSON to pin the trusted set.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from safe_compounds import config, paths, scripts  # noqa: E402
from safe_compounds.approve import is_segment_trusted  # noqa: E402
from safe_compounds.enforce import enforce_bash  # noqa: E402
from safe_compounds.log import log_debug  # noqa: E402
from safe_compounds.mcp import classify_mcp_tool  # noqa: E402
from safe_compounds.shell import split_segments  # noqa: E402
from safe_compounds.trust import get_trusted  # noqa: E402
from safe_compounds import workflow  # noqa: E402
from safe_compounds.workflow import classify_workflow_tool  # noqa: E402
from safe_compounds.worktree_tool import classify_enter_worktree, classify_exit_worktree  # noqa: E402
from safe_compounds.writes import decide_write_edit  # noqa: E402

POWERSHELL_TOOL_REASON = (
    'BLOCKED: PowerShell tool is disabled per global CLAUDE.md rule '
    '("Always use Bash tool, never PowerShell tool"). Re-issue this command using the Bash tool. '
    'For Windows-specific operations, use bash equivalents or a Python script in .tmp/. If PowerShell '
    'is genuinely required, invoke it from Bash via `powershell -NoProfile -Command "..."`.'
)


def allow():
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'allow'}}))
    sys.exit(0)


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
        'permissionDecisionReason': reason}}))
    sys.exit(0)


def defer():
    sys.exit(0)


def handle_bash(command):
    if not command:
        defer()

    log_debug(f"=== Hook evaluating command: {command[:200]}")

    reason = enforce_bash(command)
    if reason:
        log_debug(f"DECISION: Deny (enforce): {reason[:80]}")
        deny(reason)

    segments = split_segments(command)
    non_empty = [s for s in segments if s.strip()]
    if not non_empty:
        defer()

    trusted = get_trusted()
    paths.extract_pending_worktree_paths(non_empty)

    if not all(is_segment_trusted(seg, trusted) for seg in non_empty):
        block = scripts.get_block_reason()
        if block:
            log_debug(f"DECISION: Deny (AI judged script unsafe): {block[:80]}")
            deny(block)
        log_debug("DECISION: Defer (not all segments trusted)")
        defer()

    # No $()-substitution re-check is needed here: enforce_bash() blocks any
    # command containing a $() / backtick substitution before this point, so by
    # the time a command reaches approval it provably has none. (The original
    # single-file hook carried a substitution-trust loop here that was therefore
    # unreachable; extract_substitutions remains in shell.py as a tested helper.)

    log_debug("DECISION: Allow")
    allow()


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        defer()

    try:
        dispatch(data)
    except Exception:
        log_debug(f"HOOK CRASHED, deferring to normal permission flow:\n{traceback.format_exc()}")
        defer()


def dispatch(data):
    config.reset()
    paths.reset_pending_worktree_paths()
    paths.reset_allowed_edit_dirs()
    scripts.reset_block_reason()
    workflow.reset_block_reason()

    tool = data.get('tool_name')
    tool_input = data.get('tool_input', {})

    if tool == 'PowerShell':
        log_debug("DECISION: Deny (PowerShell tool)")
        deny(POWERSHELL_TOOL_REASON)

    if tool == 'Read':
        file_path = tool_input.get('file_path', '').replace('\\', '/')
        import re
        m = re.search(r'(^|/)\.claude/([^/]+)/', file_path)
        if m and m.group(2) in {
            'agents', 'commands', 'navigation', 'rules', 'screenshots',
            'skills', 'teams', 'templates', 'test-plans', 'worktrees',
        }:
            log_debug(f"DECISION: Allow Read from .claude/{m.group(2)}/: {file_path[:100]}")
            allow()
        defer()

    if tool in ('Write', 'Edit'):
        file_path = tool_input.get('file_path', '')
        decision, reason = decide_write_edit(file_path)
        log_debug(f"DECISION: {decision} Write/Edit: {file_path[:100]}")
        if decision == 'allow':
            allow()
        if decision == 'block':
            deny(reason)
        defer()

    if tool and tool.startswith('mcp__'):
        if classify_mcp_tool(tool):
            log_debug(f"DECISION: Allow MCP tool {tool}")
            allow()
        defer()

    if tool == 'Workflow':
        if classify_workflow_tool(tool_input):
            log_debug(f"DECISION: Allow Workflow {tool_input.get('name')}")
            allow()
        block = workflow.get_block_reason()
        if block:
            log_debug(f"DECISION: Deny (AI judged workflow script unsafe): {block[:80]}")
            deny(block)
        defer()

    if tool == 'EnterWorktree':
        approved, reason = classify_enter_worktree(tool_input)
        if approved:
            log_debug(f"DECISION: Allow EnterWorktree {tool_input.get('path') or tool_input.get('name')}")
            allow()
        log_debug(f"DECISION: Deny EnterWorktree (path given, redirecting to direct access): {tool_input.get('path')}")
        deny(reason)

    if tool == 'ExitWorktree':
        decision, reason = classify_exit_worktree(tool_input)
        if decision == 'allow':
            log_debug(f"DECISION: Allow ExitWorktree action={tool_input.get('action')}")
            allow()
        if decision == 'block':
            log_debug("DECISION: Deny ExitWorktree discard_changes, redirecting to git worktree remove")
            deny(reason)
        defer()

    if tool != 'Bash':
        defer()

    handle_bash(tool_input.get('command', ''))


if __name__ == '__main__':
    main()
