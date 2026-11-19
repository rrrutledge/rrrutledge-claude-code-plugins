"""Classify EnterWorktree and ExitWorktree tool calls as auto-allowable.

Creating a *new* worktree (no `path` given) always lands under
`.claude/worktrees/`, the EnterWorktree equivalent of `git worktree add`
there -- already a trusted git subcommand -- so this case is approved
unconditionally.

Switching into an *existing* worktree (`path` given) is always blocked
instead of approved, no matter where that path lives. Claude Code enforces
its own permission-root confirmation on any explicit-`path` EnterWorktree
call -- moving the session's working directory, write access, and project
config counts as a permission-root change, and per Claude Code's own docs
only `bypassPermissions` mode can suppress that prompt; no hook decision
can. So approving the call buys nothing -- the harness still asks -- while
blocking it lets the redirect message steer the model to a genuinely
prompt-free alternative: work the existing worktree without relocating into
it, via `git -C <path> <cmd>` for git operations and absolute paths under it
for Read/Write/Edit and other file tools. Neither of those touches the
permission root, so neither prompts. A path outside `.claude/worktrees/`
gets an extra first step in the same message: move it into convention with
`git worktree move` (already a trusted git subcommand, preserving the
branch and any uncommitted work) before working on it directly -- the same
self-correcting pattern used elsewhere in this hook (e.g. the
PowerShell-tool block pointing at Bash).

The one gap in that redirect: a command that genuinely needs its own
working directory to *be* the worktree (e.g. an `npm` script that relies on
relative paths) can't be expressed as `-C <path> <cmd>` or an absolute path,
and still needs a real relocation -- which still costs the one click.

ExitWorktree takes no path at all -- it only ever acts on the one worktree
this same session entered via EnterWorktree, tracked internally by the
harness, not supplied by the model. `action: "keep"` never deletes anything.
`action: "remove"` mirrors `git branch -d`'s safety contract: the tool
itself refuses when the worktree has uncommitted files or commits that
never made it onto the original branch, unless `discard_changes: true`
forces it through -- so plain removal is reversible (everything it deletes
is already preserved elsewhere). The `discard_changes` override, which
knowingly throws work away, is blocked with a redirect to `keep` plus
`git worktree remove`, which does its own uncommitted-files check.
"""
from .log import log_debug


def _under_claude_worktrees(path):
    normalized = path.replace('\\', '/').rstrip('/')
    return normalized.endswith('/.claude/worktrees') or '/.claude/worktrees/' in normalized + '/'


def classify_enter_worktree(tool_input):
    """Return (approved, reason) for an EnterWorktree tool call.

    `reason` is a corrective message when `approved` is False, else None.
    """
    path = tool_input.get('path')
    if not path:
        log_debug("EnterWorktree: no path (creates fresh under .claude/worktrees/), approving")
        return True, None

    if _under_claude_worktrees(path):
        log_debug(f"EnterWorktree: path under .claude/worktrees/ ({path!r}), blocking with redirect")
        reason = (
            'BLOCKED: relocating into "{path}" would trigger Claude Code\'s own permission-root '
            'confirmation no matter what this hook decides -- only bypassPermissions mode can '
            'suppress it, and no hook decision can.\n\n'
            'To work in this worktree without that prompt, don\'t relocate into it -- operate on '
            'it directly instead:\n'
            '  git -C "{path}" <command>            # for git operations\n'
            '  (absolute paths under "{path}")       # for Read/Write/Edit and other file tools\n\n'
            'Only call EnterWorktree with no path (or `name`) when you actually want to create a '
            'brand new worktree.'
        ).format(path=path)
        return False, reason

    log_debug(f"EnterWorktree: path outside .claude/worktrees/ ({path!r}), blocking with redirect")
    reason = (
        'BLOCKED: "{path}" is outside .claude/worktrees/, and relocating into it would also '
        'trigger Claude Code\'s own permission-root confirmation no matter what this hook decides '
        '-- only bypassPermissions mode can suppress it, and no hook decision can.\n\n'
        'To keep this worktree\'s branch and any uncommitted work while staying in convention and '
        'avoiding that prompt, move it into place first:\n'
        '  git worktree move "{path}" "$(git rev-parse --show-toplevel)/.claude/worktrees/<name>"\n\n'
        'Then work in it directly, without calling EnterWorktree again:\n'
        '  git -C "<new path>" <command>            # for git operations\n'
        '  (absolute paths under "<new path>")       # for Read/Write/Edit and other file tools\n\n'
        'If there\'s nothing to preserve, call EnterWorktree with `name` (or no arguments) '
        'instead of `path` -- that always creates fresh under .claude/worktrees/ with no prompt.'
    ).format(path=path)
    return False, reason


DISCARD_REASON = (
    'BLOCKED: `discard_changes: true` forces the removal past uncommitted files and commits the tool '
    'thinks are unmerged (a squash merge always looks that way), so this hook can\'t prove nothing is lost.\n\n'
    'Leave the worktree without deleting it, then remove it with git, which checks for you:\n'
    '  1. ExitWorktree with `action: "keep"`.\n'
    '  2. git worktree remove "<worktree path>"   # refuses a worktree holding uncommitted files\n'
    '  3. git branch -d <branch>, or `git branch -D <branch>` once `gh pr view` shows the PR MERGED.'
)


def classify_exit_worktree(tool_input):
    """Return (decision, reason) for an ExitWorktree tool call.

    `decision` is 'allow', 'prompt', or 'block'; `reason` is the corrective message for a block.
    """
    action = tool_input.get('action')
    if action == 'keep':
        log_debug("ExitWorktree: action=keep, approving")
        return 'allow', None
    if action == 'remove':
        if tool_input.get('discard_changes'):
            log_debug("ExitWorktree: discard_changes=True forces past unsaved work, blocking with redirect")
            return 'block', DISCARD_REASON
        log_debug("ExitWorktree: plain remove (tool self-refuses on unsaved work), approving")
        return 'allow', None
    log_debug(f"ExitWorktree: unrecognized action {action!r}, prompting")
    return 'prompt', None
