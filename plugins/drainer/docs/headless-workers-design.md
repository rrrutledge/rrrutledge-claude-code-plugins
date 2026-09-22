# Headless drainer workers - verified design

Status: design under review (not yet built).
Verified against `claude` 2.1.278 on Russell's machine, 2026-09-22.

## The goal in one line

Drainer-spawned Claude sessions (workers, digest, resume) run without a visible Windows Terminal tab, so a spawn never steals desktop focus, while still giving Russell the two things the terminal tab is the only current source of: a list of every running session, and a clear working-vs-waiting signal per session.

## Why the tab has to go, and why the obvious softer fixes do not work

Adding a tab to the `drainer` window activates that window every time, even when it is minimized and Russell is in a browser.
Windows Terminal has no command-line flag to open a tab without the window taking focus (`--no-focus` governs new-window creation only, and does not exist as a suppression flag).
So the only real fix is to not open a terminal window for a worker at all.
Everything below makes that safe.

## The two acceptance criteria, and how headless meets each

The terminal tab is today the only place Russell can:

1. **See every session that is still running.**
   Met by `claude agents --json`, which lists interactive and background sessions alike, rendered by the Phase 1 `claude-agents` tool (running list, needs-you sorted to the top).
2. **Tell working from waiting.**
   Met by the same feed: a background worker reports `state:working` while it grinds and `state:blocked, waitingFor:"permission prompt"` when it needs Russell, which is a richer signal than the tab's green star (it names *what* it is waiting on).

Headless ships only because both are covered at least as well as the tab covers them today.

## Verified mechanism (spiked on this machine)

`claude --bg --name "<name>"` launches a background session with these confirmed properties:

- **No window, no terminal tab, no focus steal.**
  The command returns immediately after printing the new session's short id.
- **Runs in the launcher's working directory (the real repo), with no automatic git worktree.**
  A background session reported its own `pwd` as the repo root, and `git worktree list` gained no entry.
- **Inherits the launching process's environment**, so ownership and configuration env vars set at spawn reach the worker.
- **Appears in `claude agents --json`** with `kind:"background"` and a state machine the Phase 1 tool already maps:
  `status:busy / state:working` while working,
  `status:idle / state:done` when a turn finishes,
  and `status:waiting / state:blocked / waitingFor:"permission prompt"` when it is paused at an approval gate.
- **Pauses and holds at a real approval gate.**
  A worker told to run an irreversible command (a `curl -X POST` to an untrusted domain) held the "Do you want to proceed?" prompt indefinitely: it did not proceed on its own and it did not exit.
  This is the safety guarantee the whole draft-only model depends on - a headless worker waits for Russell exactly where a tab worker would.
- **Engaged when it needs Russell** via `claude attach <id>` (opens the session in a terminal Russell launches himself, so any focus change is one he initiated) or via Remote Control on the phone or web (a session showed `/rc` active).
- **Ended and cleaned** via `claude stop <id>` and `claude rm <id>`; `claude logs <id>` prints recent output.

## The design

### Spawn

A worker is spawned with:

```
claude --bg --permission-mode manual --name "<summary>" --model "<model>" \
  --disallowedTools Artifact,Workflow,SendFeedback,PowerShell "<seed>"
```

run with the drainer repo as the working directory and the browser-chauffeur ownership env applied (below).

- `--permission-mode manual` is the safety anchor.
  It makes every action that is not auto-approved by the `safe-compounds` hook pause for Russell, which is what turns "the worker reached a send" into the `blocked / waitingFor:"permission prompt"` state rather than an autonomous send.
  The hook still auto-approves safe commands under manual mode, so the day-to-day worker experience matches today's tab workers; only the final irreversible steps wait.
- The seed and disallowed-tools list carry over unchanged from the current launcher, so a headless worker reads the same prompt file and runs without the four unused tools.

### Session identity: capture, do not mint

Background sessions manage their own id: `--bg` ignores `--session-id` and prints its own short id (`backgrounded · <id> · <name>`).
So the identity flow inverts from today's model.
The spawn captures the printed id from `claude --bg` stdout and writes it to the same per-item receipt file the tab path writes (`<prompt>.session`), so correspondent-holding, reconcile, and `peek` keep reading one receipt regardless of which path spawned the worker.

### Liveness

Worker liveness is read from `claude agents --json` (the set of background session ids currently alive), in place of the current scan for `claude --session-id <guid>` command lines.
Both the receipt content and the liveness set then live in the same id space (the background short id), so "is this worker still alive" stays a clean set-membership test, and a worker that dies without clearing releases its correspondent hold within one cycle exactly as it does today.

### Concurrency budget

The open-worker cap counts live background sessions from `claude agents --json`, in place of counting open Windows Terminal tabs.

### Ending a worker

A headless worker has no tab to close, so the tab self-close path does not apply to it.
When a worker finishes its turn the background session settles into `state:done`, and a reaper removes settled sessions with `claude rm` (or the existing reconcile absorbs that cleanup).
Two companion changes make the finish clean rather than leaving the worker stuck trying to close a tab that is not there:

- `worker-core.md` gets a headless close-up branch: finish the item and stop, with no tab-close step.
- The Stop-hook close-check (`~/OneDrive/Claude/scripts/close-check.py`, outside this repo) learns to recognize a headless worker and skip the "close your tab" reminder for it.
  This is the one piece of the change that lands outside the plugin repo.

### browser-chauffeur ownership

A worker that stages a Slack or Teams draft drives browser-chauffeur, which ties each browser tab to an owner process so the tab is reclaimed when the owner is gone.
Headless, the owner is the background worker's own `claude` process, whose pid appears in `claude agents --json` after the spawn.
The spawn captures that pid and records it as the owner, so a browser tab a worker opens is reclaimed when that worker ends.

### Attention signal

The running list plus the `blocked` state already tell Russell which worker needs him.
An optional `Notification` hook can additionally write per-session state to a file or raise a desktop toast the moment a worker goes `blocked`, so Russell is not required to poll the list.
This is additive and can follow once the core path is trusted.

### Configuration and fallback

The whole headless path sits behind an off-by-default `headless_workers` config flag.
While it is off, workers spawn into Windows Terminal tabs exactly as they do now.
The tab path stays in place as the fallback until the headless path is proven in real use, so turning the flag off is always a clean retreat.

## Open decisions for Russell

1. **Permission mode.**
   The design uses `manual` so every irreversible step waits.
   Today's tab workers inherit the session default instead.
   Confirm `manual` is the intended posture for headless, or name the mode you want.
2. **Reaper vs reconcile for cleanup.**
   Settled background sessions can be removed by a small dedicated reaper or folded into the existing reconcile pass.
   Either works; the choice is about where the cleanup logic reads most cleanly.
3. **Cutover shape.**
   Flip all three spawn sites (fresh worker, digest, resume) to headless together, or cut over the fresh-worker path first and move digest and resume once it has run for a while.

## How the build proves out

1. Land the headless path behind the off-by-default flag, tab path unchanged as the default.
2. Turn the flag on for Russell's own machine and drain real items headless for a stretch, watching the Phase 1 list and the phone.
3. Once a headless worker has been dispatched, paused for approval, engaged from the phone, and cleaned up cleanly across every source, make headless the default and keep the tab path as the documented fallback.
