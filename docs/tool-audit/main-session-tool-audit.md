# Main-session tool audit

Measured 2026-11-02 on Russell's main account, Claude Code in this repo.
It covers what a fresh main session carries in its fixed prompt before the first user turn, and how often each tool was called in the last 60 days.
Every removal recommended below waits on Russell's decision; settings, plugins, and connectors are as measured.

## How it was measured

**Fixed prompt.**
Each configuration is one `claude -p "Reply with exactly: OK" --max-turns 1 --output-format stream-json --verbose` call, reading the first assistant message's `usage` (input plus cache-write plus cache-read tokens).
A tool's cost is the drop when that one tool is left out of an explicit `--tools` list, measured with `--strict-mcp-config` so connector loading can't shift the number.
A connector's cost is the drop under `--disallowedTools mcp__<server>`.
`-p` defaults to Sonnet 5.5 here; the counts include the SessionStart hook context and this repo's `CLAUDE.md`.

**Usage.**
[`scan_tool_usage.py`](scan_tool_usage.py) streams every transcript under `~/.claude/projects` and `~/.claude-background/projects` and counts `tool_use` blocks, `Skill` calls, typed slash commands, and API turns per session kind.
Rerun it with `python docs/tool-audit/scan_tool_usage.py --days 60 --json .tmp/usage.json`.

## The fixed prompt of a main session

| Configuration | Fixed prompt tokens | Tokens the removed part costs |
|---|---:|---:|
| Full main session | 50,269 | - |
| Without `CLAUDE.md` files | 38,154 | 12,115 |
| Without the claude.ai connectors | 44,979 | 5,290 |
| Without skills (drops the `Skill` tool and its listing) | 45,453 | 4,816 |
| No tools, connectors, or skills | 15,478 | floor |
| Denying PowerShell, Workflow, Figma, Claude Docs | 42,673 | 7,596 |

**Deferred tools cost only their names.**
Connector tools and the rarely used built-ins (cron, Monitor, NotebookEdit, PushNotification, RemoteTrigger, SendToProject, TaskStop, DesignSync, the worktree tools, WebSearch) load on demand through `ToolSearch`.
Each deferred built-in costs under 150 tokens per turn, and most cost under 10.
Dropping `ToolSearch` itself adds 18K, and `--tools ""` in a session with connectors grows the prompt to 104,582, because every deferred definition then loads up front.

**`--tools ""` pays off when paired with `--setting-sources ""`.**
With no settings sources, no connector loads, so there is nothing to inline: the drainer's triage flags measure 3,364 tokens on the main account and 3,412 on the background account.

**A settings deny removes the tool from the prompt.**
`permissions.deny` in settings and `--disallowedTools` on the command line produce the same 42,673-token prompt, so either lever works.

## Usage over the last 60 days

| Session kind | Sessions | API turns |
|---|---:|---:|
| interactive | 116 | 4,923 |
| handoff | 398 | 23,090 |
| drainer-worker | 1,397 | 40,461 |
| drainer-main | 1 | 39 |
| headless (`claude -p`, mostly triage) | 171,162 transcripts | 66,080 |
| subagent | 3,778 | 36,135 |

The headless row counts transcript files, and its turn count is unchecked; neither figure feeds the main-session math below.
The main sessions (interactive, handoff, drainer worker, drainer main) took 68,513 turns, and each turn re-reads the whole fixed prompt.
One token in the fixed prompt therefore costs about 68.5K tokens read per 60 days, mostly as cache reads.

## Per tool and connector

Calls are main-session calls (interactive + handoff + drainer worker + drainer main); subagent and headless calls are listed where they matter.
Tokens read per 60 days is the per-turn cost times 68,513 turns.

| Tool or connector | Tokens per turn | Main-session calls | Tokens read per 60 days | Recommendation |
|---|---:|---:|---:|---|
| `PowerShell` | 3,744 | 1 | 256M | **Drop** (Russell agreed) |
| `Workflow` | 1,979 | 0 | 136M | **Drop** |
| claude.ai Claude Docs | 947 | 0 | 65M | **Drop** |
| claude.ai Figma | 927 + its share of the 2.3K server instructions | 0 | 64M+ | **Drop** |
| `ReportFindings` | 821 | 0 (739 headless) | 56M | **Drop** after checking `/code-review` |
| `ScheduleWakeup` | 1,695 | 28 (67 headless) | 116M | Keep, low use |
| `Skill` + listing | 4,816 | 2,952 | 330M | Keep |
| `Agent` | 3,409 | 2,568 | 234M | Keep |
| `Bash` | 1,688 | 28,638 | 116M | Keep |
| `Grep` | 1,276 | 4,330 | 87M | Keep |
| `Read` | 712 | 15,461 | 49M | Keep |
| claude.ai Gmail | 635 | 605, plus 519 in subagents | 44M | Keep |
| `ListAgents` | 405 | 198 | 28M | Keep |
| `Edit` | 348 | 5,623 | 24M | Keep |
| `Glob` | 277 | 1,426 | 19M | Keep |
| claude.ai Google Drive | 248 | 280 | 17M | Keep |
| `Write` | 236 | 7,286 | 16M | Keep |
| claude.ai Google Calendar | 206 | 150 | 14M | Keep |
| Deferred built-ins with zero calls: cron, NotebookEdit, PushNotification, RemoteTrigger, SendToProject, DesignSync | see "Deferred tools cost only their names" | 0 | under 1M each | Keep, too cheap to matter |

**Near-zero-use tools worth dropping.**
`PowerShell`, `Workflow`, Claude Docs, and Figma together cost 7,596 tokens per turn, about 15% of the fixed prompt, for one call in 60 days.
Disconnecting Figma at claude.ai also removes its server instructions, which a settings deny leaves in place, so for Figma disconnecting at claude.ai saves more than a settings deny.
Disconnecting there also removes the connector from claude.ai chat.

**ReportFindings is the code-review findings tool.**
All 739 calls came from headless `claude -p` sessions.
Deny it in main sessions once one `/code-review` run launched from a main session shows it still reports findings.

**ScheduleWakeup carries `/loop` and self-scheduled check-ins.**
It costs 1,695 tokens per turn for 28 main-session calls; it stays because denying it removes `/loop` and self-scheduled check-ins from main sessions.

**The biggest fixed costs are `CLAUDE.md` files and the skills listing.**
`CLAUDE.md` files (12.1K) cost more than any single tool, and the `Skill` tool with its listing (4.8K) is the largest tool cost; both are the next place to look.

## Where each change applies

**Drainer workers and handoff sessions take flags per launch.**
Both start from a launcher command, so `--disallowedTools PowerShell,Workflow,mcp__claude_ai_Figma,mcp__claude_ai_Claude_Docs` on that command covers 63,551 of the 68,513 main-session turns.

**Interactive sessions need a settings change.**
A `permissions.deny` entry with the same four names in the shared `~/OneDrive/Claude/.claude/settings.json` covers every session on every machine, launched ones included.
