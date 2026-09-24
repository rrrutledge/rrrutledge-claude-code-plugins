"""Shared reader for `.claude/drainer.local.md` — the per-machine drainer settings.

Both entry points use it: the fast-loop poller (`run-poller.py`) and the once-a-day digest
launcher (`run-digest.py`). Keeping the parse in one place means the two never drift on knob
names or defaults. Everything here is deterministic string/int parsing of the YAML frontmatter.

It also resolves the CONFIG ROOT the poller/digest read from (`ensure_main_worktree`): a drainer-owned
git worktree pinned to origin/main, so config always reflects merged main regardless of which branch a
human session left checked out at the real repo root.
"""
import os
import re
import subprocess

# The drainer-owned config worktree, pinned to origin/main. A stable, machine-local path next to the
# scheduled-task launcher (`~/.claude/drainer/`), independent of where the repo itself lives.
DEFAULT_MAIN_WORKTREE = os.path.join(
    os.path.expanduser("~"), ".claude", "drainer", "main-worktree")


def _git(args, timeout=60):
    """Run one git command, raising on a nonzero exit (so the caller's try/except can fall back).
    creationflags keeps it window-less under the pythonw poller."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=timeout, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ensure_main_worktree(source_repo, worktree=DEFAULT_MAIN_WORKTREE):
    """Return the path the poller/digest should read CONFIG from: a drainer-owned git worktree pinned
    to origin/main, refreshed to the latest merged main on every call.

    Why this exists: the poller/digest read their config (drainer.local.md knobs, context.md, provider
    overlays, trello-boards.yaml, initiatives/) from a working tree. Pointed at the real repo root, that
    tree sits on whatever branch a human session last checked out, so a merged config change stays
    dormant until someone restores main by hand. A worktree that ONLY the drainer owns, and that only
    ever holds origin/main, removes the coupling: config always reflects merged main.

    Resetting it every cycle is safe because nothing writes to it in place — worker tabs run with
    cwd = the real repo, and (per the git-workflow skill) any branch work creates its own worktree
    rather than checking out here, so this one stays a clean detached origin/main.

    Fail-safe: on any git error (offline, no origin, or a refresh race between the poller and the daily
    digest) this returns `source_repo`, so the drainer still runs against the real repo — the prior
    behavior — rather than not running at all.
    """
    try:
        _git(["-C", source_repo, "fetch", "--quiet", "origin", "main"], timeout=90)
        # A registered worktree carries a `.git` FILE (a gitdir pointer), not a directory.
        if os.path.isfile(os.path.join(worktree, ".git")):
            _git(["-C", worktree, "checkout", "--detach", "--force", "origin/main"])
        else:
            os.makedirs(os.path.dirname(worktree), exist_ok=True)  # parent only; git creates the leaf
            # --detach so no local branch can diverge; --force tolerates a stale registration at the path.
            _git(["-C", source_repo, "worktree", "add", "--detach", "--force", worktree, "origin/main"])
        return worktree
    except (OSError, subprocess.SubprocessError):
        return source_repo


def _read_local(repo):
    path = os.path.join(repo, ".claude", "drainer.local.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def parse_provider_names(text):
    """The immediate child keys under the `providers:` block (e.g. outlook-graph, trello)."""
    names, in_block = [], False
    for line in text.splitlines():
        if re.match(r"^\s*providers\s*:\s*$", line):
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line) or line.strip() in ("---", ""):
                if line.strip() == "":
                    continue
                if re.match(r"^\S", line):
                    break
            m = re.match(r"^\s{2}([A-Za-z0-9_-]+)\s*:", line)
            if m:
                names.append(m.group(1))
    return names


def read_config(repo, runtime_root=None):
    """Pull the scalar knobs + enabled provider names out of .claude/drainer.local.md frontmatter.

    `repo` is the CONFIG root — under the main-worktree setup this is the drainer-owned worktree pinned
    to origin/main (see `ensure_main_worktree`), so a feature branch left checked out at the real repo
    root can't make the poller read stale config. Everything config-shaped resolves against it:
    drainer.local.md itself, a relative `local_dir` (context.md, provider overlays), and `cfg["repo"]`
    (which the trello adapter re-reads for trello-boards.yaml).

    `runtime_root` is where RUNTIME state lives (seen.json, provider-health.json, digest-queue.json,
    seeds/, items/) — the REAL repo, passed separately so a relative `runtime_dir` stays anchored there
    and never migrates into the throwaway worktree. Defaults to `repo` when omitted (single-root callers
    and tests), preserving the original behavior."""
    text = _read_local(repo)
    runtime_root = runtime_root or repo

    def scalar(key, default):
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text, re.MULTILINE)
        return m.group(1).strip().strip('"\'') if m else default

    runtime_dir = scalar("runtime_dir", ".tmp/drainer")
    if not os.path.isabs(runtime_dir):
        runtime_dir = os.path.join(runtime_root, runtime_dir)
    # A relative local_dir resolves against the config root (the main-worktree), so context.md and the
    # provider overlays come from merged main; an absolute local_dir is honored verbatim (the pre-setup
    # form, and the safe no-op during rollout before drainer.local.md is switched to the relative value).
    local_dir = scalar("local_dir", "drainer-local")
    if not os.path.isabs(local_dir):
        local_dir = os.path.join(repo, local_dir)
    return {
        "providers": parse_provider_names(text),
        "repo": repo,  # so adapters that drain configured targets (e.g. trello boards) can re-read this file
        "runtime_dir": runtime_dir,
        "local_dir": local_dir,
        # The worker buffer, expressed as two numbers the dispatch loop reads together (see run-poller's
        # main dispatch). The point of the buffer was never "N workers" - it is "whenever Russell turns
        # his attention there is always something waiting for his review, so he is never idle waiting on
        # the AI". So each cycle the poller tops the WAITING pile up toward target_reviewable (any live
        # session not actively busy - a parked background worker, or an idle interactive session Russell
        # left open), opening `target_reviewable - waiting` fresh needs-you workers, floored at 0 - no
        # in-flight accounting. max_concurrent caps the TOTAL live-session load
        # as a load guard - every session in the Claude app, background drainer workers and interactive
        # sessions Russell started himself alike - so the drainer also backs off when he already has a lot
        # open, and it bounds the working pile-up that "open the difference" can create over several cycles
        # before fresh workers mature into the waiting state. At ~1 worker/minute matured and a 5-minute
        # cycle, a target of 5 keeps a full review buffer. Both are read from env each cycle (a fresh
        # process), so a changed value takes effect on the very next cycle.
        "target_reviewable": int(os.environ.get("DRAINER_TARGET_REVIEWABLE", "5")),
        "max_concurrent": int(os.environ.get("DRAINER_MAX_CONCURRENT", "18")),
        # Worker tabs need an explicit model — otherwise they inherit the session default, which may
        # not be what a given worker should run. The poller picks per item by triage complexity:
        # simple -> worker_model, complex -> worker_model_complex. Both models report a 1M context
        # window (`claude-opus-4-8`'s contextWindow is 1000000), so native auto-compact (~967K) is not
        # a usable cost control on its own — see `autocompact_window` below and the session-lifecycle
        # hook (`~/OneDrive/Claude/scripts/session-lifecycle.py`) for the actual per-turn cost control.
        "worker_model": scalar("worker_model", "claude-sonnet-5"),
        "worker_model_complex": scalar("worker_model_complex", "claude-opus-4-8"),
        # The triage call must also pin a model — under the scheduled task it has no parent session,
        # so it would otherwise inherit whatever the session default happens to be. Sonnet.
        "triage_model": scalar("triage_model", "claude-sonnet-5"),
        # The once-a-day digest session. It summarizes fyi and groups junk with source-stop
        # proposals - judgment-heavy, so a stronger model.
        "digest_model": scalar("digest_model", "claude-opus-4-8"),
        # The native `--autocompact` floor passed to a spawned headless worker (spawn_bg): the dumb,
        # cooperation-free backstop for a single autonomous run that executes a long tool-call chain
        # without ever yielding a turn — the one case the session-lifecycle hook's UserPromptSubmit
        # judgment can't see, since nothing fires mid-run. Well under the 1M window so it actually
        # bites before the window itself would force a compact.
        "autocompact_window": int(scalar("autocompact_window", "250000")),
        # A CLAUDE_CONFIG_DIR to run every unattended Claude launch under — triage calls, worker tabs,
        # and the digest — so they draw from a dedicated background Claude subscription instead of the
        # account Russell types into interactively. Empty (the default) leaves CLAUDE_CONFIG_DIR unset,
        # so those launches use the same account as everything else — the prior behavior. Set it in the
        # machine-local drainer.local.md once that background account's CLI is authenticated under the
        # given directory. The interactive-worker path (spawn-tab.cmd) and the triage subprocess both
        # read this; the orphan-session resume path deliberately does not (it reopens Russell's own
        # sessions, which only his account can see).
        "background_config_dir": scalar("background_config_dir", ""),
        # Reconcile grace: a dispatched item with no live worker session is treated as unfinished and
        # re-queued, but only once it's been launched at least this many minutes - so a just-dispatched
        # tab whose session file isn't written yet isn't misread as dead.
        "orphan_grace_minutes": int(scalar("orphan_grace_minutes", "15")),
        # Wall-clock time (HH:MM, 24h) the daily digest task fires; consumed by the installer.
        "digest_time": scalar("digest_time", "17:00"),
    }


def provider_search_dirs(plugin_providers_dir, local_dir):
    """Where a provider's files are looked up, in order: the plugin's own `providers/`, then the
    machine-local `<local_dir>/providers/`. The plugin ships the generic, identity-free providers; a
    machine keeps work- or personal-specific providers (that shouldn't live in the shared plugin) in
    `<local_dir>/providers/` and enables them by name in drainer.local.md exactly like a shipped one."""
    dirs = [plugin_providers_dir]
    if local_dir:
        dirs.append(os.path.join(local_dir, "providers"))
    return dirs


def find_provider_file(plugin_providers_dir, local_dir, name, suffix):
    """Resolve `<name><suffix>` (e.g. `-adapter.py` / `-provider.md`) across the search dirs above.
    Returns the first existing path, or None if no dir has it."""
    for d in provider_search_dirs(plugin_providers_dir, local_dir):
        path = os.path.join(d, f"{name}{suffix}")
        if os.path.exists(path):
            return path
    return None
