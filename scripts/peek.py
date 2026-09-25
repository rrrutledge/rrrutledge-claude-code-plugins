"""Peek at a spawned Claude session's transcript as a compact timeline.

Usage:
  python peek.py <session-guid | short-id prefix | .session receipt | .jsonl path> [--tail N]

Claude records every session as JSONL under <config dir>/projects/<proj>/<session-id>.jsonl.
This searches every account's config dir (~/.claude, each account in session-mgr's accounts.json,
and CLAUDE_CONFIG_DIR) and
accepts a short-id prefix; when several sessions match, it shows the newest and names the others.
This prints a readable timeline (assistant text, tool calls + short results), skipping the
big base64 image blobs, so an orchestrator can monitor how a launched session is doing.
"""
import json
import os
import sys
import glob

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def known_accounts():
    """The account config dirs session-mgr has seen sessions run on (its accounts.json), or []."""
    try:
        with open(os.path.expanduser("~/.claude/session-mgr/accounts.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, str)] if isinstance(data, list) else []


def projects_dirs():
    """Every Claude account's projects dir on this machine: the main ~/.claude, every account
    session-mgr has seen a session run on, and whatever CLAUDE_CONFIG_DIR points at, each only
    when it exists."""
    dirs, seen = [], set()
    for d in ("~/.claude", *known_accounts(), os.environ.get("CLAUDE_CONFIG_DIR")):
        if not d:
            continue
        p = os.path.abspath(os.path.join(os.path.expanduser(d), "projects"))
        if os.path.isdir(p) and os.path.normcase(p) not in seen:
            seen.add(os.path.normcase(p))
            dirs.append(p)
    return dirs


def resolve_jsonl(arg):
    if arg.endswith(".jsonl") and os.path.exists(arg):
        return arg
    if os.path.isfile(arg):
        # A `.session` receipt (or any small file) holding the session id.
        with open(arg, encoding="utf-8") as f:
            arg = f.read().strip()
    # arg is now a full session guid or a short-id prefix of one.
    hits = []
    for projects in projects_dirs():
        hits += glob.glob(os.path.join(projects, "**", arg + "*.jsonl"), recursive=True)
    if not hits:
        return None
    by_id = {}
    for h in hits:
        by_id.setdefault(os.path.basename(h)[:-len(".jsonl")], []).append(h)
    ids = sorted(by_id, key=lambda i: max(os.path.getmtime(p) for p in by_id[i]), reverse=True)
    if len(ids) > 1:
        others = ", ".join(ids[1:6]) + (", ..." if len(ids) > 6 else "")
        print(f"# {len(ids)} sessions match {arg!r}; showing the newest ({ids[0]}). Others: {others}")
    # A spawned session can leave a tiny ai-title stub under one project dir while the real
    # conversation lands under another (e.g. a git worktree vs its main repo). Pick the LARGEST
    # file for the chosen id - the stub is ~120 bytes; the real transcript is the big one.
    return max(by_id[ids[0]], key=os.path.getsize)


def short(s, n=200):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "..."


def text_from_content(content):
    """Pull human-readable text + tool calls from a message content (list or str)."""
    out = []
    if isinstance(content, str):
        return [("text", content)]
    if isinstance(content, list):
        for blk in content:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "text":
                out.append(("text", blk.get("text", "")))
            elif t == "thinking":
                out.append(("think", blk.get("thinking", "")))
            elif t == "tool_use":
                name = blk.get("name", "tool")
                inp = blk.get("input", {})
                # keep tool input compact; never dump base64
                desc = inp.get("command") or inp.get("description") or inp.get("file_path") \
                    or inp.get("prompt") or inp.get("skill") or ""
                out.append(("tool", f"{name}({short(desc, 120)})"))
            elif t == "tool_result":
                c = blk.get("content", "")
                if isinstance(c, list):
                    c = " ".join(b.get("text", "[non-text]") for b in c if isinstance(b, dict))
                out.append(("result", short(c, 160)))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    tail = None
    if "--tail" in sys.argv:
        i = sys.argv.index("--tail")
        tail = int(sys.argv[i + 1])
    jsonl = resolve_jsonl(sys.argv[1])
    if not jsonl:
        print(f"No transcript found for {sys.argv[1]!r}")
        return 1
    print(f"# {jsonl}\n")
    rows = []
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            role = rec.get("type") or msg.get("role") or "?"
            content = msg.get("content", rec.get("content", ""))
            for kind, val in text_from_content(content):
                val = short(val, 300)
                if not val.strip():
                    continue
                tag = {"text": role, "think": "(thinking)", "tool": ">> tool",
                       "result": "   ="}.get(kind, kind)
                rows.append(f"{tag}: {val}")
    if tail:
        rows = rows[-tail:]
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
