"""Headless-worker path: spawn_bg command/env/id-parsing, the agents-json liveness and attention-budget
branches, and the reconcile reaper that removes background workers their worker marked done.

Run directly:
    python plugins/drainer/tests/test_headless_workers.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "drainer", "scripts"))
sys.path.insert(0, SCRIPTS)  # run-poller / provider_base import their siblings by bare name


def load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


provider_base = load("provider_base", "provider_base.py")
poller = load("run_poller", "run-poller.py")

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class FakeRun:
    """A stand-in for subprocess.run that records the call and returns a canned result."""

    def __init__(self, stdout="", returncode=0, raise_exc=None):
        self.stdout, self.returncode, self.raise_exc = stdout, returncode, raise_exc
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.raise_exc:
            raise self.raise_exc
        return types.SimpleNamespace(stdout=self.stdout, returncode=self.returncode, stderr="")


# ---------------------------------------------------------------------------- spawn_bg
print("\nspawn_bg builds the right command, clears the right env, and parses the short id")
fake = FakeRun(stdout="Starting background service\u2026\nbackgrounded \u00b7 9e40dfb1 \u00b7 my-worker\n")
orig_run = provider_base.subprocess.run
provider_base.subprocess.run = fake
os.environ["BROWSER_CHAUFFEUR_OWNER_PID"] = "11292"
os.environ["CLAUDE_PID"] = "31920"
os.environ["CLAUDE_CODE_SESSION_ID"] = "parent-guid"
try:
    got_id = provider_base.spawn_bg("do the thing", "claude-sonnet-5", "C:/repo", "My Worker")
finally:
    provider_base.subprocess.run = orig_run
args = fake.calls[0][0]
env = fake.calls[0][1].get("env", {})
check("returns the parsed short id", got_id, "9e40dfb1")
check("command is claude --bg", args[:2], ["claude", "--bg"])
check("permission mode is manual", args[args.index("--permission-mode") + 1], "manual")
check("names the worker", args[args.index("--name") + 1], "My Worker")
check("disallows the four unused tools",
      args[args.index("--disallowedTools") + 1], "Artifact,Workflow,SendFeedback,PowerShell")
check("a -- separates the seed so the variadic can't swallow it", args[-2:], ["--", "do the thing"])
check("cwd is passed through", fake.calls[0][1].get("cwd"), "C:/repo")
for var in ("BROWSER_CHAUFFEUR_OWNER_PID", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_CHILD_SESSION", "BROWSER_CHAUFFEUR_OWNER_START"):
    check(f"launch env clears {var}", var in env, False)

print("\nspawn_bg returns None when the launch fails or prints no id")
for label, f in (("nonzero exit", FakeRun(stdout="boom", returncode=1)),
                 ("no id in output", FakeRun(stdout="Starting background service\u2026\n")),
                 ("launch raises", FakeRun(raise_exc=OSError("no claude")))):
    provider_base.subprocess.run = f
    try:
        check(f"None on {label}", provider_base.spawn_bg("s", "m", "cwd", "n"), None)
    finally:
        provider_base.subprocess.run = orig_run

# ---------------------------------------------------------------------------- liveness + budget
print("\nlive_session_ids(headless) returns the live background session short ids")
agents = [
    {"id": "aaaa1111", "kind": "background", "status": "working", "state": "working"},
    {"id": "bbbb2222", "kind": "background", "status": "idle", "state": "blocked"},
    {"pid": 1, "kind": "interactive", "status": "busy", "sessionId": "cccc-3333"},
]
orig_agents = poller._claude_agents
poller._claude_agents = lambda: agents
try:
    check("only background ids, both states", poller.live_session_ids(headless=True), {"aaaa1111", "bbbb2222"})
    check("total_claude_tabs counts ALL sessions (interactive + background)",
          poller.total_claude_tabs(headless=True), 3)
    poller._claude_agents = lambda: None
    check("live_session_ids is None when agents can't be read", poller.live_session_ids(headless=True), None)
    check("total_claude_tabs is None (fail-closed) when agents can't be read",
          poller.total_claude_tabs(headless=True), None)
finally:
    poller._claude_agents = orig_agents

# ---------------------------------------------------------------------------- reaper
print("\n_reap_done_bg_workers reaps only receipts carrying a .done marker")
reap_run = FakeRun()
orig_poller_run = poller.subprocess.run
poller.subprocess.run = reap_run
with tempfile.TemporaryDirectory() as tmp:
    seeds = os.path.join(tmp, "seeds")
    os.makedirs(seeds)
    # done worker: receipt + marker
    done_receipt = os.path.join(seeds, "done.prompt.txt.session")
    with open(done_receipt, "w", encoding="utf-8") as f:
        f.write("dddd4444")
    open(done_receipt + ".done", "w").close()
    # parked worker: receipt, NO marker
    parked_receipt = os.path.join(seeds, "parked.prompt.txt.session")
    with open(parked_receipt, "w", encoding="utf-8") as f:
        f.write("pppp5555")
    try:
        n = poller._reap_done_bg_workers(tmp)
    finally:
        poller.subprocess.run = orig_poller_run
    check("reaped exactly the done worker", n, 1)
    check("done receipt removed", os.path.exists(done_receipt), False)
    check("done marker removed", os.path.exists(done_receipt + ".done"), False)
    check("parked receipt untouched", os.path.exists(parked_receipt), True)
    reaped_ids = [c[0][2] for c in reap_run.calls if len(c[0]) >= 3 and c[0][1] == "rm"]
    check("claude rm called on the done worker's id", reaped_ids, ["dddd4444"])

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
