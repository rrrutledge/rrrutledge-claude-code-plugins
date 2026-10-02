"""Shell command parsing: tokenizing, segment splitting, command-name extraction.

This is the one place shell syntax is walked. Everything that needs to reason
about quoting, operators, or substitutions uses ShellTokenizer or the helpers
here rather than ad-hoc string scanning.
"""
import os
import re
import shlex

# Sentinel returned by first_word for a pure variable assignment (no command).
ASSIGNMENT_ONLY = '__assignment__'


class ShellTokenizer:
    """Stateful character scanner with POSIX quote/escape tracking."""

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.in_single = False
        self.in_double = False

    def peek(self, offset=0):
        idx = self.pos + offset
        return self.text[idx] if idx < len(self.text) else None

    def advance(self, count=1):
        self.pos = min(self.pos + count, len(self.text))

    def at_end(self):
        return self.pos >= len(self.text)

    def consume_escape(self):
        """If positioned on a backslash escape (outside single quotes), consume
        and return the two-char sequence; otherwise return None."""
        if not self.in_single and self.peek() == '\\' and self.peek(1):
            escaped = self.text[self.pos:self.pos + 2]
            self.advance(2)
            return escaped
        return None

    def update_quote_state(self):
        """Toggle quote state if positioned on an active quote char. Returns
        True if the current char was a quote delimiter."""
        ch = self.peek()
        if ch == "'" and not self.in_double:
            self.in_single = not self.in_single
            return True
        if ch == '"' and not self.in_single:
            self.in_double = not self.in_double
            return True
        return False

    def in_quotes(self):
        return self.in_single or self.in_double


def shell_tokenize(text):
    """Split into tokens using POSIX shell rules, falling back to whitespace."""
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


# A bare (unquoted) Windows drive-letter path, e.g. C:\Users\...
_WINDOWS_DRIVE_PATH = re.compile(r'^[A-Za-z]:\\')


def has_unquoted_windows_drive_path(segment):
    """True if `segment` contains a Windows drive-letter path outside of any
    quotes. POSIX word-splitting (both real shells and shlex) only preserves a
    backslash literally inside double quotes; unquoted, `\\U` collapses to `U`.
    A command built with an unquoted `C:\\Users\\...` path therefore has its
    path silently mangled by the shell before the program ever sees it — this
    flags that hazard so callers can surface it instead of failing silently.
    """
    tok = ShellTokenizer(segment)
    while not tok.at_end():
        if tok.consume_escape():
            continue
        if tok.update_quote_state():
            tok.advance()
            continue
        if not tok.in_quotes() and _WINDOWS_DRIVE_PATH.match(segment[tok.pos:]):
            return True
        tok.advance()
    return False


def split_segments(cmd):
    """Split a compound command into segments on &&, ||, ;, |, and bare newline
    operators, only when those operators appear outside quotes -- matching real
    bash, where an unquoted newline terminates a statement exactly like `;`.
    A backslash-newline line continuation is consumed whole by consume_escape()
    before reaching this check, so it stays part of the current segment instead
    of splitting -- exactly like a `\\<newline>`-continued command runs as one
    statement in bash."""
    segments = []
    current = []
    tok = ShellTokenizer(cmd)

    while not tok.at_end():
        escaped = tok.consume_escape()
        if escaped:
            current.append(escaped)
            continue

        if tok.update_quote_state():
            current.append(tok.peek())
            tok.advance()
            continue

        if not tok.in_quotes():
            two = cmd[tok.pos:tok.pos + 2]
            if two in ('&&', '||'):
                segments.append(''.join(current))
                current = []
                tok.advance(2)
                continue
            if tok.peek() in (';', '|', '\n'):
                segments.append(''.join(current))
                current = []
                tok.advance()
                continue

        current.append(tok.peek())
        tok.advance()

    segments.append(''.join(current))
    return segments


def extract_substitutions(cmd):
    """Return the inner command strings of all $(...) substitutions, respecting
    nesting and single-quote suppression."""
    subs = []
    tok = ShellTokenizer(cmd)

    while not tok.at_end():
        if tok.consume_escape():
            continue
        if tok.update_quote_state():
            tok.advance()
            continue

        if not tok.in_single and tok.peek() == '$' and tok.peek(1) == '(':
            depth = 1
            start = tok.pos + 2
            inner = ShellTokenizer(cmd[start:])
            while not inner.at_end() and depth > 0:
                if inner.consume_escape():
                    continue
                if inner.update_quote_state():
                    inner.advance()
                    continue
                if not inner.in_quotes():
                    if inner.peek() == '$' and inner.peek(1) == '(':
                        depth += 1
                        inner.advance()
                    elif inner.peek() == ')':
                        depth -= 1
                        if depth == 0:
                            subs.append(cmd[start:start + inner.pos])
                            break
                inner.advance()
            tok.pos = start + inner.pos + 1
            continue

        tok.advance()

    return subs


def strip_var_assignment(segment):
    """Strip a leading VAR=value assignment (handling $(), quotes, escapes) and
    return the remainder, or the original segment if there is no assignment."""
    m = re.match(r'^(\w+)=', segment)
    if not m:
        return segment
    i = m.end()
    in_sq = in_dq = False
    depth = 0
    while i < len(segment):
        c = segment[i]
        if c == '\\' and i + 1 < len(segment) and not in_sq:
            i += 2
            continue
        if c == "'" and not in_dq and depth == 0:
            in_sq = not in_sq
        elif c == '"' and not in_sq and depth == 0:
            in_dq = not in_dq
        elif not in_sq:
            if segment[i:i + 2] == '$(':
                depth += 1
                i += 1
            elif c == ')' and depth > 0:
                depth -= 1
        if not in_sq and not in_dq and depth == 0 and c in (' ', '\t'):
            break
        i += 1
    return segment[i:].strip()


def first_word(segment):
    """Return the executable name from a segment, ignoring leading env-var
    assignments and redirections, and reducing paths to their basename."""
    segment = segment.strip()
    while re.match(r'^\w+=', segment):
        rest = strip_var_assignment(segment)
        if rest == segment:
            break
        segment = rest
    if not segment:
        return ASSIGNMENT_ONLY
    segment = re.sub(r'^\d*[<>]+\S*\s*', '', segment).strip()
    # A quoted executable path may contain spaces ("/c/Program Files/.../x.exe"),
    # so take the whole quoted string rather than the text up to the first space.
    quoted = re.match(r'^"([^"]*)"|^\'([^\']*)\'', segment)
    if quoted:
        cmd = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
    else:
        words = segment.split()
        cmd = words[0] if words else ''
    cmd = cmd.strip('"\'')
    if '/' in cmd or '\\' in cmd:
        cmd = os.path.basename(cmd.replace('\\', '/'))
    if cmd.endswith('.exe'):
        cmd = cmd[:-4]
    cmd = cmd.lstrip('(')
    return cmd


def get_subcommands(seg, skip=1):
    """Return non-flag tokens after the first `skip` tokens (e.g. the args of a
    subcommand-style CLI)."""
    tokens = shell_tokenize(seg)
    return [t for t in tokens[skip:] if not t.startswith('-')]
