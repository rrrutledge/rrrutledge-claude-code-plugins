"""Per-command safety checkers for commands that need more than name-trust:
git/gh write-protection, curl destination rules, sed -i, package managers,
start/wt/cmd launchers, and CWD-scoped file operations (cp/mv/touch/ln/chmod).
"""
import os
import re

from . import ai, config
from .learned import add_learned_command, add_learned_subcommand, learned_subcommands
from .log import log_debug
from .paths import (
    is_in_trusted_script_dir, is_path_within_allowed_edits, is_path_within_claude_plugins,
    is_path_within_cwd, is_path_within_git_worktree, is_path_within_trusted_destination,
    is_safe_read_location, is_within_any_tmp_dir, read_script_file,
)
from .shell import ASSIGNMENT_ONLY, get_subcommands, shell_tokenize

# ---------------------------------------------------------------- curl --------
CURL_WRITE_FLAGS = {
    '-X', '--request', '-d', '--data', '--data-raw', '--data-binary',
    '--data-urlencode', '-F', '--form', '--form-string', '-T', '--upload-file', '--json',
}
CURL_LOCALHOST_PATTERNS = re.compile(r'https?://(localhost|127\.0\.0\.1)(:[0-9]+)?(/|$|\s)')


def _matches_configured_domain(segment):
    """True if the URL host contains a domain the consumer marked trusted."""
    domains = config.get_config().get('curl_domains', [])
    for domain in domains:
        if re.search(rf'https?://[^\s/]*{re.escape(domain)}', segment):
            return True
    return False


def is_curl_safe(segment):
    """Approve curl to localhost or a configured-trusted domain (any method);
    otherwise allow only read-only (no write flag) requests.

    Rationale: a GET is read-only, and configured corporate domains are treated
    as trusted for reversible work. Writes to arbitrary public URLs are not
    auto-approved.
    """
    if CURL_LOCALHOST_PATTERNS.search(segment) or _matches_configured_domain(segment):
        return True
    return not any(token in CURL_WRITE_FLAGS for token in segment.split())


# ----------------------------------------------------------------- sed --------
SED_INPLACE_PATTERN = re.compile(r'^-[a-zA-Z]*i')


def is_sed_command_safe(seg):
    """Allow sed unless an -i (in-place edit) flag is present."""
    for token in shell_tokenize(seg)[1:]:
        if token.startswith('--'):
            continue
        if SED_INPLACE_PATTERN.match(token):
            return False
    return True


# ============================================================ subcommand tools
# git, gh, and the package managers all share one shape: skip leading flags,
# find the subcommand, check it against an allowlist, and (for most) fall back
# to an AI judgment that is then remembered. They are expressed declaratively in
# SUBCOMMAND_SPECS and evaluated by check_subcommand_tool(). Tools whose logic
# doesn't fit (curl, sed, start, wt, .cmd files, cp/mv/...) stay bespoke below.
#
# A spec is a dict with:
#   trusted       - set of always-allowed subcommand names
#   category      - learned-store key (None disables AI fallback + learning)
#   conditional   - {subcommand: {destructive flags}} -> allow unless a flag is present
#   specials      - {subcommand: callable(args) -> bool} for one-off rules
#   cache_clean   - allow "<tool> cache clean"
#   flag_style    - 'simple' or 'yarn' (yarn's long flags consume a value)
#   global_opts   - flags that take a value before the subcommand (git -C <dir>)
#   allow_empty   - decision when there is no subcommand (git alone is harmless)
# A spec may instead be a callable(seg) -> bool for a fully bespoke tool (gh).

# A shell redirection token, e.g. 2>/dev/null, >>out, <in, 2>&1, &>log. These
# must never be mistaken for a subcommand when locating it.
_REDIRECT_OP = re.compile(r'^&?\d*(>>?|<)')
_REDIRECT_BARE = re.compile(r'^&?\d*(>>?|<)$|^&>$')


def _subcommand_prompt(command, subcommand, full_segment):
    return (
        f'Is "{command} {subcommand}" a safe, legitimate software development operation?\n\n'
        f'Full command:\n```\n{full_segment}\n```\n\n'
        'Respond with ONLY "SAFE" or "DANGEROUS".\n\n'
        'SAFE: Common build, test, install, format, lint, publish, deploy, or dev workflow operations.\n'
        'DANGEROUS: Operations that could cause irreversible system harm, data exfiltration, or security compromise.\n\n'
        'Response (SAFE or DANGEROUS):'
    )


def _ai_learn(command, label, seg, category, store_value=None):
    """Ask the AI about an unknown subcommand; remember it on approval."""
    verdict, _ = ai.call_ai(_subcommand_prompt(command, label, seg))
    if verdict is True:
        add_learned_subcommand(category, store_value if store_value is not None else label)
        return True
    return False


def _extract_subcommand(tokens, global_opts, flag_style):
    """Return (subcommand, trailing tokens) after skipping leading flags."""
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in global_opts:
            i += 2
            continue
        if flag_style == 'yarn' and t.startswith('--') and '=' not in t:
            i += 2
            continue
        if t.startswith('-'):
            i += 1
            continue
        if _REDIRECT_OP.match(t):
            # Skip a redirection; a bare operator also consumes its target token.
            i += 2 if _REDIRECT_BARE.match(t) else 1
            continue
        return t, tokens[i + 1:]
    return None, []


def _check_subcommand_tool(seg, spec):
    """Generic evaluator for a subcommand-shaped tool (see SUBCOMMAND_SPECS)."""
    tokens = shell_tokenize(seg)
    sub, args = _extract_subcommand(tokens, spec.get('global_opts', set()), spec.get('flag_style', 'simple'))
    if sub is None:
        return spec.get('allow_empty', False)
    if spec.get('case_insensitive'):
        sub = sub.lower()

    category = spec.get('category')
    allowed = spec['trusted'] | (learned_subcommands(category) if category else set())
    if sub in allowed:
        return True

    conditional = spec.get('conditional', {})
    if sub in conditional:
        return not any(a in conditional[sub] for a in args)

    specials = spec.get('specials', {})
    if sub in specials:
        return specials[sub](args)

    if spec.get('cache_clean') and sub == 'cache':
        first = next((a for a in args if not a.startswith('-')), None)
        return first == 'clean'

    if category:
        return _ai_learn(spec['command'], sub, seg, category)
    return False


# ----------------------------------------------------------------- git --------
# Allowlist, not denylist: approve only known read-only or reversible
# subcommands; dual-use ones are approved unless a destructive flag is present.
GIT_GLOBAL_OPTS_WITH_ARG = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix'}

GIT_TRUSTED_SUBCOMMANDS = {
    # read-only
    'status', 'log', 'diff', 'show', 'remote', 'describe', 'rev-parse', 'rev-list',
    'ls-files', 'ls-remote', 'ls-tree', 'shortlog', 'blame', 'reflog', 'cat-file', 'for-each-ref',
    'symbolic-ref', 'name-rev', 'grep', 'count-objects', 'merge-base', 'merge-tree', 'cherry',
    'whatchanged', 'show-ref', 'show-branch', 'archive', 'diff-tree', 'diff-index', 'diff-files',
    'format-patch', 'fsck', 'verify-commit', 'verify-tag', 'verify-pack', 'version', 'bisect',
    'check-ignore', 'check-attr', 'check-ref-format', 'check-mailmap',
    'range-diff', 'patch-id', 'var', 'help', 'interpret-trailers', 'index-pack',
    # reversible writes (the effect can be undone)
    'add', 'branch', 'commit', 'mv', 'rm', 'stash', 'fetch', 'pull', 'merge', 'rebase', 'revert', 'cherry-pick',
    'worktree', 'config', 'init', 'clone', 'update-ref', 'restore', 'sparse-checkout',
}

GIT_CONDITIONAL_SUBCOMMANDS = {
    'push':   {'--force', '-f', '--delete'},
    'tag':    {'-d', '--delete'},
    'switch': {'--discard-changes', '-f', '--force'},
}


def _git_checkout_ok(args):
    # Block force-discards and broad wildcard restores; allow specific-file restores
    if '-f' in args or '--force' in args:
        return False
    if '--' in args:
        dash_idx = args.index('--')
        pathspecs = args[dash_idx + 1:]
        has_ref = dash_idx > 0
        if not has_ref and pathspecs == ['.']:
            return False
        if '.' in pathspecs:
            return False
    return True


def _git_clean_ok(args):
    return any(a in ('-n', '--dry-run') for a in args)


def _git_reset_ok(args):
    if '--hard' not in args:
        return True
    # Reject if -- appears: tokens after it are pathspecs, not refs.
    if '--' in args:
        return False
    # Allow --hard only when syncing to a remote tracking ref (origin/...).
    # The target commits exist on the remote; committed local work is recoverable via reflog.
    # Note: uncommitted working-tree changes are NOT recoverable — --hard always discards them.
    non_flag_args = [a for a in args if not a.startswith('-')]
    return bool(non_flag_args) and non_flag_args[0].startswith('origin/')


GIT_SPEC = {
    'command': 'git',
    'trusted': GIT_TRUSTED_SUBCOMMANDS,
    'conditional': GIT_CONDITIONAL_SUBCOMMANDS,
    'specials': {'checkout': _git_checkout_ok, 'clean': _git_clean_ok, 'reset': _git_reset_ok},
    'global_opts': GIT_GLOBAL_OPTS_WITH_ARG,
    'allow_empty': True,
    'category': None,  # no AI fallback: unknown git subcommands prompt
}


# ------------------------------------------------------ package managers ------
NPM_TRUSTED_SUBCOMMANDS = {'audit', 'ci', 'dedupe', 'install', 'list', 'ls', 'outdated', 'prune', 'root', 'run', 'test', 'update'}
YARN_TRUSTED_SUBCOMMANDS = {
    'add', 'audit', 'backstage-cli', 'bin', 'build:all', 'build:backend',
    'build:frontend', 'check', 'dedupe', 'dev', 'dlx', 'exec', 'explain',
    'husky', 'info', 'install', 'link', 'lint:all', 'lint:fix', 'list',
    'node', 'outdated', 'pack', 'prettier', 'prettier:check', 'prettier:fix',
    'remove', 'run', 'start', 'start-backend', 'test', 'test:all', 'tsc',
    'unlink', 'upgrade', 'why', 'workspace', 'workspaces',
}
PIP_TRUSTED_SUBCOMMANDS = {'2>&1', 'check', 'freeze', 'install', 'list', 'show', 'uninstall'}
PNPM_TRUSTED_SUBCOMMANDS = {'install', 'add', 'update', 'remove', 'list', 'outdated', 'prune'}
BUN_TRUSTED_SUBCOMMANDS = {'install', 'add', 'update', 'remove', 'test'}
SCHTASKS_TRUSTED_SUBCOMMANDS = {'/query', '//query', '/change', '//change', '/disable', '//disable', '/enable', '//enable'}
# Read-only verbs only: qe/el/gl/gli/ep/gp just read the event log or its
# metadata. sl/im/um mutate log config or publishers, and cl/epl/al clear,
# export, or archive a log — all excluded so they keep prompting.
WEVTUTIL_TRUSTED_SUBCOMMANDS = {
    'qe', 'query-events', 'el', 'enum-logs', 'gl', 'get-log',
    'gli', 'get-log-info', 'ep', 'enum-publishers', 'gp', 'get-publisher',
}


# ------------------------------------------------------------------ gh --------
GH_TRUSTED_SUBCOMMANDS = {
    'pr':       {'view', 'list', 'diff', 'checks', 'create', 'edit', 'close', 'reopen', 'ready', 'comment', 'review', 'checkout'},
    'issue':    {'view', 'list', 'create', 'edit', 'close', 'reopen', 'comment'},
    'repo':     {'view'},
    'run':      {'view', 'list', 'cancel', 'rerun'},
    'release':  {'view', 'list', 'create', 'edit'},
    'workflow': {'view', 'list', 'run'},
}
GH_WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
# The bare repo endpoint (repos/owner/repo). A PATCH here edits repo settings
# (delete_branch_on_merge, default_branch, visibility, ...) which is reversible —
# just flip the value back. A DELETE here destroys the whole repo and is NOT
# reversible, so it is deliberately excluded below (DELETE keeps prompting).
GH_REPO_SETTINGS_PATTERN = re.compile(r'/?repos/[^/]+/[^/]+/?$')
GH_REVERSIBLE_API_PATTERNS = [
    re.compile(r'/?orgs/[^/]+/teams(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/branches(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/pulls(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/issues(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/labels(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/milestones(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/comments(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/git/refs(?:/|$)'),
    re.compile(r'/?repos/[^/]+/[^/]+/projects(?:/|$)'),
]
GH_AI_TRUSTED_PAIRS_BASE = {
    '--version:*', 'issue:2>&1', 'label:*', 'org:*', 'pr:merge',
    'project:*', 'repo:clone', 'repo:create', 'repo:edit', 'repo:list',
}


def _gh_trusted_pairs():
    return GH_AI_TRUSTED_PAIRS_BASE | learned_subcommands('GH_AI_TRUSTED_PAIRS')


def _gh_api_safe(tokens):
    """gh api: read methods are fine; write methods only to reversible endpoints."""
    api_url = ''
    method = 'GET'
    i = 2
    while i < len(tokens):
        if tokens[i] in ('--method', '-X') and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
            continue
        if not tokens[i].startswith('-') and not api_url:
            api_url = tokens[i]
        i += 1
    if method in GH_WRITE_METHODS:
        if method != 'DELETE' and GH_REPO_SETTINGS_PATTERN.search(api_url):
            return True
        return any(p.search(api_url) for p in GH_REVERSIBLE_API_PATTERNS)
    return True


def _gh_repo_sync_ok(tokens):
    """gh repo sync fast-forwards a branch from its source (like `git pull
    --ff-only`) and fails rather than rewriting history on divergence. --force
    lifts that guard and can discard commits, so it keeps prompting."""
    return not any(t in ('--force', '-f') for t in tokens)


GH_SPECIALS = {
    'repo': {'sync': _gh_repo_sync_ok},
}


# gh global flags that consume the next token as their argument
GH_GLOBAL_FLAGS_WITH_ARG = {'--repo', '-R', '--hostname', '--cwd'}


def _gh_find_group(tokens):
    """Return (index, group) skipping only argument-consuming global flags
    (--repo/-R/--hostname/--cwd). Any other token — including --version or
    a subcommand — stops the scan and is returned as the group."""
    i = 1
    while i < len(tokens) and tokens[i] in GH_GLOBAL_FLAGS_WITH_ARG:
        i += 2
    return i, tokens[i] if i < len(tokens) else None


def is_gh_command_safe(seg):
    """gh is two-level (group action) plus the special `api`/`auth` groups, so it
    has its own handler — but it shares the AI-learn fallback of the engine."""
    tokens = shell_tokenize(seg)
    if len(tokens) < 2:
        return True
    group_idx, group = _gh_find_group(tokens)
    if group is None:
        return True
    if group in ('search', 'status', 'browse'):
        return True
    if group == 'auth':
        subs = get_subcommands(seg, skip=group_idx + 1)
        return bool(subs) and subs[0] in ('status', 'token', 'switch')
    if group == 'api':
        return _gh_api_safe(tokens)
    if group in GH_TRUSTED_SUBCOMMANDS:
        subs = get_subcommands(seg, skip=group_idx + 1)
        action = subs[0] if subs else ''
        if action in GH_TRUSTED_SUBCOMMANDS[group]:
            return True
        special = GH_SPECIALS.get(group, {}).get(action)
        if special and special(tokens):
            return True
        pair = f'{group}:{action}'
        if pair in _gh_trusted_pairs():
            return True
        return _ai_learn('gh', f'{group} {action}', seg, 'GH_AI_TRUSTED_PAIRS', store_value=pair)
    pair = f'{group}:*'
    if pair in _gh_trusted_pairs():
        return True
    return _ai_learn('gh', group, seg, 'GH_AI_TRUSTED_PAIRS', store_value=pair)


# Read-only WMI verbs; everything else (call/delete/set/create/assoc) mutates.
WMIC_READONLY_VERBS = {'get', 'list'}
WMIC_DESTRUCTIVE_VERBS = {'call', 'delete', 'set', 'create', 'assoc'}


def is_wmic_safe(seg):
    """wmic queries (`wmic <alias> where ... get/list`) are read-only and safe.
    Its verb follows a `where` clause rather than sitting in second position, so
    wmic gets a bespoke handler: approve when a read-only verb is present and no
    mutating verb (call/delete/set/create) appears anywhere in the command."""
    tokens = [t.lower() for t in shell_tokenize(seg)]
    if WMIC_DESTRUCTIVE_VERBS & set(tokens):
        return False
    return bool(WMIC_READONLY_VERBS & set(tokens))


# ----------------------------------------------------- the spec table ---------
SUBCOMMAND_SPECS = {
    'git':  GIT_SPEC,
    'npm':  {'command': 'npm',  'trusted': NPM_TRUSTED_SUBCOMMANDS,  'category': 'NPM_TRUSTED_SUBCOMMANDS',  'cache_clean': True, 'allow_empty': True},
    'yarn': {'command': 'yarn', 'trusted': YARN_TRUSTED_SUBCOMMANDS, 'category': 'YARN_TRUSTED_SUBCOMMANDS', 'cache_clean': True, 'flag_style': 'yarn', 'allow_empty': True},
    'pip':  {'command': 'pip',  'trusted': PIP_TRUSTED_SUBCOMMANDS,  'category': 'PIP_TRUSTED_SUBCOMMANDS',  'allow_empty': True},
    'pnpm': {'command': 'pnpm', 'trusted': PNPM_TRUSTED_SUBCOMMANDS, 'category': 'PNPM_TRUSTED_SUBCOMMANDS', 'allow_empty': True},
    'bun':      {'command': 'bun',      'trusted': BUN_TRUSTED_SUBCOMMANDS,      'category': 'BUN_TRUSTED_SUBCOMMANDS',      'allow_empty': True},
    'schtasks': {'command': 'schtasks', 'trusted': SCHTASKS_TRUSTED_SUBCOMMANDS, 'category': None, 'case_insensitive': True},
    'reg':      {'command': 'reg',      'trusted': {'query'},                     'category': None, 'case_insensitive': True},
    'wevtutil': {'command': 'wevtutil', 'trusted': WEVTUTIL_TRUSTED_SUBCOMMANDS,   'category': None, 'case_insensitive': True},
    'gh':   is_gh_command_safe,
    'wmic': is_wmic_safe,
}

SUBCOMMAND_TOOLS = set(SUBCOMMAND_SPECS)


def check_subcommand_tool(command, seg):
    """Evaluate a subcommand-shaped tool via its spec (or bespoke handler)."""
    spec = SUBCOMMAND_SPECS[command]
    if callable(spec):
        return spec(seg)
    return _check_subcommand_tool(seg, spec)


def is_git_command_safe(segment):
    """Back-compat wrapper used by unit tests."""
    tokens = shell_tokenize(segment)
    if not tokens or tokens[0] != 'git':
        return True
    return _check_subcommand_tool(segment, GIT_SPEC)


def is_pkg_manager_safe(seg, manager):
    """Back-compat wrapper."""
    return _check_subcommand_tool(seg, SUBCOMMAND_SPECS[manager])


# --------------------------------------------------------------- start --------
START_SAFE_EXTENSIONS = {
    '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.pdf', '.rtf', '.odt',
    '.md', '.txt', '.csv', '.tsv', '.json', '.log', '.xml', '.yaml', '.yml',
    '.html', '.htm',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico', '.tiff',
    '.mp4', '.mov', '.webm', '.mp3', '.wav',
}


def is_start_safe(seg):
    tokens = shell_tokenize(seg)
    if len(tokens) < 2:
        return False
    args = [t for t in tokens[1:] if not t.startswith('/')]
    if not args:
        return False
    target = args[-1].strip('"\'')
    if target.startswith('http://') or target.startswith('https://'):
        return True
    if os.path.isdir(target):
        return True
    _, ext = os.path.splitext(target)
    return ext.lower() in START_SAFE_EXTENSIONS


# ---------------------------------------------------------- powershell --------
POWERSHELL_ENV_PATTERN = re.compile(
    r'^powershell\s+-NoProfile\s+-Command\s+"?\[System\.Environment\]::GetEnvironmentVariable\('
)


def is_powershell_safe(seg):
    return bool(POWERSHELL_ENV_PATTERN.match(seg.strip()))


# ------------------------------------------------------------ taskkill --------
# A drainer worker closes its own tab by killing the terminal's hosting process
# (see plugins/drainer/skills/drainer/engine/worker-core.md). Approve ONLY the
# narrow self-close shape: `/PID <n>` (never `/IM`, `/S`, `/U`, `/P`, `/FI`, ...)
# where every PID given is proven — via a live OS process-ancestry walk, not a
# trusted argument — to be this session's own tab host. Anything else defers to
# a manual prompt.
#
# The Bash tool runs Git Bash, whose MSYS layer treats a single leading slash
# as a POSIX path and mangles it — so commands built for that tool arrive as
# `//PID`, `//T`, `//F` (doubled slash escapes the mangling) rather than the
# Windows-native `/PID`. Normalize away the doubling before comparing so both
# forms are recognized.
TASKKILL_NO_ARG_FLAGS = {'/t', '/f'}


def _normalize_taskkill_flag(flag):
    return re.sub(r'^/+', '/', flag.lower())


def is_taskkill_safe(seg):
    tokens = shell_tokenize(seg)
    if len(tokens) < 2:
        return False
    pids = []
    i = 1
    while i < len(tokens):
        flag = _normalize_taskkill_flag(tokens[i])
        if flag == '/pid':
            if i + 1 >= len(tokens) or not tokens[i + 1].isdigit():
                return False
            pids.append(int(tokens[i + 1]))
            i += 2
            continue
        if flag in TASKKILL_NO_ARG_FLAGS:
            i += 1
            continue
        return False
    if not pids:
        return False
    from . import procs
    host_pid = procs.self_tab_host_pid()
    return host_pid is not None and all(pid == host_pid for pid in pids)


# ------------------------------------------------------------------ wt --------
WT_SUBCOMMANDS = {
    'new-tab', 'nt', 'split-pane', 'sp', 'focus-tab', 'ft', 'move-focus', 'mf',
    'swap-pane', 'focus-pane', 'fp', 'move-pane', 'mp', 'new-window', 'nw',
}
WT_FLAGS_WITH_ARG = {
    '-d', '--startingDirectory', '--title', '-p', '--profile', '--tabColor',
    '--colorScheme', '-w', '--window', '--size', '--pos', '-s', '--startingDir',
    '--appendCommandLine',
}


def is_wt_exe_path_safe(seg):
    """Approve full-path wt.exe only when launching the Claude session script."""
    seg_lower = seg.lower()
    return 'launch-session.ps1' in seg_lower and 'claude' in seg_lower


def is_wt_safe(seg, trusted):
    """Approve `wt` if it launches the known-safe Claude session script, or if
    the program it ultimately launches is trusted.

    first_word() collapses any path (bare `wt` or a full `.../wt.exe` path)
    down to `wt`, so the exe-path-specific check has to happen here rather
    than as a separate dispatch branch in approve.py.
    """
    if is_wt_exe_path_safe(seg):
        return True
    tokens = shell_tokenize(seg)
    if len(tokens) < 2:
        return False
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in WT_FLAGS_WITH_ARG:
            i += 2
            continue
        if t.startswith('-'):
            i += 1
            continue
        base = os.path.basename(t.strip('"\'').replace('\\', '/')).lower()
        if base.endswith('.exe'):
            base = base[:-4]
        if base in WT_SUBCOMMANDS:
            i += 1
            continue
        return base in trusted
    return True


# ----------------------------------------------------------- cmd files --------
CMD_BATCH_SAFE_PATTERNS = [
    re.compile(r'^@?echo\s+(off|on)\s*$', re.IGNORECASE),
    re.compile(r'^setlocal\b', re.IGNORECASE),
    re.compile(r'^endlocal\b', re.IGNORECASE),
    re.compile(r'^exit\s*/b', re.IGNORECASE),
    re.compile(r'^goto\s+:eof\s*$', re.IGNORECASE),
    re.compile(r'^:[a-zA-Z_]'),
    re.compile(r'^set\s+', re.IGNORECASE),
    re.compile(r'^for\s+%%', re.IGNORECASE),
    re.compile(r'^if\s+', re.IGNORECASE),
    re.compile(r'^echo\b', re.IGNORECASE),
]


def is_cmd_file_safe(filepath, trusted):
    from .approve import is_segment_trusted
    filepath = filepath.strip('"\'')
    if is_in_trusted_script_dir(filepath):
        return True
    content = read_script_file(filepath)
    if content is None:
        return False
    batch_trusted = trusted | {ASSIGNMENT_ONLY}
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('::') or re.match(r'^rem\b', line, re.IGNORECASE):
            continue
        if line.startswith('@'):
            line = line[1:].strip()
        if any(p.match(line) for p in CMD_BATCH_SAFE_PATTERNS):
            continue
        if re.match(r'^call\s+', line, re.IGNORECASE):
            line = line[5:].strip()
        if '%' in line:
            from .scripts import ask_ai_about_script
            return ask_ai_about_script(content, 'batch', filepath) is True
        if not is_segment_trusted(line, batch_trusted):
            return False
    return True


# -------------------------------------------------- CWD-scoped file ops -------
CWD_FILE_COMMAND_CONFIG = {
    'mv':    {'flags_with_args': {'-S', '--suffix', '-t', '--target-directory'}, 'min_args': 2, 'skip_args': 0},
    'cp':    {'flags_with_args': {'-S', '--suffix', '-t', '--target-directory'}, 'min_args': 2, 'skip_args': 0},
    'touch': {'flags_with_args': {'-r', '--reference', '-d', '--date', '-t'},    'min_args': 1, 'skip_args': 0},
    'ln':    {'flags_with_args': {'-t', '--target-directory', '-S', '--suffix'}, 'min_args': 2, 'skip_args': 0},
    'chmod': {'flags_with_args': {'--reference'},                                'min_args': 2, 'skip_args': 1},
}


def _dest_allowed(path):
    # /dev/null discards whatever's written to it, so it's always a safe
    # destination -- same carve-out is_output_redirection_safe() already
    # makes for `> /dev/null`.
    if path.strip().strip('"\'') == '/dev/null':
        return True
    return (is_path_within_cwd(path) or is_path_within_git_worktree(path)
            or is_path_within_allowed_edits(path) or is_path_within_claude_plugins(path)
            or is_within_any_tmp_dir(path))


def _source_allowed(path):
    return _dest_allowed(path) or is_safe_read_location(path)


def check_cwd_file_command(seg, command):
    """Approve cp/mv/touch/ln/chmod only when paths stay within CWD (and, for
    sources, safe read locations)."""
    cfg = CWD_FILE_COMMAND_CONFIG.get(command)
    if not cfg:
        return False
    tokens = shell_tokenize(seg)
    if not tokens or tokens[0].replace('.exe', '') != command:
        return False

    args = []
    i = 1
    skip_next = False
    while i < len(tokens):
        token = tokens[i]
        if skip_next:
            skip_next = False
            i += 1
            continue
        if token.startswith('-') and token != '-':
            if token in cfg['flags_with_args']:
                skip_next = True
            i += 1
            continue
        args.append(token)
        i += 1

    if len(args) < cfg['min_args']:
        return False
    paths = args[cfg['skip_args']:]

    if command in ('cp', 'mv', 'ln') and len(paths) >= 2:
        dest = paths[-1].strip('"\'')
        if dest == '/dev/null':
            # Content copied to /dev/null is discarded, never written or
            # exposed anywhere -- the source-location check exists to stop
            # sensitive files from landing somewhere readable, which can't
            # happen here, so skip it.
            return True
        if not (_dest_allowed(dest) or is_path_within_trusted_destination(dest)):
            log_debug(f"{command} destination outside allowed areas: {dest}")
            return False
        for src in paths[:-1]:
            if not _source_allowed(src.strip('"\'')):
                log_debug(f"{command} source outside allowed areas: {src}")
                return False
        return True

    for path in paths:
        if not _dest_allowed(path.strip('"\'')):
            log_debug(f"{command} path outside allowed areas: {path}")
            return False
    return True


# ----------------------------------------------- output redirection ----------
def is_output_redirection_safe(segment):
    """True if any > / >> redirect targets a relative path, .tmp/, or /dev/null.

    Quoted spans are masked (length-preserving, so match positions still line
    up with the original string) before scanning for the '>' operator, so a
    literal '>' inside a quoted argument (e.g. a grep pattern like ">$886")
    isn't mistaken for a real redirect operator. The target is then read back
    from the original (unmasked) string, so a genuinely quoted redirect
    target like `> ".tmp/foo.txt"` still extracts correctly."""
    masked = re.sub(
        r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'',
        lambda m: '\0' * len(m.group(0)),
        segment,
    )
    match = re.search(r'(?:^|[;\|&]|\s)(>>?)\s*([^\s;&|]+)', masked)
    if not match:
        return True
    target = segment[match.start(2):match.end(2)].strip().strip('"\'')
    if not re.match(r'^([a-zA-Z]:|/|\\)', target):
        return True
    if target.startswith('.tmp/') or target.startswith('.tmp\\'):
        return True
    if '/.tmp/' in target or '\\.tmp\\' in target or '/.tmp\\' in target or '\\.tmp/' in target:
        return True
    if target == '/dev/null':
        return True
    return False


# enforce_bash's own redirect detector (enforce.py: detect_output_redirection)
# runs before any segment reaches this module, and blocks every '>' / '>>'
# redirect except stream merges/discards (2>&1, >&2, 2>/dev/null, >/dev/null,
# ...). So a stream-merge/discard clause is the only kind of redirect that can
# still be present on a segment by the time per-command checkers run.
_SAFE_REDIRECT_CLAUSE = re.compile(r'(?:^|\s)\d*>>?\s*(?:/dev/null|&\d+)')


def strip_safe_redirections(segment):
    """Remove stream-merge/discard redirect clauses from a segment string.

    Per-command checkers (is_start_safe, check_cwd_file_command, ...) tokenize
    a segment and look at positional arguments (last token, first non-flag
    arg). A trailing clause like "2>&1" left in the string reads as a real
    argument — e.g. "start file.html 2>&1" picks "2>&1" as the launch target
    instead of "file.html", failing a check that would otherwise pass. These
    clauses never write to a new location, so they're always safe to drop
    before positional analysis runs.
    """
    masked = re.sub(
        r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'',
        lambda m: '\0' * len(m.group(0)),
        segment,
    )
    spans = [m.span() for m in _SAFE_REDIRECT_CLAUSE.finditer(masked)]
    if not spans:
        return segment
    parts = []
    last = 0
    for start, end in spans:
        parts.append(segment[last:start])
        parts.append(' ')
        last = end
    parts.append(segment[last:])
    return ''.join(parts)


# --------------------------------------------------- unknown commands --------
UNKNOWN_CMD_PATTERN = re.compile(r'^[a-zA-Z][\w.-]*$')


def is_unknown_command_safe(command_name, full_segment):
    """Ask Haiku whether an unrecognized CLI tool is a safe dev tool; remember it."""
    if not UNKNOWN_CMD_PATTERN.match(command_name):
        return False
    log_debug(f"Unknown command '{command_name}', asking AI")
    prompt = f"""Is the CLI tool "{command_name}" a safe, common development or productivity tool?

The full command being run is:
```
{full_segment}
```

Respond with ONLY "SAFE" or "DANGEROUS".

SAFE: Well-known open-source or standard CLI tools for software development, build systems, \
document processing, media conversion, linting, formatting, testing, deployment, presentation, etc. \
Examples: webpack, eslint, prettier, tsc, ffmpeg, pandoc, decktape, mocha, jest, esbuild, vite, etc.

DANGEROUS: System administration tools that modify OS state, network attack tools, disk management \
tools, or anything that could cause irreversible system damage. Examples: dd, mkfs, iptables, fdisk, \
format, etc. Also respond DANGEROUS if you don't recognize the tool at all.

Response (SAFE or DANGEROUS):"""
    verdict, _ = ai.call_ai(prompt)
    if verdict is True:
        add_learned_command(command_name)
        return True
    return False
