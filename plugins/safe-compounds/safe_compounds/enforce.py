"""Enforcement = "rewrite into a form the whole tool chain can validate".

A block here does not mean "this is forbidden". It means the command is in a
form whose safety can't be statically determined by some part of the chain
that has to approve it before it runs — either this hook itself (heredocs,
redirection, inline scripts, env-var expansion, loops/conditionals/
substitutions, ...) or Claude Code's own native permission system, which
re-prompts unconditionally for some paths (the installed plugin cache) no
matter what this hook decides. Either way the fix is the same: ask for an
equivalent form the whole chain can wave through without bugging the user —
typically a `.tmp/` Python script or the Write tool for the hook's own blind
spots, or pointing at a checked-out repo source instead of the plugin cache
for the one Claude Code itself can never let this hook pre-approve.

enforce_bash(command) returns a block-reason string, or None to let the command
proceed to approval. Individual detectors are exposed for unit testing.
"""
import os
import re

from .log import log_debug
from .paths import read_script_file
from .scripts import extract_script_filename
from .shell import ShellTokenizer, first_word, shell_tokenize, split_segments, strip_var_assignment

POWERSHELL_CMDLETS = {
    'Get-ChildItem': 'ls (list) or find (recursive search)',
    'Set-Location': 'cd',
    'Get-Location': 'pwd',
    'Push-Location': 'pushd',
    'Pop-Location': 'popd',
    'New-Item': 'touch (file) or mkdir (directory)',
    'Remove-Item': 'rm',
    'Copy-Item': 'cp',
    'Move-Item': 'mv',
    'Rename-Item': 'mv',
    'Get-Content': 'cat',
    'Set-Content': 'use the Write tool (never redirect in bash)',
    'Add-Content': 'use the Write tool (never redirect in bash)',
    'Clear-Content': 'use the Write tool with empty content',
    'Out-File': 'use the Write tool (never redirect in bash)',
    'Test-Path': 'test -f path (file), test -d path (directory), or [ -e path ] (exists)',
    'Resolve-Path': 'realpath',
    'Get-Item': 'ls or stat',
    'Split-Path': 'dirname (parent) or basename (filename)',
    'Join-Path': 'concatenate path strings directly',
    'Select-Object': 'jq (JSON), awk, or cut (columns)',
    'Where-Object': 'grep',
    'Sort-Object': 'sort',
    'Group-Object': 'sort | uniq -c',
    'Measure-Object': 'wc (lines/words/chars)',
    'Format-Table': 'column -t',
    'Format-List': 'not needed — use raw output directly',
    'Format-Wide': 'not needed — use raw output directly',
    'Select-String': 'grep',
    'Tee-Object': 'tee',
    'Compare-Object': 'diff',
    'Out-String': 'not needed in bash',
    'Out-Null': 'redirect to /dev/null: command > /dev/null 2>&1',
    'Get-Process': 'ps',
    'Stop-Process': 'kill',
    'Start-Process': 'run the command directly',
    'Wait-Process': 'wait',
    'Invoke-Item': 'start',
    'Get-Date': 'date',
    'Write-Host': 'echo',
    'Write-Output': 'echo',
    'Write-Error': 'echo >&2',
    'Write-Warning': 'echo >&2',
    'Write-Information': 'echo',
    'Write-Verbose': 'echo',
    'Write-Debug': 'echo',
    'Clear-Host': 'clear',
    'Get-Variable': 'printenv or env',
    'Set-Variable': 'VAR=value (shell assignment)',
    'Remove-Variable': 'unset VAR',
    'Get-Alias': 'alias',
    'Get-Command': 'which or type',
    'Get-Help': 'man',
    'Get-Member': 'not applicable in bash',
    'Get-Host': 'not needed in bash',
    'Get-Module': 'not applicable in bash',
    'Import-Module': 'not applicable in bash',
    'Invoke-WebRequest': 'curl',
    'Invoke-RestMethod': 'curl (add -s for JSON)',
    'Test-NetConnection': 'ping or nc',
    'ConvertFrom-Json': 'jq',
    'ConvertTo-Json': 'jq',
    'ConvertFrom-Csv': 'awk or write a Python script to .tmp/',
    'ConvertTo-Csv': 'awk or write a Python script to .tmp/',
    'Export-Csv': 'awk or write a Python script to .tmp/',
    'Import-Csv': 'awk or write a Python script to .tmp/',
}

CMD_REWRITE_MAP = {
    'dir': 'ls (add -la for details, grep "^l" to filter symlinks)',
    'type': 'cat',
    'copy': 'cp',
    'move': 'mv',
    'del': 'rm',
    'rd': 'rmdir',
    'md': 'mkdir',
    'echo': 'echo',
    'set': 'printenv or export VAR=value',
    'where': 'which',
    'findstr': 'grep',
    'icacls': 'stat (permissions)',
}

STANDARD_BASH_VARS = {
    'HOME', 'PATH', 'USER', 'SHELL', 'TERM', 'TMPDIR', 'TMP', 'TEMP',
    'PWD', 'OLDPWD', 'EDITOR', 'VISUAL', 'PAGER', 'MANPATH', 'LANG',
    'LC_ALL', 'LC_CTYPE', 'DISPLAY', 'XDG_RUNTIME_DIR', 'XDG_SESSION_TYPE',
    'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'CLAUDE_CWD', 'PROFILE',
}
SIMPLE_VAR_PATTERN = re.compile(r'^([A-Z][A-Z0-9_]{2,})')


def strip_heredocs(command):
    """Remove heredoc bodies. Returns (cleaned, all_safe) where all_safe is
    None if no heredoc, True if every heredoc is single-quoted, else False."""
    result = command
    found_any = False
    all_safe = True
    while True:
        match = re.search(r'<<\s*(["\']?)(\w+)\1', result)
        if not match:
            break
        found_any = True
        if match.group(1) != "'":
            all_safe = False
        marker = match.group(2)
        heredoc_end = match.end()
        end_match = re.search(rf'\n{re.escape(marker)}(?:\n|$)', result[heredoc_end:])
        if not end_match:
            return command, False
        result = result[:match.start()] + result[heredoc_end + end_match.end():]
    return result, (all_safe if found_any else None)


def detect_powershell_herestring(command):
    """Detect PowerShell here-string syntax (@'...'@ or @"..."@) in a Bash
    command. This is not valid Bash syntax: Bash instead reads `@'` as `@`
    plus an ordinary single-quoted string, which closes at the *first*
    embedded quote character rather than at the matching `'@` marker. Any
    apostrophe in the body (a contraction like "letter's", for instance)
    silently truncates the argument and spills the remaining text into the
    command line as further shell input. There's no reliable way to
    statically confirm a given body is free of that character, so this is
    always blocked rather than validated."""
    for m in re.finditer(r'@([\'"])\r?\n', command):
        quote = m.group(1)
        if re.search(rf'\n{quote}@(?:\r?\n|$)', command[m.end():]):
            return True
    return False


def detect_powershell_cmdlet(command):
    for seg in split_segments(command):
        word = first_word(seg.strip())
        if word in POWERSHELL_CMDLETS:
            return word, POWERSHELL_CMDLETS[word]
    return None, None


def detect_inline_script(command):
    for seg in split_segments(command):
        word = first_word(seg.strip())
        if word in ('python', 'python3'):
            if '-c' in seg.strip().split():
                return 'python -c', 'Write a .tmp/script.py using the Write tool, then run: python .tmp/script.py'
        elif word == 'node':
            if '-e' in seg.strip().split():
                return 'node -e', 'Write a .tmp/script.js using the Write tool, then run: node .tmp/script.js'
    return None, None


def detect_output_redirection(command):
    for seg in split_segments(command):
        # Replace quoted spans with a placeholder (not erase them) so a quoted
        # redirect target like `> "some path.txt"` still matches as a target
        # instead of vanishing and hiding the redirection.
        unquoted = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'', 'Q', seg)
        for m in re.finditer(r'(\d*)(>>?)\s*([^\s;&|]+)', unquoted):
            fd = m.group(1)
            target = m.group(3).strip().strip('"\'')
            if fd == '2' and target in ('/dev/null', '&1'):
                continue
            if target == '/dev/null' or target.startswith('&'):
                continue
            return True
    return False


def detect_input_redirection(command):
    for seg in split_segments(command):
        tok = ShellTokenizer(seg)
        while not tok.at_end():
            if tok.consume_escape():
                continue
            if tok.update_quote_state():
                tok.advance()
                continue
            if not tok.in_quotes() and tok.peek() == '<':
                nxt = tok.peek(1)
                if nxt == '<':
                    tok.advance(3 if tok.peek(2) == '<' else 2)
                    continue
                if nxt == '(':
                    tok.advance(2)
                    continue
                prev = tok.pos - 1
                if prev >= 0 and seg[prev].isdigit():
                    tok.advance()
                    continue
                return True
            tok.advance()
    return False


def detect_function_definition(command):
    """Return the defined name if a segment defines a shell function
    (`name() { ... }` or `function name { ... }`), else None. Function
    definitions can't be statically validated — a function can shadow any
    real command, so the hook can't confirm what actually runs."""
    for seg in split_segments(command):
        stripped = seg.strip()
        m = re.match(r'^(\w+)\s*\(\)\s*\{', stripped)
        if m:
            return m.group(1)
        m = re.match(r'^function\s+(\w+)\s*(?:\(\))?\s*\{', stripped)
        if m:
            return m.group(1)
    return None


def detect_cd_cwd_prefix(command):
    cwd = os.environ.get('CLAUDE_CWD', os.getcwd())
    m = re.match(r'^cd\s+("([^"]+)"|\'([^\']+)\'|(\S+))\s*(?:&&|;)', command.strip())
    if not m:
        return False
    target = m.group(2) or m.group(3) or m.group(4)
    return os.path.normpath(os.path.expanduser(target)).lower() == os.path.normpath(cwd).lower()


def detect_cd_compound(command):
    """Return the cd target if the command starts with `cd <dir>` followed by more
    commands (via &&, ;, or newline) and the target differs from the current working directory.

    This function receives the FULL command string before split_segments() splits it.
    Newlines are preserved in the string, so we need to detect both inline compound forms
    (cd && cmd, cd ; cmd) and newline-separated forms (cd\\ncmd).
    """
    cwd = os.environ.get('CLAUDE_CWD', os.getcwd())

    # Match: cd <dir> followed by &&, ;, or newline, then more content
    # Using re.MULTILINE so we can match newlines properly
    m = re.match(
        r'^cd\s+("([^"]+)"|\'([^\']+)\'|(\S+))(?:\s*(?:&&|;)|\s*$)',
        command.strip(),
        re.MULTILINE
    )
    if m:
        target = m.group(2) or m.group(3) or m.group(4)
        # Check if there's actual content after the cd line
        rest = command.strip()[m.end():].strip()
        if rest:  # More commands follow
            if os.path.normpath(os.path.expanduser(target)).lower() != os.path.normpath(cwd).lower():
                return target

    return None


def detect_variable_assignment(command):
    segments = split_segments(command)
    for i, seg in enumerate(segments):
        seg = seg.strip()
        m = re.match(r'^(\w+)=', seg)
        if m:
            if i + 1 < len(segments):
                return m.group(1)
            rest = strip_var_assignment(seg)
            if rest and rest != seg:
                return m.group(1)
    return None


def detect_simple_expansion(command):
    tok = ShellTokenizer(command)
    while not tok.at_end():
        if tok.consume_escape():
            continue
        if tok.update_quote_state():
            tok.advance()
            continue
        if not tok.in_single and tok.peek() == '$':
            nxt = tok.peek(1)
            if nxt in ('(', '{', '?', '#', '@', '*', '!', '-', '$'):
                tok.advance()
                continue
            m = SIMPLE_VAR_PATTERN.match(command[tok.pos + 1:])
            if m and m.group(1) not in STANDARD_BASH_VARS:
                return m.group(1)
        tok.advance()
    return None


def detect_complex_bash(command):
    """Return (is_complex, reason) for patterns that belong in a Python script."""
    tok = ShellTokenizer(command)
    while not tok.at_end():
        if tok.update_quote_state():
            tok.advance()
            continue
        if tok.consume_escape():
            continue
        if not tok.in_single and tok.peek() == '$' and tok.peek(1) == '(':
            return True, "command substitution $()"
        if not tok.in_quotes() and tok.peek() == '`':
            return True, "backtick command substitution"
        tok.advance()

    if re.search(r'\bfor\s+\w+\s+in\b', command):
        return True, "for loop"
    if re.search(r'\bwhile\s+', command):
        return True, "while loop"
    if re.search(r'\buntil\s+', command):
        return True, "until loop"
    if re.search(r'\bif\s+(\[|\[\[|test\b)', command):
        return True, "if conditional"

    for segment in split_segments(command):
        pipes = 0
        st = ShellTokenizer(segment)
        while not st.at_end():
            if st.update_quote_state():
                st.advance()
                continue
            if st.consume_escape():
                continue
            if not st.in_quotes() and st.peek() == '|' and st.peek(1) != '|':
                pipes += 1
            st.advance()
        if pipes >= 3:
            return True, "complex pipeline with 3+ stages"

    for seg in split_segments(command):
        stripped = seg.strip()
        if stripped.startswith('{') and len(stripped) > 1 and stripped[1] in (' ', '\t', '"', "'"):
            return True, "brace command grouping { ... }"

    return False, None


def _subprocess_in_tmp_python(seg, word):
    """Block .tmp/ python scripts that shell out via subprocess."""
    filename = extract_script_filename(seg, word)
    if filename and ('.tmp/' in filename or '\\.tmp\\' in filename):
        content = read_script_file(filename)
        if content and re.search(r'(?<!\w)subprocess[.\s]', content):
            return filename
    return None


PLUGIN_CACHE_PATTERN = re.compile(r'\.claude[/\\]plugins[/\\]cache[/\\]', re.IGNORECASE)

# Commands that legitimately run a plugin's own script straight from the
# cache -- the normal, supported way an installed plugin invokes its tooling.
# Every other command (ls, cat, grep, find, cp, mv, ...) that merely reads or
# operates on a cache path is a case where Claude should redirect at the
# checked-out repo source instead of touching the cache at all.
_PLUGIN_CACHE_INTERPRETER_EXEMPT = {'python', 'python3', 'node'}


def detect_plugin_cache_reference(command):
    """True if a command segment references a path under the installed
    plugin cache (~/.claude/plugins/cache/...) other than running a plugin's
    own script through its interpreter (python/node) -- that's the normal,
    supported way plugins invoke their own tooling and isn't included.
    Everything else (ls, cat, grep, find, cp, mv, ln, touch, chmod, ...)
    against a cache path either hits Claude Code's own "sensitive file"
    confirmation (which this hook can't suppress) or, even where it
    wouldn't, reads stale installed content instead of the checked-out repo
    source. So route around the cache entirely instead of hitting that
    prompt -- or reading the wrong copy -- on every such command against an
    installed plugin."""
    for seg in split_segments(command):
        word = first_word(seg.strip())
        if word and word not in _PLUGIN_CACHE_INTERPRETER_EXEMPT and PLUGIN_CACHE_PATTERN.search(seg):
            return True
    return False


_FIND_LEADING_OPTS = {'-H', '-L', '-P', '-D', '-O'}


def _is_wide_find_root(path):
    """True for `/`, a drive root (`/c`, `C:/`), /proc, /cygdrive, or home
    (`~`, or its expanded Windows or MSYS spelling)."""
    p = path.replace('\\', '/').rstrip('/').lower()
    if p in ('', '~', '$home', '/proc', '/cygdrive') or re.fullmatch(r'/[a-z]|[a-z]:', p):
        return True
    home = os.path.expanduser('~').replace('\\', '/').rstrip('/').lower()
    msys_home = re.sub(r'^([a-z]):', r'/\1', home)
    return p in (home, msys_home)


def detect_unbounded_wide_find(command):
    """True if a `find` segment starts at the filesystem root, a drive root,
    or the home directory and carries no -maxdepth. In Git Bash `/` also
    holds /proc, which exposes the whole Windows registry (three times over)
    and loops back into every drive, so such a walk runs for hours -- and
    when the Bash tool times out or a downstream `head` exits, find.exe is
    orphaned rather than killed and keeps a core pinned long after the
    session ends. A bounded find (a repo path, or any -maxdepth) is left
    alone."""
    for seg in split_segments(command):
        tokens = shell_tokenize(seg.strip())
        if not tokens or tokens[0] != 'find' or '-maxdepth' in tokens:
            continue
        i = 1
        while i < len(tokens) and (tokens[i] in _FIND_LEADING_OPTS or tokens[i].startswith('-O')):
            i += 2 if tokens[i] == '-D' else 1
        roots = []
        while i < len(tokens) and not tokens[i].startswith(('-', '(', '!', '\\(')):
            roots.append(tokens[i])
            i += 1
        if any(_is_wide_find_root(r) for r in roots):
            return True
    return False


GH_API_CONTENTS_PATTERN = re.compile(r'/?repos/[^\s/]+/[^\s/]+/contents/')


def detect_gh_api_contents_write(command):
    """True if a `gh api` call writes (PUT or DELETE) to the GitHub Contents
    API (repos/.../contents/...) -- editing a file's bytes directly through
    the API instead of cloning the repo and using normal file tools. Per
    CLAUDE.md ("Writing files to other repos: clone, don't API"), this is
    always the wrong mechanism, so it's blocked outright rather than left to
    the approve-layer's reversibility check -- there's no version of this
    endpoint call that should go through, only a different way to make the
    same edit."""
    for seg in split_segments(command):
        tokens = shell_tokenize(seg.strip())
        if len(tokens) < 3 or tokens[0] != 'gh' or tokens[1] != 'api':
            continue
        method = 'GET'
        api_url = ''
        i = 2
        while i < len(tokens):
            if tokens[i] in ('--method', '-X') and i + 1 < len(tokens):
                method = tokens[i + 1].upper()
                i += 2
                continue
            if not tokens[i].startswith('-') and not api_url:
                api_url = tokens[i]
            i += 1
        if method in ('PUT', 'DELETE') and GH_API_CONTENTS_PATTERN.search(api_url):
            return True
    return False


_TRELLO_API_HOST = 'api.trello.com'
_CURL_BODY_FLAGS = {
    '-d', '--data', '--data-raw', '--data-binary', '--data-urlencode',
    '-F', '--form', '--form-string', '-T', '--upload-file', '--json',
}


def detect_raw_trello_write(command):
    """True if a curl segment writes to the Trello REST API (api.trello.com) --
    a POST/PUT/DELETE/PATCH, or any request carrying a body that defaults the
    method off GET. A raw write to Trello is always the wrong mechanism: every
    card/comment/label/checklist/list mutation goes through the `trello` skill's
    trello_utils.py, which owns the credentials, base URL, bounded timeout, and
    read-after-write verification. So it's blocked outright with a pointer to
    that path, the same way a `gh api` Contents write is. A plain GET read to
    api.trello.com is left alone -- lower stakes, and it still auto-approves."""
    for seg in split_segments(command):
        tokens = shell_tokenize(seg.strip())
        if not tokens or tokens[0] != 'curl':
            continue
        if not any(_TRELLO_API_HOST in t for t in tokens):
            continue
        method = None
        has_body = False
        for i, t in enumerate(tokens[1:], 1):
            if t in ('-X', '--request') and i + 1 < len(tokens):
                method = tokens[i + 1].upper()
            elif t.startswith('-X') and len(t) > 2:
                method = t[2:].upper()
            elif t == '-d' or t.startswith('--data') or t in _CURL_BODY_FLAGS:
                has_body = True
        if method in ('POST', 'PUT', 'DELETE', 'PATCH') or (has_body and method != 'GET'):
            return True
    return False


def enforce_bash(command):
    """Return a block-reason string for `command`, or None to allow it to
    proceed to approval. Mirrors the legacy block ordering exactly."""
    _, heredocs_found = strip_heredocs(command)
    if heredocs_found is not None:
        return ('BLOCKED: Heredoc syntax (<< EOF) detected. Per CLAUDE.md rules, never use heredocs. '
                'Use the Write tool to create the file, then run it separately.')

    if detect_powershell_herestring(command):
        return ('BLOCKED: PowerShell here-string syntax (@\'...\'@ or @"..."@) detected. This is not '
                'valid Bash syntax -- Bash reads it as an ordinary single-quoted string that '
                'terminates at the first embedded quote character (e.g. an apostrophe in a '
                'contraction), silently truncating the argument and spilling the rest of the text '
                'into the command line as further shell input. Write the multi-line text to a file '
                'with the Write tool instead, then pass it via that file (e.g. '
                '`git commit -F .tmp/commit-msg.txt`).')

    if detect_gh_api_contents_write(command):
        return ('BLOCKED: "gh api" is writing directly to the GitHub Contents API '
                '(repos/.../contents/...) with PUT/DELETE. Per CLAUDE.md ("Writing files to '
                'other repos: clone, don\'t API"), edit files in another repo by cloning (or '
                'sparse-checking-out) it into .tmp/, editing the file with the Write/Edit tool, '
                'then `git commit` and `git push` the branch -- never by hand-building a '
                'Contents API payload. Rewrite the command that way.')

    if detect_raw_trello_write(command):
        return ('BLOCKED: This curl writes to the Trello REST API (api.trello.com). Every Trello '
                'mutation -- card, comment, label, checklist, list -- goes through the `trello` '
                'skill\'s trello_utils.py, which owns the credentials, base URL, bounded timeout, '
                'and read-after-write verification. Write a .tmp/ Python script that imports '
                'trello_utils (get_trello_session plus the typed wrapper for what you\'re doing, or '
                'trello_request for anything without one -- e.g. add_comment for a card comment) and '
                'run it with `python .tmp/script.py`. See the `trello` skill\'s SKILL.md for the '
                'import pattern.')

    if detect_plugin_cache_reference(command):
        return ('BLOCKED: Command references a path under the installed plugin cache '
                '(~/.claude/plugins/cache/...). Claude Code always shows its own "sensitive '
                'file" confirmation for that path, no matter what this hook decides -- it '
                'can\'t be suppressed here. If the plugin\'s source repo is checked out '
                'locally, point the command at that checkout instead (e.g. '
                '~/Dev/<org>/<repo>/plugins/<name>/...) -- ordinary repo content isn\'t '
                'gated. Otherwise, read the file with the Read tool directly instead of '
                'Bash -- that triggers just the one native prompt, without also needing '
                'this Bash command approved.')

    if detect_unbounded_wide_find(command):
        return ('BLOCKED: This find walks the whole filesystem (/, a drive root, or home) with no '
                '-maxdepth, which in Git Bash runs for hours and outlives the session. To locate a plugin '
                'script, take its path from the resolved skill-directory list in your drainer seed '
                'prompt, or from the plugin\'s checked-out source under ~/Dev. Otherwise use the '
                'Glob tool on a specific directory, or point find at that directory (or add '
                '-maxdepth).')

    cmdlet, alternative = detect_powershell_cmdlet(command)
    if cmdlet:
        return (f'BLOCKED: PowerShell cmdlet "{cmdlet}" used in Bash command. '
                f'Use bash equivalent instead: {alternative}. '
                'Always use bash commands in the Bash tool, never PowerShell cmdlets.')

    func_name = detect_function_definition(command)
    if func_name:
        if func_name == 'cd':
            return ('BLOCKED: Shell function definition "cd() { ... }" detected. This is usually added to '
                     'stop an embedded cd from changing the working directory, but the Bash tool already runs '
                     'every command in the correct working directory — there is no cd to guard against. Drop '
                     'the "cd() { ... };" prefix entirely and run the rest of the command directly.')
        return (f'BLOCKED: Shell function definition ("{func_name}() {{ ... }}") detected. Function definitions '
                'can\'t be statically validated — a function can silently shadow a real command, so the hook '
                'can\'t confirm what actually runs. Remove the function definition and call the command '
                'directly, or move the logic into a .tmp/ script (Python/Node) if it\'s genuinely needed.')

    for seg in split_segments(command):
        word = first_word(seg.strip())
        if word == 'powershell':
            from .commands import is_powershell_safe
            if not is_powershell_safe(seg):
                return ('BLOCKED: powershell command in Bash. Rewrite using bash commands or a Python '
                        'script in .tmp/. See ~/.claude/CLAUDE.md for guidelines.')
        if word == 'cmd':
            tokens = shell_tokenize(seg.strip())
            if any(t.lower() in ('/c', '//c') for t in tokens[1:]):
                inner = ''
                for i, t in enumerate(tokens[1:], 1):
                    if t.lower() in ('/c', '//c') and i + 1 < len(tokens):
                        inner = tokens[i + 1].strip().split()[0].lower() if tokens[i + 1].strip() else ''
                        break
                rewrite = CMD_REWRITE_MAP.get(inner, '')
                hint = (f' Use "{rewrite}" instead.' if rewrite
                        else (' Call the Windows utility (e.g. fsutil, icacls) directly in Git Bash without cmd //c.'
                              if inner else ''))
                return ('BLOCKED: cmd /c or cmd //c detected. Windows utilities run directly in Git Bash — '
                        'no cmd wrapper needed.' + hint +
                        ' For bash equivalents of Windows commands see the PowerShell cmdlet map in the hook.')
        if word == 'sed' and re.search(r'\bsed\b.*\s-i\b', seg):
            return ('BLOCKED: "sed -i" destroys files on Windows/MSYS (especially symlinks). '
                    'Use the Edit tool instead.')
        if word in ('python', 'python3'):
            filename = _subprocess_in_tmp_python(seg, word)
            if filename:
                return (f'BLOCKED: Script "{filename}" uses subprocess. Per CLAUDE.md, .tmp/ scripts must use '
                        'client libraries (urllib.request, requests, etc.) instead of subprocess. Rewrite the '
                        'script to make API calls directly with urllib.request or requests, then re-run it.')

    inline_flag, inline_alternative = detect_inline_script(command)
    if inline_flag:
        return (f'BLOCKED: Inline script flag "{inline_flag}" can\'t be validated inline. '
                f'{inline_alternative}')

    if detect_output_redirection(command):
        return ('BLOCKED: Output redirection (> or >>) detected. Per CLAUDE.md rules, never use output '
                'redirection. Use the Write tool to create files instead.')

    if detect_input_redirection(command):
        return ('BLOCKED: Input redirection (<) detected. Per CLAUDE.md rules, do not use input redirection. '
                'Use "cat file | command" instead of "command < file".')

    if detect_cd_cwd_prefix(command):
        return ('BLOCKED: Redundant "cd <cwd> &&" prefix detected. The Bash tool already sets the working '
                'directory to the project root. Remove the cd prefix and run the command directly.')

    cd_target = detect_cd_compound(command)
    if cd_target:
        return (f'BLOCKED: "cd {cd_target}" followed by more commands. The hook cannot validate scripts or '
                'file operations in another directory — it needs to inspect files relative to the current '
                'working directory.\n\n'
                'NEVER prepend "cd <dir>" to Bash commands (with &&, ;, or newlines).\n\n'
                'Instead:\n\n'
                '  • For scripts: Use absolute or relative paths from the current directory:\n'
                f'      python {cd_target}/script.py\n'
                f'      bash {cd_target}/script.sh\n\n'
                '  • For git: Use -C to specify the repository:\n'
                f'      git -C {cd_target} status\n'
                f'      git -C {cd_target} checkout origin/main -- path/to/file\n\n'
                '  • For file operations: Use paths relative to the current directory.\n\n'
                'The Bash tool\'s working directory is already set correctly. Just run commands directly.')

    assigned = detect_variable_assignment(command)
    if assigned:
        return (f'BLOCKED: Variable assignment "{assigned}=..." followed by more commands. Per CLAUDE.md rules, '
                'commands with variable assignments must be written as Python scripts to .tmp/ instead. This '
                'makes them analyzable by the hook and easier to debug. Write a Python script that sets the '
                'variable and runs the commands, then run: python .tmp/script_name.py')

    var_name = detect_simple_expansion(command)
    if var_name:
        return (f'BLOCKED: Simple environment variable expansion "${var_name}" detected. Per CLAUDE.md rules, '
                'commands that depend on env vars should be written as Python scripts so they read variables '
                f'via os.environ. Write a Python script to .tmp/ that uses os.environ.get("{var_name}") and '
                'makes the call directly, then run: python .tmp/script_name.py')

    is_complex, reason = detect_complex_bash(command)
    if is_complex:
        return (f'BLOCKED: Command contains {reason}, whose safety cannot be statically validated. Rewrite the '
                'logic as a Python script in .tmp/ (which the hook can read and check) and run it with: '
                'python .tmp/script_name.py')

    return None
