# Repo-level scripts

Utilities shared across this repo's plugins and the session tooling, referenced by stable clone
path (e.g. `~/Dev/rrrutledge/rrrutledge-claude-code-plugins/scripts/<name>`). They live here — not
inside any one plugin — so several callers can reuse them without depending on a plugin version.

| Script | What it does |
| ------ | ------------ |
| `wait-done.py` | Block until a completion marker file appears. |
| `peek.py` | Render a spawned session's transcript as a compact timeline. |
| `auto_bump_version.py` | Post-merge CI helper: auto-bump a plugin's patch version when its files changed but no manual bump landed. |

## The launch → wait → peek toolkit

Launching a session, `wait-done.py`, and `peek.py` compose into one pattern: spawn a Claude session in the background, wait for it to finish, and watch its progress while you wait.
An orchestrator (the handoff launcher, a drainer worker delegating to another repo, or any agent) uses all three together.

### 1. Launch - session-mgr's `spawn-session.py`

The launcher lives in the session-mgr plugin at `plugins/session-mgr/skills/resume-sessions/scripts/spawn-session.py`.
It starts a background session (`claude --bg`) with Remote Control on, so the session is reachable from claude.ai/code and the phone.
The drainer's `spawn-handoff.py` is a thin shim over the same launcher.

```
python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --title "<short title>" --cwd "<repo dir>" --brief "<repo dir>/.tmp/handoff-<slug>.md" --model <model id>
python "<session-mgr>/skills/resume-sessions/scripts/spawn-session.py" --resume <session guid> --cwd "<its cwd>"
```

It seeds the session one of three ways:

- `--brief <file>` points the session at a handoff doc.
- `--prompt-file <file>` points it at a standing instructions file, optionally led by the one-line text in `--summary-file`.
- `--resume <guid>` continues an existing session with its history, optionally handed a `--brief` or `--prompt-file` as a follow-up.

A fresh launch needs `--model` and a name (`--title` or `--summary-file`).
It prints the new session's short id, the handle `claude attach`, `claude logs`, and `claude stop` take.
It also writes the session's id to `<file>.session` beside the brief or prompt file, the receipt `peek.py` reads.

All prose travels in a file, never on the command line.
Only paths, titles, model ids, and guids cross the command line, so an embedded quote or `;` never reaches a shell.

### 2. Wait — `wait-done.py`

```
python wait-done.py <path>.done
```

Blocks (polling every 20s) until either `<path>.done` or `<path>.skip` exists, then exits 0. Run it in
the **foreground** in short cycles so the orchestrator's session stays in the "working" state; between
cycles, `peek` the spawned session to show live progress.

### 3. Peek — `peek.py`

```
python peek.py <prompt>.session --tail 8
python peek.py <short id> --tail 8
```

It takes a `.session` receipt, a full session guid, the short-id prefix `spawn-session.py` prints, or a `.jsonl` path.
It finds the transcript under every account's `projects` dir: `~/.claude`, each account session-mgr has seen a session run on, and the current `CLAUDE_CONFIG_DIR`, each when it exists.
When a prefix matches several sessions, it shows the newest and names the others.
It prints a compact timeline of assistant text, tool calls, and short tool results, skipping base64 image blobs, so you can see what the spawned session is doing.

## The `.done` / `.skip` marker protocol

Spawned sessions and their orchestrator coordinate through two zero-content marker files, not a live
channel:

- **`.done`** — the spawned session writes this (with a one-line summary as its content) when it has
  finished its work. `wait-done.py` returns as soon as it appears. Re-writing it later is harmless.
- **`.skip`** — the orchestrator (or a human) writes this to unblock a `wait-done.py` that is stuck on
  a session that will never finish. Same base path as the `.done`, with a `.skip` extension.

The marker base path is chosen by the orchestrator and handed to the spawned session in its prompt
(e.g. a drainer worker points the session at `<item>.work.done` / `<item>.work-result.md`). This keeps
the handoff to a single file the orchestrator polls — no extra monitoring machinery.

## Primary consumers

These are generic, but the **drainer** is the main user: a drainer worker handling a SkyStage/IDP item
launches a full session in the internal-developer-portal repo, waits on its `.done`, and peeks its
progress meanwhile (see `drainer-local/skystage-idp-session.md` in the russ-ai-pod repo).
The **handoff launcher** (documented in `~/OneDrive/Claude/handoffs.md`) launches through `spawn-session.py --brief`.
