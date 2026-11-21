"""Count which tools, MCP servers, and skills Claude Code sessions actually call.

Scans the session transcripts under one or more Claude config dirs
(default: ~/.claude and ~/.claude-background) inside a time window, and
reports call counts per built-in tool, per MCP server and tool, per skill,
per typed slash command, and per subagent type launched, plus sessions and
API turns, all split by the kind of session that made the call.

Session kinds, decided from each transcript's path and opening lines:
  drainer-worker   first prompt points at a drainer seed (.tmp/drainer/seeds)
  drainer-main     the drainer's own long-running session (drainer-main-worktree)
  handoff          first prompt is "Resume from the handoff at ..."
  headless         entrypoint is sdk-* (a `claude -p` call)
  interactive      everything else
  subagent         a subagents/agent-*.jsonl transcript (its tool set comes
                   from the agent type)

Per-turn cost matters more than call count, so the report also counts the API
turns (unique assistant message ids) each session kind took in the window:
a tool's fixed-prompt tokens are paid once per turn whether it is called or not.

Usage:
  python scan_tool_usage.py [--days 60] [--config-dir DIR ...] [--json OUT]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KINDS = ["interactive", "handoff", "drainer-worker", "drainer-main", "headless", "subagent"]
COMMAND_RE = re.compile(r"<command-name>/?([^<\s]+)</command-name>")


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def first_prompt_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return None


def classify(path, first_prompt, entrypoint):
    if "subagents" in path.parts:
        return "subagent"
    if "drainer-main-worktree" in str(path.parent.name):
        return "drainer-main"
    text = (first_prompt or "").replace("\\", "/")
    if ".tmp/drainer/seeds" in text or "/drainer/seeds/" in text:
        return "drainer-worker"
    if text.lstrip().startswith("Resume from the handoff"):
        return "handoff"
    if entrypoint and entrypoint.startswith("sdk"):
        return "headless"
    return "interactive"


TS_RE = re.compile(r'"timestamp":"([^"]+)"')
MSG_ID_RE = re.compile(r'"id":"(msg_[^"]+)"')


def find_kind(path):
    """Read only the head of a transcript to learn its session kind."""
    first_prompt = None
    entrypoint = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > 200:
                break
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            entrypoint = entrypoint or rec.get("entrypoint")
            if rec.get("type") == "user":
                text = first_prompt_text((rec.get("message") or {}).get("content"))
                if text and not text.startswith("<local-command") and not text.startswith("<system-reminder"):
                    first_prompt = text
                    break
    return classify(path, first_prompt, entrypoint)


def scan_file(path, cutoff, stats):
    """Stream a transcript; fully parse only the lines that can carry a tool call or slash command."""
    kind = find_kind(path)
    session = path.stem
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            is_assistant = '"type":"assistant"' in line
            if not is_assistant and "<command-name>" not in line:
                continue
            ts = TS_RE.search(line)
            if not ts or ts.group(1)[:19] < cutoff_iso:
                continue
            stats["sessions"][kind].add(session)
            if is_assistant and '"type":"tool_use"' not in line:
                mid = MSG_ID_RE.search(line)
                if mid:
                    stats["turns"][kind].add(mid.group(1))
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if rec.get("type") == "assistant":
                if msg.get("id"):
                    stats["turns"][kind].add(msg["id"])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "?")
                    inp = block.get("input") or {}
                    stats["tool_calls"][name][kind] += 1
                    stats["tool_sessions"][name].add(session)
                    if name == "Skill" and inp.get("skill"):
                        stats["skill_calls"][inp["skill"]][kind] += 1
                    if name in ("Agent", "Task") and inp.get("subagent_type"):
                        stats["agent_types"][inp["subagent_type"]][kind] += 1
            elif rec.get("type") == "user":
                text = content if isinstance(content, str) else first_prompt_text(content) or ""
                for cmd in COMMAND_RE.findall(text):
                    stats["slash_commands"][cmd][kind] += 1


def mcp_split(name):
    _, server, tool = name.split("__", 2)
    return server, tool


def build_report(stats, days, config_dirs):
    turns = {k: len(stats["turns"][k]) for k in KINDS}
    sessions = {k: len(stats["sessions"][k]) for k in KINDS}

    builtin, mcp_servers, mcp_tools = {}, defaultdict(Counter), {}
    for name, by_kind in stats["tool_calls"].items():
        row = {"total": sum(by_kind.values()), "by_kind": dict(by_kind), "sessions": len(stats["tool_sessions"][name])}
        if name.startswith("mcp__"):
            server, tool = mcp_split(name)
            mcp_tools[name] = row
            for k, v in by_kind.items():
                mcp_servers[server][k] += v
        else:
            builtin[name] = row

    def counter_rows(d):
        return {k: {"total": sum(v.values()), "by_kind": dict(v)} for k, v in d.items()}

    return {
        "window_days": days,
        "config_dirs": [str(c) for c in config_dirs],
        "generated": datetime.now().astimezone().isoformat(timespec="minutes"),
        "sessions": sessions,
        "turns": turns,
        "builtin_tools": dict(sorted(builtin.items(), key=lambda kv: -kv[1]["total"])),
        "mcp_servers": {s: {"total": sum(c.values()), "by_kind": dict(c)} for s, c in sorted(mcp_servers.items(), key=lambda kv: -sum(kv[1].values()))},
        "mcp_tools": dict(sorted(mcp_tools.items(), key=lambda kv: -kv[1]["total"])),
        "skills": dict(sorted(counter_rows(stats["skill_calls"]).items(), key=lambda kv: -kv[1]["total"])),
        "slash_commands": dict(sorted(counter_rows(stats["slash_commands"]).items(), key=lambda kv: -kv[1]["total"])),
        "agent_types": dict(sorted(counter_rows(stats["agent_types"]).items(), key=lambda kv: -kv[1]["total"])),
    }


def print_table(title, rows, kinds):
    print(f"\n## {title}\n")
    print("| Name | Total | " + " | ".join(kinds) + " |")
    print("|---|---:|" + "---:|" * len(kinds))
    for name, row in rows.items():
        cells = " | ".join(str(row["by_kind"].get(k, 0)) for k in kinds)
        print(f"| `{name}` | {row['total']} | {cells} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--config-dir", action="append", type=Path)
    ap.add_argument("--json", type=Path, help="also write the full report as JSON here")
    args = ap.parse_args()

    home = Path.home()
    config_dirs = args.config_dir or [home / ".claude", home / ".claude-background"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    stats = {
        "sessions": defaultdict(set),
        "turns": defaultdict(set),
        "tool_calls": defaultdict(Counter),
        "tool_sessions": defaultdict(set),
        "skill_calls": defaultdict(Counter),
        "slash_commands": defaultdict(Counter),
        "agent_types": defaultdict(Counter),
    }
    for cfg in config_dirs:
        projects = cfg / "projects"
        if not projects.is_dir():
            continue
        for path in projects.rglob("*.jsonl"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    continue
                scan_file(path, cutoff, stats)
            except OSError:
                continue

    report = build_report(stats, args.days, config_dirs)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")

    print(f"# Tool usage, last {args.days} days ({report['generated']})\n")
    print("| Session kind | Sessions | API turns |")
    print("|---|---:|---:|")
    for k in KINDS:
        print(f"| {k} | {report['sessions'][k]} | {report['turns'][k]} |")
    print_table("Built-in tools", report["builtin_tools"], KINDS)
    print_table("MCP servers", report["mcp_servers"], KINDS)
    print_table("MCP tools", report["mcp_tools"], KINDS)
    print_table("Skills (Skill tool)", report["skills"], KINDS)
    print_table("Slash commands typed", report["slash_commands"], KINDS)
    print_table("Subagent types launched", report["agent_types"], KINDS)


if __name__ == "__main__":
    main()
