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
from .paths import PLUGIN_CACHE_PATTERN, is_in_git_repo, is_path_within_cwd, path_under

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
    return 'YES' in text.upper()


def _temp_redirect_reason(file_path):
    base = os.path.basename(file_path.replace('\\', '/'))
    return (
        f'BLOCKED: "{base}" looks like a temporary file but is not in .tmp/. '
        'Write it to .tmp/ instead (e.g. .tmp/commit-msg.txt). '
        'Per CLAUDE.md, .tmp/ is the only location for temporary staging files.'
    )


DEFAULT_BLOCKED_DIR_ALTERNATIVE = 'Write scratch/staging files to .tmp/ in your own working repo instead.'

# Plain ~/.claude/<tool>/ runtime dirs that should never receive a Write/Edit
# -- every legitimate write there happens some other way (a setup script, a
# config read), never through this tool. Add a new dir here for the common
# case; give it a full BLOCKED_CLAUDE_DIRS entry below instead only if it
# needs a non-.tmp/ alternative or a matcher other than path_under() (e.g.
# because something outside writes.py needs to reuse that same matcher, the
# way enforce.py reuses PLUGIN_CACHE_PATTERN against raw Bash command
# strings, not just Write/Edit paths).
BLOCKED_CLAUDE_SUBDIRS = [
    '~/.claude/drainer',
    '~/.claude/browser-chauffeur',
]


def _subdir_matcher(subdir):
    base = os.path.expanduser(subdir)
    return lambda p: path_under(p, base)


BLOCKED_CLAUDE_DIRS = [
    {'matcher': _subdir_matcher(subdir), 'target': f'{subdir}/'}
    for subdir in BLOCKED_CLAUDE_SUBDIRS
] + [
    {
        'matcher': lambda p: bool(PLUGIN_CACHE_PATTERN.search(p)),
        'target': 'the installed plugin cache (~/.claude/plugins/cache/...)',
        'alternative': (
            "Point the plugin's checked-out repo source instead, or write scratch/staging "
            'files to .tmp/ in your own working repo.'
        ),
    },
]


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
        if entry['matcher'](file_path):
            return 'block', _blocked_dir_reason(file_path, entry)

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

    if ask_ai_if_temp_file(file_path):
        return 'block', _temp_redirect_reason(file_path)

    return 'prompt', None
