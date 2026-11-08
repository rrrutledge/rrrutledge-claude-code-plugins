"""The set of trusted command names.

Built from a hardcoded base (TRUSTED_COMMANDS + SHELL_BUILTINS), the user's
settings.json allow list, and the machine-local learned store. Tests pin the
set exactly via SAFE_COMPOUNDS_TRUSTED_JSON.
"""
import json
import os
import re

from .learned import learned_commands
from .shell import ASSIGNMENT_ONLY

SHELL_BUILTINS = {
    '[', '[[', 'basename', 'dirname', 'printf', 'read', 'true', 'false',
}

TRUSTED_COMMANDS = {
    'awk',
    'base32', 'base64', 'basename', 'bc', 'bq',
    'cal', 'cat', 'cd', 'claude', 'clip', 'cmp', 'column', 'comm', 'command', 'credential', 'cut', 'cygpath',
    'date', 'decktape', 'df', 'diff', 'dirname', 'docker', 'du',
    'echo', 'env', 'exec', 'expand', 'explorer', 'export', 'expr',
    'factor', 'file', 'find', 'findstr', 'fmt', 'fold', 'free', 'fsutil',
    'gcloud', 'getconf', 'gh', 'git', 'grep', 'groups', 'grype',
    'head', 'hexdump', 'hostname',
    'iconv', 'id', 'identify',
    'jobs', 'join', 'jq',
    'kill', 'kubectl',
    'less', 'locale', 'logname', 'look', 'ls', 'lsof',
    'md5sum', 'mkdir', 'more',
    'nc', 'netstat', 'nl', 'npm', 'nproc', 'numfmt', 'nvm',
    'od',
    'pandoc', 'paste', 'pg_isready', 'pip', 'pnpm', 'printenv', 'ps', 'psql', 'pwd',
    'Read', 'readlink', 'realpath', 'rev', 'rm', 'rmdir',
    'seq', 'sha1sum', 'sha256sum', 'sha512sum', 'sleep', 'snyk', 'sort', 'source', 'stat', 'strings', 'systeminfo',
    'tac', 'tail', 'tasklist', 'tee', 'test', 'timeout', 'touch', 'tr', 'tree', 'trivy', 'tty', 'type',
    'uname', 'unexpand', 'uniq', 'unzip', 'uptime', 'users',
    'w', 'wait', 'wc', 'where', 'which', 'who', 'whoami',
    'xargs', 'xxd',
    'yarn', 'yes', 'yt-dlp',
}


def load_trusted_from_settings():
    """Command names appearing in Bash(...) allow rules across settings files."""
    trusted = set()
    for name in ('settings.json', 'settings.local.json'):
        path = os.path.join(os.path.expanduser('~/.claude'), name)
        try:
            with open(path, encoding='utf-8') as f:
                settings = json.load(f)
            for rule in settings.get('permissions', {}).get('allow', []):
                m = re.match(r'^Bash\(([a-zA-Z][\w-]*)', rule)
                if m:
                    trusted.add(m.group(1))
        except Exception:
            pass
    return trusted


def get_trusted():
    """Return the full trusted command set (plus the assignment sentinel).

    When SAFE_COMPOUNDS_TRUSTED_JSON points at a JSON list, that list is used
    verbatim instead of the live sources - this is how tests pin behavior.
    """
    override = os.environ.get('SAFE_COMPOUNDS_TRUSTED_JSON')
    if override:
        try:
            with open(override, encoding='utf-8') as f:
                base = set(json.load(f))
        except Exception:
            base = set()
        return base | {ASSIGNMENT_ONLY}
    from . import config
    configured = set(config.get_config().get('trusted_commands', []))
    return (SHELL_BUILTINS | TRUSTED_COMMANDS | configured
            | load_trusted_from_settings() | learned_commands() | {ASSIGNMENT_ONLY})
