# safe-compounds

A `PreToolUse` hook that lets long Claude Code sessions run without a stream of
permission prompts — **without** `--dangerously-skip-permissions`. It
auto-approves what it can prove is safe, and when it can't prove safety, it asks
for the command in a form it *can* check.

This plugin is the repo-managed, tested successor to a hand-maintained
single-file hook.

## Philosophy

**Allow as much as can be allowed safely; everything else falls through to a
normal prompt.** Two things are "safe":

1. **Read-only operations** — they can't change anything (`ls`, `grep`,
   `git status`, `gh pr list`, a GET request).
2. **Operations that are surely reversible** — the effect can be undone, so the
   worst case is cheap. Opening a PR (you can close it), making a commit (you can
   revert it), creating a branch or label (you can delete it), an MCP "create"
   (you can delete it). These get approved even though they write.

What is *not* safe — irreversible or unknowable — is never auto-approved. It
isn't forbidden; it just falls through to a manual prompt so you decide. The
hook is deliberately **deny-by-default**: it approves from an allowlist of
known-safe forms rather than trying to enumerate everything dangerous.

## Vocabulary

- **trusted** — the allowlist *data*: names that are pre-allowed. The bare
  command set `TRUSTED_COMMANDS` (e.g. `ls`, `grep`) plus the per-tool
  `*_TRUSTED_SUBCOMMANDS` sets and `GH_AI_TRUSTED_PAIRS`. Fed by
  `TRUSTED_COMMANDS` + config + your settings.json + learned.
- **safe** — a checker's *verdict* that a specific command or segment may be
  auto-allowed (the `is_*_safe` functions).
- **allow / block / prompt** — the three decisions the hook emits.

("allowed" appears only for filesystem dirs your settings.json grants Edit/Write
on — a separate idea.)

## Three decisions

For each tool call the hook produces one of:

- **ALLOW** — auto-approve (read-only or surely-reversible).
- **PROMPT** — stay silent and let Claude Code's normal permission flow ask you.
  This is the default for anything not provably safe.
- **BLOCK** — deny *with a corrective message*. A block does **not** mean
  "forbidden". It means the command is in a form whose safety can't be
  statically determined, so the hook asks for an equivalent form it can validate
  — e.g. move complex bash into a `.tmp/` Python script (which it can read and
  check), or use the Write tool instead of `>` redirection.

## What it does

- **Bash** — splits a compound command (`a && b | c`) into segments and approves
  it only if *every* segment is trusted or a recognized-safe form: read-only/
  reversible `git` (allowlist of subcommands), read-only/reversible `gh`,
  `curl` to localhost / a configured domain / any GET, package-manager installs,
  CWD-scoped `cp`/`mv`/`touch`/`ln` (also trusting `~/Downloads` as a
  destination by default, plus any dirs in `trusted_destination_dirs`),
  and `start` on viewable file types.
  `wt` (Windows Terminal) always prompts, since the command line it runs in a new tab goes unchecked.
- **Enforcement (BLOCK → rewrite)** — heredocs, output/input redirection, inline
  `python -c` / `node -e`, PowerShell cmdlets in bash, `cmd /c`, `sed -i`, `$VAR`
  expansion, `VAR=...` assignment, and complex bash (loops, `$()`, conditionals,
  3+-stage pipelines, brace groups). Each returns a message pointing at the
  validatable alternative.
- **Write/Edit** — approves `.tmp/` files, `.claude/{skills,commands,screenshots}`,
  and files inside a git repo's working tree; redirects stray temp-named files
  into `.tmp/`.
- **MCP** — approves read-only and reversible-write verbs; prompts on destructive
  verbs (`delete`, `purge`, …). Whole servers can be blanket-approved via config,
  and individual tools (by full name) can be blanket-approved too.

## Configuration

The plugin ships with **no organization-specific data**. Supply your own via a
machine-local JSON file at `~/.claude/safe-compounds-config.json` (or the path in
`SAFE_COMPOUNDS_CONFIG_JSON`). All keys are optional; defaults are empty (trust
nothing extra — curl is then limited to localhost and GETs, no MCP server is
blanket-approved, etc.).

```json
{
  "trusted_commands": ["mycli"],
  "curl_domains": ["atlassian.net", "mycorp.sharepoint.com"],
  "mcp_blanket_servers": ["plugin_product-management_atlassian"],
  "mcp_blanket_tools": ["mcp__claude_ai_Gmail__apply_sensitive_message_label"],
  "trusted_script_dirs": ["my-plugins/"],
  "trusted_destination_dirs": ["~/OneDrive/Deliverables"]
}
```

### Environment variables

- `SAFE_COMPOUNDS_CONFIG_JSON=<path>` — override the config file location.
- `SAFE_COMPOUNDS_DISABLE_AI=1` — disable all Haiku fallbacks (kill-switch; used
  by the test suite).
- `SAFE_COMPOUNDS_TRUSTED_JSON=<path>` — use a JSON list as the trusted set
  verbatim (used by tests to pin behavior).
- `SAFE_COMPOUNDS_LEARNED_JSON=<path>` — override the learned-store location.

The AI fallback uses `ANTHROPIC_HOOK_API_KEY` / `ANTHROPIC_API_KEY`, or Vertex
via `ANTHROPIC_VERTEX_PROJECT_ID`. With no credentials, AI calls return
"undecided" and the command falls through to a prompt.

## Learned commands

When the AI fallback approves a command or subcommand the hook didn't already
recognize, it records it in `~/.claude/safe-compounds-learned.json` so future
runs approve it instantly without re-asking. The store is machine-local; the
running hook only ever reads its own source, never rewrites it.

Those learnings are promoted to a shared PR **automatically at session end**: a
`SessionEnd` hook runs `tools/sync_learned.py`, which folds each new entry into
the matching allowlist in the source (`TRUSTED_COMMANDS` in `trust.py`, the
`*_TRUSTED_SUBCOMMANDS` sets in `commands.py`) and opens/updates a single rolling
`safe-compounds-learned` PR via the GitHub API. It needs no local checkout
(works from the installed plugin), never touches your working tree, and is
best-effort — if `gh` isn't authed it just leaves the learnings local for next
time. Run **`/safe-compounds:sync-learned`** to trigger the same thing on demand.

The per-command hook itself never does any of this — it only appends to the
local store, so it stays fast.

Company-specific trust is never shared: the sync skips anything in your
`trusted_commands` config (that's your private allowlist) and anything you list
in `learned_sync_exclude`. Put internal tool names in config, not the shared PR.

## Architecture

```
hook.py                  entrypoint / orchestrator (decision order)
safe_compounds/
  shell.py               tokenizing, segment splitting, command-name extraction
  paths.py               cross-platform "is this path inside an allowed area?"
  trust.py               the trusted command set (base + config + settings + learned)
  config.py              consumer config (domains, servers, commands, dirs)
  ai.py                  single Claude Haiku request impl (fallback classifier)
  learned.py             machine-local store of AI-approved commands
  scripts.py             node/python script safety analysis (deny-by-default)
  commands.py            subcommand engine (git/gh/npm/yarn/pip/pnpm/bun via a
                         spec table) + bespoke checkers (curl/sed/start/cmd/file ops/taskkill)
  procs.py               live Windows process-ancestry walk (ctypes, no deps) —
                         backs the taskkill self-close-my-own-tab checker
  enforce.py             the BLOCK rules ("rewrite into a validatable form")
  approve.py             per-segment trust decision
  mcp.py                 MCP tool classification
  writes.py              Write/Edit handling
commands/sync-learned.md slash command to promote learnings into a PR
tools/sync_learned.py    auto-sync (SessionEnd hook + on-demand): learnings -> PR
```

Subcommand-shaped tools (git, gh, the package managers) share one
`check_subcommand_tool` engine driven by `SUBCOMMAND_SPECS` in `commands.py` —
adding a new such tool is a table entry, not new code. Tools whose safety isn't
subcommand-shaped (curl URLs, `sed -i`, `start` extensions,
`.cmd` parsing, CWD-scoped file ops, `taskkill` self-targeting) stay
as small purpose-built checkers.

One orchestrator (not two separate hook processes) preserves the block-then-
approve ordering, which the Write/Edit path depends on (a temp-named file
*inside* `.tmp/` must be allowed, not redirected).

## Tests (run locally as needed)

```
python -m pip install pytest
python -m pytest plugins/safe-compounds
```

`tests/corpus_def.py` is the behavior spec: each case carries the decision the
hook must produce (`expect`). `test_characterization.py` runs the hook as a
subprocess (exactly how the harness invokes it) over the corpus and asserts each
decision; `test_units.py` covers the parsing/classification helpers. Everything
is hermetic — AI disabled, the trusted set pinned (generated from `trust.py` at
test time, so it can't drift), config from a fixture, temp working dirs. There's
no CI; run these locally when you change the hook.

## Requirements

- Python 3 on `PATH` (the harness runs `python "${CLAUDE_PLUGIN_ROOT}/hook.py"`).
