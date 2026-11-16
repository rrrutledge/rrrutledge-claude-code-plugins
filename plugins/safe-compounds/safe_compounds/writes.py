"""Write/Edit tool handling: approve project & temp files, redirect stray temp
files into .tmp/, and otherwise fall through to a prompt.

Precedence matters: the dirs in BLOCKED_CLAUDE_DIRS are checked *before* the
.tmp/-anywhere check, since a path can match both (e.g.
main-worktree/.tmp/scan.py) and no allow decision there can actually
suppress Claude Code's own prompt anyway. Below that, a file inside .tmp/ or
a .claude config dir is approved *before* the temp-name check, so e.g.
".tmp/commit_tmp.txt" is allowed rather than redirected.
"""
import os
import re

from . import ai
from .log import log_debug
from .paths import (
    PLUGIN_CACHE_PATTERN, convert_msys_path_to_windows, is_in_git_repo, is_path_within_allowed_edits,
    is_path_within_cwd, is_path_within_trusted_destination, is_stray_root_file,
    is_within_claude_jobs_dir, resolve_against_cwd,
)

TEMP_FILE_NAME_PATTERNS = [
    re.compile(r'[_.-][Tt][Mm][Pp]$'),
    re.compile(r'[_.-][Tt][Ee][Mm][Pp]$'),
    re.compile(r'^\s*[Tt][Mm][Pp][_.-]'),
    re.compile(r'^\s*[Tt][Ee][Mm][Pp][_.-]'),
    re.compile(r'[_.-][Ss][Cc][Rr][Aa][Tt][Cc][Hh]$'),
    re.compile(r'[_.-][Ss][Tt][Aa][Gg][Ii][Nn][Gg]$'),
]


def looks_like_temp_file_by_name(file_path):
    basename = os.path.basename(file_path.replace('\\', '/'))
    return any(p.search(basename) for p in TEMP_FILE_NAME_PATTERNS)


def ask_ai_if_temp_file(file_path):
    """Ask Haiku whether a path looks like a temp/staging file; True/False/None."""
    log_debug(f"Asking AI if temp file: {file_path[:100]}")
    prompt = (
        f'Does this file path look like a temporary or staging file that should live in a .tmp/ directory '
        f'rather than where it is being written?\n\n'
        f'File path: {file_path}\n\n'
        'Respond with ONLY "YES" or "NO".\n\n'
        'YES: The name suggests it is temporary, staging, scratch, intermediate output, or a workaround '
        '(e.g. commit_msg_staging, _TMP suffix, scratch_output, temp_data, etc.)\n'
        'NO: The name suggests a permanent project artifact.\n\n'
        'Response (YES or NO):'
    )
    text = ai.ask(prompt, max_tokens=5)
    if text is None:
        return None
    return text.upper().startswith('YES')


def _temp_redirect_reason(file_path):
    base = os.path.basename(file_path.replace('\\', '/'))
    return (
        f'BLOCKED: "{base}" looks like a temporary file but is not in .tmp/. '
        'Write it to .tmp/ instead (e.g. .tmp/commit-msg.txt). '
        'Per CLAUDE.md, .tmp/ is the only location for temporary staging files.'
    )


def _file_exists(file_path):
    """Whether the file exists, reading ~, relative, and MSYS /c/... forms the way the path checks do."""
    path = resolve_against_cwd(file_path)
    if os.name == 'nt':
        path = convert_msys_path_to_windows(path)
    return os.path.exists(path)


def _stray_root_reason(file_path):
    base = os.path.basename(file_path.replace('\\', '/'))
    return (
        f'BLOCKED: "{base}" would be a loose file at the root of your home folder or a drive. '
        'For a scratch file, write it to .tmp/ in your working repo; otherwise put it inside a git repo. '
        'If the user named this exact path, tell them it needs to go somewhere else or that they should write it themselves.'
    )


DEFAULT_BLOCKED_DIR_ALTERNATIVE = 'Write scratch/staging files to .tmp/ in your own working repo instead.'

# Every entry's pattern is unanchored -- like PLUGIN_CACHE_PATTERN, it just
# has to appear somewhere in the path, so it needs no home-expansion or
# cross-platform normalization to catch both C:\...\.claude\drainer\... and
# /c/.../.claude/drainer/... forms. Add a new dir here for any other
# ~/.claude/<tool>/ runtime dir that should never receive a Write/Edit --
# every legitimate write there happens some other way (a setup script, a
# config read), never through this tool.
BLOCKED_CLAUDE_DIRS = [
    {
        'pattern': re.compile(r'\.claude[/\\]drainer[/\\]', re.IGNORECASE),
        'target': '~/.claude/drainer/',
    },
    {
        'pattern': re.compile(r'\.claude[/\\]browser-chauffeur[/\\]', re.IGNORECASE),
        'target': '~/.claude/browser-chauffeur/',
    },
    {
        # Shared with enforce.py's detect_plugin_cache_reference(), which scans whole raw
        # Bash command strings for this same pattern, not just Write/Edit paths.
        'pattern': PLUGIN_CACHE_PATTERN,
        'target': 'the installed plugin cache (~/.claude/plugins/cache/...)',
        'alternative': (
            "Point the plugin's checked-out repo source instead, or write scratch/staging "
            'files to .tmp/ in your own working repo.'
        ),
    },
]


JOBS_DIR_ENTRY = {
    'target': '~/.claude/jobs/',
    'alternative': (
        'The whole jobs tree is off-limits, even a .tmp/ folder inside it. Write the file to '
        '.tmp/ in your own working repo instead, outside ~/.claude.'
    ),
}


def _blocked_dir_reason(file_path, entry):
    base = os.path.basename(file_path.replace('\\', '/'))
    alternative = entry.get('alternative', DEFAULT_BLOCKED_DIR_ALTERNATIVE)
    return (
        f'BLOCKED: "{base}" targets {entry["target"]}. That sits under Claude Code\'s own '
        "sensitive config root, so a write there always hits Claude Code's native confirmation "
        f'no matter what this hook decides. {alternative}'
    )


def decide_write_edit(file_path):
    """Return ('allow'|'block'|'prompt', reason_or_None) for a Write/Edit path."""
    for entry in BLOCKED_CLAUDE_DIRS:
        if entry['pattern'].search(file_path):
            return 'block', _blocked_dir_reason(file_path, entry)

    # Same segment matcher the Bash side uses (enforce.detect_claude_jobs_write), so both
    # tools treat ~/.claude/jobs identically; a path ending in .tmp/ inside it is still blocked.
    if is_within_claude_jobs_dir(file_path):
        return 'block', _blocked_dir_reason(file_path, JOBS_DIR_ENTRY)

    cwd = os.environ.get('CLAUDE_CWD', os.getcwd()).replace('\\', '/')
    if not cwd.endswith('/'):
        cwd += '/'
    normalized = file_path.replace('\\', '/')
    if normalized.startswith(cwd):
        normalized = normalized[len(cwd):]

    if normalized.startswith('.tmp/') or '/.tmp/' in normalized:
        return 'allow', None

    full_norm = file_path.replace('\\', '/')
    if re.search(r'(^|/)\.claude/(skills|commands|screenshots)/', full_norm):
        return 'allow', None

    if looks_like_temp_file_by_name(file_path):
        return 'block', _temp_redirect_reason(file_path)

    if is_in_git_repo(cwd) and is_path_within_cwd(file_path):
        return 'allow', None

    # The same places cp/mv/ln may write outside the repo. A dir the user already covered with an
    # Edit/Write allow rule is theirs to have granted. A trusted destination dir (~/Downloads plus
    # config extras) is allowed only for a file that doesn't exist yet: a Write or Edit replaces
    # contents in place, and outside git an overwrite can't be undone.
    if is_path_within_allowed_edits(file_path):
        return 'allow', None
    exists = _file_exists(file_path)
    if is_path_within_trusted_destination(file_path) and not exists:
        return 'allow', None

    # Only a new loose file is blocked: that is the write that creates the stray. An existing one
    # was put there deliberately (or earlier), so editing it prompts as usual.
    if is_stray_root_file(file_path) and not exists:
        return 'block', _stray_root_reason(file_path)

    if ask_ai_if_temp_file(file_path):
        return 'block', _temp_redirect_reason(file_path)

    return 'prompt', None
