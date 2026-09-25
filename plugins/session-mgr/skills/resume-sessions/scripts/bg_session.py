"""The one place a Claude session is launched: a headless `claude --bg` background session, reachable
from claude.ai/code and the phone over Remote Control. Every automated launch goes through spawn_bg -
drainer workers, handoffs, diagnostics, the daily digest, crash resumes, resume-on-completion - either
by importing this module or by running spawn-session.py, its command-line front end. Nothing opens a
Windows Terminal tab.

Two facts about background sessions shape this module:
  - A `claude --bg` call doesn't run the session itself. It hands it to a long-running `claude daemon`
    and exits, and the session inherits the daemon's environment, not the launching command's. So a
    value that has to be current in the session travels in a per-launch `--settings` env block
    (_settings_env), not in the launch command's environment.
  - There is one daemon per Claude account (CLAUDE_CONFIG_DIR), and `claude agents` lists only the
    sessions of the account it runs under. So a launch passes the current account explicitly
    (_launch_env), and anything counting or checking sessions reads every account (claude_agents).

Stdlib only: the drainer imports it straight from the installed plugin, and session-mgr's hook and
scripts import it from their own tree.
"""
import glob
import json
import os
import re
import shutil
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The tools an automated session never uses - their definitions would otherwise ride in every model
# call's prompt for no purpose. PowerShell is denied so the session uses the Bash tool, per
# ~/.claude/CLAUDE.md.
BG_DISALLOWED_TOOLS = "Artifact,Workflow,SendFeedback,PowerShell"

# `claude --bg` prints:  backgrounded · <shortId> · <name>
# Capture the short id (the sessionId's first hyphen-delimited segment) - the handle `claude
# attach/logs/stop/rm` take. `[^0-9a-f]*` skips the middot and spaces after "backgrounded" up to the
# id. Color codes are stripped first (_ANSI_RE): launched from inside a session, FORCE_COLOR is set,
# so claude wraps the id in an escape like `\x1b[36m` whose own digits would otherwise stop the skip
# short of the real id.
_BG_ID_RE = re.compile(r"backgrounded[^0-9a-f]*([0-9a-f]{6,})", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Vars a running Claude session sets for its own tool subprocesses. A launch from inside a session (a
# handoff) inherits them. They are cleared from the launch env so the `claude --bg` client, and the
# daemon that client starts when none is running yet, never take on the launching session's identity:
# its session id and pid, its job dir, messaging socket, bridge id and effort, and the profile's
# terminal host pid.
_SESSION_ENV = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_HOST_PID",
                "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_BRIDGE_SESSION_ID",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_JOB_DIR", "CLAUDE_EFFORT")

# User-level toggles a session's hooks read, re-read from the registry at every launch and handed to
# the session in its --settings env block, since the daemon's copy dates from whenever it started.
# Only non-secret values belong here: `claude --bg` saves its launch flags in the job's state.json so
# the daemon can respawn the session, so anything passed this way lands on disk.
FRESH_USER_ENV = ("CLAUDE_NOTIFY_SOUND",)

DEFAULT_CONFIG_DIR = "~/.claude"
# The config dir of every Claude account a session has run on, beyond the default one. session-mgr's
# SessionStart hook and every launch add their own account's dir (remember_account), so the list
# builds itself as accounts come into use - nothing names an account up front. It sits beside the
# live-session registry, which every account's sessions share.
ACCOUNTS_PATH = os.path.expanduser("~/.claude/session-mgr/accounts.json")


def run_bounded(args, timeout, **kw):
    """Run `args` with a hard `timeout`, killing the whole process tree on expiry (a lone kill can leave
    a grandchild holding the output pipes open, so the wait never ends). Returns a CompletedProcess;
    a timeout comes back as returncode 1."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=NO_WINDOW)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = "", ""
        return subprocess.CompletedProcess(args, 1, out or "", (err or "") + f"\ntimed out after {timeout}s")


# ------------------------------------------------------------------------------------------ accounts

def _user_env(name):
    """`(known, value)` for a User-level environment variable in the registry. `known` is False off
    Windows (no registry to read), so the caller falls back to the inherited value there."""
    try:
        import winreg
    except ImportError:
        return False, None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return True, winreg.QueryValueEx(key, name)[0] or None
    except OSError:
        return True, None


def current_config_dir():
    """The config dir of the account new sessions launch on: the User-level CLAUDE_CONFIG_DIR that
    `claude-account main|backup` last wrote, or None for the main account (the default dir). A
    long-lived launcher - the scheduled poller, or a session started before the switch - inherited
    whatever was current when it started, so the registry is read fresh every time."""
    known, value = _user_env("CLAUDE_CONFIG_DIR")
    return value if known else os.environ.get("CLAUDE_CONFIG_DIR") or None


def _norm(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _known_accounts():
    """The account dirs remember_account has recorded, or [] when there is no readable list."""
    try:
        with open(ACCOUNTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, str)] if isinstance(data, list) else []


def remember_account(config_dir):
    """Add `config_dir` (a CLAUDE_CONFIG_DIR value; None or the default dir is the main account, which
    is always known) to the known-accounts list. Best effort: a list that can't be written only means
    the account is found again at its next session start."""
    if not config_dir or _norm(config_dir) == _norm(DEFAULT_CONFIG_DIR):
        return
    known = _known_accounts()
    if any(_norm(d) == _norm(config_dir) for d in known):
        return
    try:
        os.makedirs(os.path.dirname(ACCOUNTS_PATH), exist_ok=True)
        tmp_path = ACCOUNTS_PATH + f".tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(known + [os.path.abspath(os.path.expanduser(config_dir))], f, indent=2)
        os.replace(tmp_path, ACCOUNTS_PATH)
    except OSError:
        pass


def account_config_dirs():
    """Every account's config dir to read sessions from, as the CLAUDE_CONFIG_DIR value to run under:
    None for the main account first, then each other account's dir that exists here - every account
    remember_account has recorded, plus whatever is current or inherited."""
    dirs, seen = [None], {_norm(DEFAULT_CONFIG_DIR)}
    for d in (*_known_accounts(), current_config_dir(), os.environ.get("CLAUDE_CONFIG_DIR")):
        if d and os.path.isdir(os.path.expanduser(d)) and _norm(d) not in seen:
            seen.add(_norm(d))
            dirs.append(os.path.expanduser(d))
    return dirs


def _account_env(config_dir, base=None):
    """`base` (default: this process's env) with CLAUDE_CONFIG_DIR set to `config_dir`, or removed
    for the main account."""
    env = dict(os.environ if base is None else base)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def transcript_account(session_guid):
    """The config dir (None = main) of the account holding `session_guid`'s transcript, or False when
    no account has it. A session resumes on the account that owns its history, since a resume reads
    the transcript from that account's projects folder."""
    for d in account_config_dirs():
        root = os.path.expanduser(d or DEFAULT_CONFIG_DIR)
        if glob.glob(os.path.join(root, "projects", "*", session_guid + ".jsonl")):
            return d
    return False


def claude_agents():
    """Every Claude session on this machine across every account: the merged `claude agents --json`
    lists, each entry carrying `id` (the short id, background sessions only), `sessionId` (the full
    guid), `kind` (interactive/background), `cwd`, `name`, `pid`/`status` while running, plus
    `configDir` (the account's CLAUDE_CONFIG_DIR, None for main). None when no account's list could
    be read, so a caller can tell "nothing running" from "couldn't look"."""
    claude = shutil.which("claude") or "claude"
    merged, seen, any_ok = [], set(), False
    for d in account_config_dirs():
        try:
            res = run_bounded([claude, "agents", "--json"], timeout=30, env=_account_env(d),
                              creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode != 0:
            continue
        try:
            data = json.loads(res.stdout or "[]")
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        any_ok = True
        for a in data:
            if not isinstance(a, dict):
                continue
            key = a.get("sessionId") or a.get("id")
            if key in seen:
                continue
            seen.add(key)
            merged.append({**a, "configDir": d})
    return merged if any_ok else None


# ---------------------------------------------------------------------------------------- the launch

def _launch_env():
    """The env the `claude --bg` call runs under: the launcher's own, minus _SESSION_ENV, on the
    current account."""
    env = {k: v for k, v in os.environ.items() if k not in _SESSION_ENV}
    return _account_env(current_config_dir(), env)


def _settings_env():
    """The --settings JSON handing the session current values the daemon's env may have stale:
    FRESH_USER_ENV from the registry, and an empty CLAUDE_HOST_PID - a daemon started from a
    profile-loaded PowerShell inherits that terminal's host pid, which is never a background
    session's own."""
    env = {"CLAUDE_HOST_PID": ""}
    for name in FRESH_USER_ENV:
        known, value = _user_env(name)
        value = value if known else os.environ.get(name)
        if value:
            env[name] = value
    return json.dumps({"env": env})


def spawn_bg(seed, model, cwd, name, resume=None):
    """Launch a headless background Claude session with `claude --bg` and return the short session id
    claude prints (hand it to write_receipt), or None when the launch fails or the id can't be parsed.

    Load-bearing details:
      - `--remote-control` is what puts the session on claude.ai/code and the phone app.
        `remoteControlAtStartup` only auto-connects a normal interactive launch, never `--bg`, so
        without the flag the session runs but is reachable only from a terminal on this machine.
      - `--permission-mode manual` is the safety anchor: every action the safe-compounds hook does not
        auto-approve pauses for Russell, so reaching a "send" becomes the blocked state rather than an
        autonomous send. The hook still auto-approves safe commands, so day to day the session runs
        on its own; only the final irreversible steps wait.
      - `--` precedes the seed because `--disallowedTools` is variadic and would otherwise swallow the
        seed as another tool name, leaving the session idle with no prompt.
      - The call runs on the current account (_launch_env) and hands the session current values
        through `--settings` (_settings_env), since the session's own env comes from the daemon.
    `resume` takes an existing session's full guid: `claude --bg --resume <guid>` continues it in the
    background with its full history, on the account that holds its transcript. `seed` may then be
    None to reopen it with nothing queued, and `model`/`name` None to keep the ones it already has.
    The continuation runs under a new session id, so the resumed guid leaves the live-session
    registry (forget_session) - its history lives on in the new session, which registers itself.
    """
    args = ["claude", "--bg", "--remote-control", "--permission-mode", "manual"]
    env = _launch_env()
    if resume:
        args += ["--resume", resume]
        owner = transcript_account(resume)
        if owner is not False:
            env = _account_env(owner, env)
    if name:
        args += ["--name", name]
    if model:
        args += ["--model", model]
    args += ["--settings", _settings_env(), "--disallowedTools", BG_DISALLOWED_TOOLS]
    if seed:
        args += ["--", seed]
    try:
        res = run_bounded(args, timeout=120, cwd=cwd, env=env, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    m = _BG_ID_RE.search(_ANSI_RE.sub("", res.stdout or ""))
    if m:
        remember_account(env.get("CLAUDE_CONFIG_DIR"))
    if m and resume:
        forget_session(resume)
    return m.group(1) if m else None


REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")


def forget_session(session_guid):
    """Drop `session_guid` from session-mgr's live-session registry (hooks/session_registry.py), the
    list find-orphans reads. A resumed session continues under a new id, so without this the old
    entry would stay listed as a crashed session forever. Best effort: an unreadable registry is left
    alone."""
    for _ in range(5):
        try:
            with open(REGISTRY_PATH, encoding="utf-8") as f:
                registry = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(registry, dict) or session_guid not in registry:
            return
        registry.pop(session_guid)
        tmp_path = REGISTRY_PATH + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)
            os.replace(tmp_path, REGISTRY_PATH)
            return
        except OSError:
            continue


def prompt_seed(prompt_file, lead=""):
    """The one-line seed for a session whose instructions live in `prompt_file`: an optional lead (a
    descriptive first line), then the pointer at the file. The instructions stay on disk, so only
    the lead and a path ever reach the command line."""
    lead = re.sub(r"\s+", " ", lead or "").strip()
    return ((lead + " ") if lead else "") + (
        f"Your task instructions are in '{prompt_file}' - open it and begin immediately "
        "without waiting for further input.")


def brief_seed(brief_file):
    """The one-line seed for a handoff: a pointer at the handoff doc holding the full brief."""
    return (f"Resume from the handoff at {brief_file}. Read it fully, then begin the work it describes "
            "without waiting for further input.")


def session_name(text, fallback, maxlen=50):
    """`text` as a session `--name`: whitespace collapsed to one line and cut to `maxlen` characters
    (the Claude app's session list truncates a long one anyway). `fallback` stands in when nothing is
    left."""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:maxlen].strip() or fallback


def session_guid(short_id, agents=None):
    """The full session guid behind a background session's short id, from claude_agents (or the
    `agents` list given), or None when it isn't listed. The short id is the guid's first segment, but
    the rest can't be derived from it, and a transcript is named `<guid>.jsonl`."""
    for agent in (claude_agents() if agents is None else agents) or []:
        if agent.get("id") == short_id and agent.get("sessionId"):
            return agent["sessionId"]
    return None


def write_receipt(anchor_file, short_id):
    """Write a launched session's id to `<anchor_file>.session` - the receipt the drainer's liveness
    and reconcile checks and peek.py read - and return what was written: the full guid when
    claude_agents lists the session, else the short id. Readers match either form."""
    ident = session_guid(short_id) or short_id
    with open(anchor_file + ".session", "w", encoding="utf-8") as f:
        f.write(ident)
    return ident


def launch(*, cwd, model=None, name=None, brief=None, prompt_file=None, lead="", resume=None):
    """Seed and launch one session - the shared body of spawn-session.py and every in-process caller.
    Exactly one of `brief`/`prompt_file`, or `resume` alone, or `resume` with one of them as a
    follow-up. Returns `(short_id, receipt)`: `receipt` is what write_receipt wrote beside the brief
    or prompt file, or the new session's guid (short id if unlisted) when there is no file;
    `(None, None)` when nothing launched."""
    anchor = brief or prompt_file
    seed = brief_seed(brief) if brief else prompt_seed(prompt_file, lead) if prompt_file else None
    short_id = spawn_bg(seed, model, cwd, name, resume=resume)
    if not short_id:
        return None, None
    return short_id, (write_receipt(anchor, short_id) if anchor else session_guid(short_id) or short_id)


# ------------------------------------------------------------------------------------ self-knowledge

def is_background_session(env=None):
    """True when the calling process runs inside a `claude --bg` session: its job dir's state.json
    names the daemon backend. A session started by hand in a terminal has no such job, whatever else
    its env holds - CLAUDE_HOST_PID in particular, which a daemon started from a profile-loaded
    PowerShell passes on to every background session."""
    env = os.environ if env is None else env
    job_dir = env.get("CLAUDE_JOB_DIR")
    if not job_dir:
        return False
    try:
        with open(os.path.join(job_dir, "state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and (state.get("backend") == "daemon" or state.get("template") == "bg")
