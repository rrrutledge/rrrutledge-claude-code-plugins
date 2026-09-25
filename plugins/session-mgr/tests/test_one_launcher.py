"""Guard: session-mgr's bg_session.py is the one place a Claude session is launched.

Walks the repo's plugins/ and scripts/ trees and fails when any code file other than bg_session.py
starts a session itself - a `claude --bg` launch, a claude argv whose first argument makes it a
session rather than a one-shot or management call, or a Windows Terminal tab (wt.exe, `wt -w`,
new-tab) or the retired tab launchers. Comments and Python docstrings are skipped, since describing
a launch isn't one. Tests dirs are skipped: they hold recorded commands and stubs, not launches.

It also scans .md docs for instructions to launch through a terminal tab (wt.exe, spawn-tab.cmd,
launch-session.ps1), with no allowlist - only changelogs, which record past releases, are skipped.
Run directly: python plugins/session-mgr/tests/test_one_launcher.py
"""
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TREES = ("plugins", "scripts")
CODE_EXTS = (".py", ".ps1", ".cmd", ".bat", ".js", ".sh")
SKIP_DIRS = {"tests", "test", "__pycache__", "node_modules", ".git"}

# Files allowed to start a session, by repo-relative path, each with the reason.
ALLOWED = {
    # The launcher itself - every other launch goes through it.
    "plugins/session-mgr/skills/resume-sessions/scripts/bg_session.py",
}

# A claude argv's first argument that makes it something other than a session launch: a one-shot
# print (`-p`/`--print`), reading or stopping sessions (`agents`, `stop`, `attach`, `logs`), or
# managing the install (`plugin`, `mcp`, `update`, `--version`).
NON_SESSION_FIRST_ARGS = {"-p", "--print", "agents", "stop", "attach", "logs", "plugin", "mcp",
                          "update", "--version", "-v", "doctor"}

CODE_PATTERNS = [
    ("`--bg` argv literal", re.compile(r"""["']--bg["']""")),
    ("claude --bg command", re.compile(r"(?<![`\w])claude(?:\.exe)?[\"']?\s+--bg\b")),
    ("wt.exe", re.compile(r"\bwt\.exe\b", re.I)),
    ("wt -w", re.compile(r"(?<![\w.-])wt\s+-w\b")),
    ("new-tab", re.compile(r"\bnew-tab\b")),
    ("retired tab launcher", re.compile(r"spawn-tab\.cmd|launch-session\.ps1", re.I)),
]
ARGV_RE = re.compile(r"""\[\s*(?:["']claude(?:\.exe)?["']|claude\w*)\s*,\s*["']([^"']+)["']""")

DOC_PATTERN = re.compile(r"wt\.exe|spawn-tab\.cmd|launch-session\.ps1", re.I)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f"\n{detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def walk(exts):
    for tree in TREES:
        for root, dirs, files in os.walk(os.path.join(REPO, tree)):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.lower().endswith(exts):
                    path = os.path.join(root, name)
                    yield os.path.relpath(path, REPO).replace("\\", "/"), path


def python_code_lines(text):
    """(line number, text) of a Python file's code with comments and docstrings removed: every
    string token that isn't a statement of its own, plus every non-string token, joined per line."""
    lines = {}
    prev = tokenize.NEWLINE
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return list(enumerate(text.splitlines(), 1))
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            continue
        nxt = tokens[i + 1].type if i + 1 < len(tokens) else tokenize.ENDMARKER
        is_docstring = (tok.type == tokenize.STRING
                        and prev in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
                                     tokenize.ENCODING)
                        and nxt in (tokenize.NEWLINE, tokenize.ENDMARKER))
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok.type
        if is_docstring or tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            continue
        lines.setdefault(tok.start[0], []).append(tok.string)
    return sorted((n, " ".join(parts)) for n, parts in lines.items())


def script_code_lines(text):
    """(line number, text) of a shell/PowerShell/JS/batch file's lines that aren't comments."""
    out, in_block = [], False
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if in_block:
            in_block = "#>" not in s
            continue
        if s.startswith("<#"):
            in_block = "#>" not in s
            continue
        if not s or s.startswith(("#", "//", "::")) or s.lower().startswith("rem "):
            continue
        out.append((n, line))
    return out


def session_launch_hits(rel, text):
    lines = python_code_lines(text) if rel.endswith(".py") else script_code_lines(text)
    hits = []
    for n, line in lines:
        for label, pattern in CODE_PATTERNS:
            if pattern.search(line):
                hits.append(f"{rel}:{n}: {label}: {line.strip()[:140]}")
        for m in ARGV_RE.finditer(line):
            if m.group(1) not in NON_SESSION_FIRST_ARGS:
                hits.append(f"{rel}:{n}: claude argv starting a session ({m.group(1)}): {line.strip()[:140]}")
    return hits


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def test_self_check():
    print("test: the scanner itself catches launches and ignores mentions")
    launch = 'import subprocess\nsubprocess.run(["claude", "--bg", "x"])\n'
    shell = 'subprocess.run("claude --bg --remote-control", shell=True)\n'
    tab = 'cmd = f\'"{wt}" -w drainer new-tab -d "{cwd}"\'\n'
    interactive = 'subprocess.run(["claude", "--resume", guid])\n'
    mention = '"""Launches a `claude --bg` session; never wt.exe."""\n# claude --bg here too\nx = 1\n'
    oneshot = 'subprocess.run([claude, "-p", "--model", m])\nsubprocess.run(["claude", "stop", s])\n'
    check("argv --bg caught", bool(session_launch_hits("a.py", launch)))
    check("shell claude --bg caught", bool(session_launch_hits("a.py", shell)))
    check("wt new-tab caught", bool(session_launch_hits("a.py", tab)))
    check("interactive claude argv caught", bool(session_launch_hits("a.py", interactive)))
    check("docstring/comment mention ignored", not session_launch_hits("a.py", mention),
          session_launch_hits("a.py", mention))
    check("one-shot and stop ignored", not session_launch_hits("a.py", oneshot),
          session_launch_hits("a.py", oneshot))
    check("ps1 launch caught", bool(session_launch_hits("a.ps1", "& claude --bg -- seed\n")))
    check("ps1 comment ignored", not session_launch_hits("a.ps1", "# wt.exe new-tab\n<#\nwt -w 0\n#>\n"))


def test_only_bg_session_launches():
    print("test: no code file other than bg_session.py starts a session")
    hits = []
    for rel, path in walk(CODE_EXTS):
        if rel in ALLOWED:
            continue
        hits.extend(session_launch_hits(rel, read(path)))
    check("no other launcher", not hits, "\n".join("    " + h for h in hits))


def test_launcher_still_launches():
    print("test: bg_session.py still holds the launch (the guard isn't vacuous)")
    rel = next(iter(ALLOWED))
    check("bg_session.py has the --bg argv", bool(session_launch_hits(rel, read(os.path.join(REPO, rel)))))


def test_docs_have_no_tab_launch():
    print("test: no .md doc tells a session to launch through a terminal tab")
    hits = []
    for rel, path in walk((".md",)):
        # A changelog records what past releases did, so it names retired launchers by design.
        if os.path.basename(rel).upper() == "CHANGELOG.MD":
            continue
        for n, line in enumerate(read(path).splitlines(), 1):
            if DOC_PATTERN.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:140]}")
    check("no wt.exe / spawn-tab.cmd / launch-session.ps1 in docs", not hits,
          "\n".join("    " + h for h in hits))


if __name__ == "__main__":
    test_self_check()
    test_only_bg_session_launches()
    test_launcher_still_launches()
    test_docs_have_no_tab_launch()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
