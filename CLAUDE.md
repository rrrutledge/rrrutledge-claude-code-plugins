# rrrutledge-claude-code-plugins

This repo contains Claude Code plugins, the most actively maintained of which is
**safe-compounds** — a `PreToolUse` hook that auto-approves Bash commands it can prove
are safe.

## Engineering principle: deterministic logic goes in Python

Wherever a plugin's behavior can be fully specified in advance, write it as a Python script,
not prose an AI re-derives each run or a PowerShell one-liner. A script is cheaper, faster,
and testable; AI judgment is reserved for the steps that genuinely need it (see the
drainer's `poller-core.md` for the canonical split between what's code and what's a single
batched AI call per cycle).

---

## Contributing

**Patch version bumps happen automatically on merge — don't bump for an ordinary fix.** A
workflow (`.github/workflows/version-bump.yml`, `scripts/auto_bump_version.py`) runs on push to
`main` and patch-bumps any plugin whose files changed but whose version didn't already move in
that push.

**A minor or major bump is still your call, made in the PR.** When a change is a new feature or a
breaking change rather than a fix, bump the `version` field in that plugin's
`.claude-plugin/plugin.json` yourself (e.g. `1.3.14` → `1.4.0`) in the same PR — the auto-bump
only ever increments the patch digit, and it leaves a plugin alone if its version already moved
in the push, so a manual bump you make is never double-bumped.

---

## safe-compounds — How It Works

### Core philosophy

Two things are "safe" and get auto-approved:

1. **Read-only** — can't change anything (`ls`, `git status`, `gh pr list`, `curl` GET).
2. **Surely reversible** — the effect can be undone cheaply (commit → revert, PR → close,
   branch → delete, label → delete).

Everything else falls through to a normal permission prompt. The hook is
**deny-by-default**: it approves from an allowlist of known-safe forms, not by trying to
enumerate everything dangerous.

### Three outcomes

| Decision | Meaning |
|----------|---------|
| **ALLOW** | Auto-approved — safe. |
| **PROMPT** | Silent — Claude Code's normal flow asks the user. |
| **BLOCK** | Denied *with a corrective message*. Not "forbidden" — the form can't be statically validated, or it always has a better equivalent. The message asks for that equivalent form (e.g. move complex bash into a `.tmp/` Python script, use the Write tool instead of `>`). |

### Decision pipeline (`hook.py`)

For every Bash call:
1. `enforce_bash()` — if the *form* is unvalidatable (heredoc, output redirection, `$()`,
   3+ pipes, loops, `cd /other/dir && cmd`, etc.) → **BLOCK** with rewrite instruction.
   It also BLOCKs an unbounded `find` from `/`, a drive root, or home, which walks the whole
   disk for hours.
2. Split compound command (`a && b | c`) into segments.
3. For each segment: `is_segment_trusted()`. If any segment fails → **PROMPT**.
4. All segments trusted → **ALLOW**.

### Allow mechanisms — from simplest to most complex

#### 1. Base trusted command names (`trust.py` — `TRUSTED_COMMANDS`)

A Python set of ~60 command names (`git`, `gh`, `npm`, `ls`, `rm`, `touch`, etc.).
Being in this set means the *name* is recognized; it does NOT auto-approve all
subcommands — each tool with subcommands has its own checker (see below).

**To add a new bare command**: add its name to `TRUSTED_COMMANDS` in `trust.py`.
Use this only for commands where the command itself (with no subcommand logic) is safe,
or where a dedicated checker will handle subcommand validation.

#### 2. Subcommand-based allows (`commands.py` — `SUBCOMMAND_SPECS`)

Tools like `git`, `gh`, `npm`, `yarn`, `pip`, `pnpm`, `bun`, `schtasks`, `wmic` are
evaluated by a **declarative spec engine**. Each spec has:

- **`trusted`** — set of always-approved subcommands (read-only or reversible writes).
- **`conditional`** — subcommands approved *unless* a destructive flag is present.
  Example: `git push` is approved unless `--force`/`-f`/`--delete` appears;
  `git reset` is approved unless `--hard` appears.
- **`specials`** — dict of `{subcommand: callable}` for one-off custom logic
  (e.g. `git checkout`, `git clean`).
- **`category`** — a string key for the AI/learned fallback (see §5 below).

**To allow a new git/gh/npm/etc. subcommand**: add it to the appropriate
`*_TRUSTED_SUBCOMMANDS` set in `commands.py`. If it's safe only without certain flags,
add it to the `conditional` dict with the blocking flags.

#### 3. Argument/path-based allows (`commands.py`)

Some commands are approved based on their *arguments*, not just their name:

- **`curl`** — approved to `localhost:*` or configured trusted domains (any method);
  approved anywhere for reads (no write flags like `-X POST`, `-d`, `--data`).
  To add a trusted domain: add to `curl_domains` in `~/.claude/safe-compounds-config.json`.
- **`cp`, `mv`, `touch`, `ln`, `chmod`** — approved when the destination path is within
  the current git repo (CWD, git worktree, or `settings.json` allowed-edit paths),
  `~/.claude/plugins/`, a `trusted_destination_dirs` entry, or any `.tmp` directory
  anywhere on disk (not just the current repo's) — `.tmp/` is always scratch space,
  regardless of which repo it's under.
- **`sed`** — approved unless the `-i` (in-place edit) flag is present.

#### 4. AI inspection + learned store (`commands.py` — `_ai_learn`, `learned.py`)

When a subcommand isn't in any static allowlist, the hook asks Haiku:
> "Is `{tool} {subcommand}` a safe, legitimate software development operation?"

If Haiku says SAFE, the approval is **persisted** to
`~/.claude/safe-compounds-learned.json` under the appropriate category key — so the
prompt never appears again for that command on this machine.

Same process for unknown top-level command names (not in `TRUSTED_COMMANDS`): Haiku
is asked whether the CLI tool name is a safe dev/productivity tool.

#### 5. User settings.json allows (`trust.py`)

Commands matching `Bash(command-name)` in `permissions.allow` in your `settings.json`
are automatically pulled into the trusted set.

#### 6. Workflow tool scripts (`workflow.py`)

A saved workflow (`.claude/workflows/` or built-in) is blanket-trusted by name via
`workflow_blanket_names`. An inline/dynamic script instead gets AI-judged fresh on every
run (`scripts.ask_ai_about_workflow_script`) against the prompts it hands to `agent()` —
the only thing such a script can actually do. DANGEROUS blocks with a reason; otherwise
it prompts as usual. See the docstrings in `workflow.py`/`scripts.py` for the full
reasoning.

#### 7. EnterWorktree / ExitWorktree tool calls (`worktree_tool.py`)

Creating a new worktree (no `path` given) is always approved.
It's the EnterWorktree equivalent of `git worktree add` under `.claude/worktrees/`, already a trusted git subcommand, so it always lands in the trusted convention.

Switching into an *existing* worktree (`path` given) is always denied instead of approved, no matter where that path lives.
Claude Code enforces its own permission-root confirmation on any explicit-`path` EnterWorktree call, independent of any hook decision: moving the session's working directory, write access, and project config counts as a permission-root change, and per Claude Code's docs only `bypassPermissions` mode suppresses that prompt - approving the call would buy nothing, since the harness asks regardless.
So the denial redirects the model to work the existing worktree without relocating into it - `git -C <path> <command>` for git operations, absolute paths under it for Read/Write/Edit and other file tools - neither of which touches the permission root, so neither prompts.
A path outside `.claude/worktrees/` gets an extra first step in that same message: move it into convention with `git worktree move` (already a trusted git subcommand, preserving the branch and any uncommitted work), then work on it directly the same way, rather than retrying EnterWorktree.
The one gap: a command that needs its own working directory to actually *be* the worktree (e.g. an `npm` script relying on relative paths) can't be expressed as `-C` or an absolute path, and still needs a real relocation - which still costs the one click.

ExitWorktree takes no path - it only ever acts on the one worktree the current session entered via EnterWorktree, tracked internally by the harness rather than supplied by the model.
`action: "keep"` is always approved (nothing is deleted).
`action: "remove"` mirrors `git branch -d`: the tool itself refuses when the worktree has uncommitted files or commits that never landed on the original branch, so a plain remove is approved.
Only `discard_changes: true`, which forces the removal through and knowingly throws away unsaved work, falls through to a manual prompt.

### Learned → source promotion (`tools/sync_learned.py`)

At **session end** (and on-demand via `/safe-compounds:sync-learned`), learned approvals
are promoted back to the plugin source via a GitHub PR on a rolling branch
`safe-compounds-learned`. The sync:
- Inserts newly learned names into the correct set literals in `trust.py` / `commands.py`.
- Excludes anything in `trusted_commands` or `learned_sync_exclude` in the config
  (private/company-specific commands).
- Is idempotent and best-effort (failures never disrupt session end).

### Config file

`~/.claude/safe-compounds-config.json`:

| Key | Purpose |
|-----|---------|
| `trusted_commands` | Private commands — trusted locally but never synced to the PR |
| `curl_domains` | Trusted domains for curl (e.g. `atlassian.net`) |
| `mcp_blanket_servers` | MCP server names to blanket-allow |
| `trusted_script_dirs` | Directories containing safe scripts |
| `learned_sync_exclude` | Learned commands to exclude from PR sync |
| `workflow_blanket_names` | Saved workflow names (`tool_input.name`) to blanket-allow — never matches an inline/dynamic script, which is instead judged fresh each run by the AI content check in `workflow.py` |
| `trusted_destination_dirs` | Extra dirs `cp`/`mv`/`ln` may write to outside the repo, beyond the built-in default (`~/Downloads`) |

---

## Typical workflow: adding a new safe command

**Scenario:** A command required a manual prompt, and you believe it's safe.

1. **Identify the command form** — is it a bare command, a subcommand of an existing
   tool, a `curl` to a specific domain, or a file op?

2. **Choose the right mechanism:**

   | Case | Where to add |
   |------|-------------|
   | New bare tool (e.g. `terraform`) | `TRUSTED_COMMANDS` in `trust.py` |
   | New subcommand of git/gh/npm/etc. | Appropriate `*_TRUSTED_SUBCOMMANDS` set in `commands.py` |
   | Subcommand safe only without certain flags | `conditional` dict in the tool's spec in `commands.py` |
   | `curl` to a new domain | `curl_domains` in `~/.claude/safe-compounds-config.json` |
   | Private/company command (don't want in public PR) | `trusted_commands` in `~/.claude/safe-compounds-config.json` |
   | A specific `/code-review`-style Workflow you want to always run | `workflow_blanket_names` in `~/.claude/safe-compounds-config.json` |
   | `cp`/`mv`/`ln` writing to a specific dir outside the repo (e.g. `~/Downloads`) | `trusted_destination_dirs` in `~/.claude/safe-compounds-config.json` |

3. **Consider reversibility** — if the operation is reversible (can be undone), it
   belongs in the allowlist. If it's irreversible or unknowable, it should remain a
   manual prompt.

4. **Run tests** and **open a PR** — changes to `trust.py` and `commands.py` are
   promoted via the normal git workflow.

---

## Key files

| File | Role |
|------|------|
| `plugins/safe-compounds/safe_compounds/hook.py` | Entry point — orchestrates the decision pipeline |
| `plugins/safe-compounds/safe_compounds/trust.py` | `TRUSTED_COMMANDS` + learned/settings integration |
| `plugins/safe-compounds/safe_compounds/commands.py` | All per-tool safety checkers + `SUBCOMMAND_SPECS` |
| `plugins/safe-compounds/safe_compounds/enforce.py` | Bash form validation + block messages |
| `plugins/safe-compounds/safe_compounds/learned.py` | Machine-local learned store read/write |
| `plugins/safe-compounds/safe_compounds/workflow.py` | Classifies Workflow tool calls: `workflow_blanket_names` for saved workflows, AI content check for inline scripts |
| `plugins/safe-compounds/safe_compounds/worktree_tool.py` | Classifies EnterWorktree (approved only when creating fresh with no path; any explicit path is denied with a redirect to work the worktree directly via `git -C`/absolute paths instead - see §7) and ExitWorktree (`keep` and plain `remove` approved, `discard_changes` prompts) tool calls |
| `plugins/safe-compounds/tools/sync_learned.py` | Promotes learned approvals to a GitHub PR |
| `plugins/safe-compounds/safe_compounds/config.py` | Config file schema + loader |
