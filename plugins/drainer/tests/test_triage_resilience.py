"""Tests for run-poller.py's triage resilience: triage() must not lose the rest of a batch's
verdicts to a single item's TriageUnavailable (a subprocess timeout or launch failure), and
_triage_one() must turn a malformed or missing model reply into a TriageUnavailable rather than
a SystemExit that would kill the whole poller process.

Run directly:
    python plugins/drainer/tests/test_triage_resilience.py
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def item(iid):
    return {"_id": iid, "_source": "demo", "from": "", "subject": "", "preview": ""}


# --- a mid-batch TriageUnavailable does not lose the other items' verdicts -----------------
print("one item unavailable, others still verdict")

ITEMS = [item("a"), item("b"), item("c"), item("d")]


def fake_triage_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    if it["_id"] == "c":
        raise poller.TriageUnavailable(f"{it['_id']}: triage call timed out after 420s (likely a network drop)")
    return {"id": it["_id"], "bucket": "fyi", "kind": "read"}


poller._triage_brain = lambda items, repo, local_dir, providers_by_name: "BRAIN"
real_triage_one = poller._triage_one
poller._triage_one = fake_triage_one

verdicts, unavailable = poller.triage(ITEMS, repo="R", local_dir="L", model="M", providers_by_name={})

check("the 3 reachable items still got verdicts", sorted(verdicts.keys()), ["a", "b", "d"])
check("the unreachable item is NOT silently defaulted into verdicts", "c" in verdicts, False)
check("the unreachable item lands in unavailable, not verdicts", unavailable, {"c"})

# --- a TriageUnavailable on the FIRST (cache-priming) item still processes the rest ---------
print("\nfirst item unavailable")


def fake_first_fails(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    if it["_id"] == "a":
        raise poller.TriageUnavailable("a: couldn't launch the triage call")
    return {"id": it["_id"], "bucket": "junk", "kind": "read"}


poller._triage_one = fake_first_fails
verdicts2, unavailable2 = poller.triage(ITEMS, repo="R", local_dir="L", model="M", providers_by_name={})
check("first item's failure is recorded, not raised", unavailable2, {"a"})
check("the rest still got triaged despite the first item failing", sorted(verdicts2.keys()), ["b", "c", "d"])

# --- everything unavailable (e.g. the network is fully down) yields no verdicts, no crash ---
print("\nall items unavailable")


def fake_all_fail(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    raise poller.TriageUnavailable(f"{it['_id']}: triage call timed out after 420s (likely a network drop)")


poller._triage_one = fake_all_fail
verdicts3, unavailable3 = poller.triage(ITEMS, repo="R", local_dir="L", model="M", providers_by_name={})
check("no verdicts when every call is unavailable", verdicts3, {})
check("every item id is reported unavailable", unavailable3, {"a", "b", "c", "d"})

poller._triage_one = real_triage_one

# --- empty batch is unaffected ---------------------------------------------------------------
print("\nempty batch")
check("empty items returns empty verdicts and empty unavailable set", poller.triage([], "R", "L", "M", {}), ({}, set()))


# --- _triage_one's own response parsing: a malformed reply must raise TriageUnavailable, never
# SystemExit — a SystemExit here is what used to kill the whole poller process on a bad reply ----
print("\n_triage_one response parsing")


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_triage_one_with_stdout(model_reply_text, returncode=0, stderr=""):
    # Mirrors `claude --output-format json`, which wraps the model's raw reply in {"result": "..."}.
    envelope = json.dumps({"result": model_reply_text})
    poller.run_subprocess_bounded = lambda *a, **k: FakeCompleted(returncode, envelope, stderr)
    return poller._triage_one({"_id": "x", "_source": "demo", "from": "", "subject": "", "preview": ""},
                               "BRAIN", "R", "M", {})


real_run_subprocess_bounded = poller.run_subprocess_bounded

try:
    got = run_triage_one_with_stdout('[{"id": "x", "bucket": "junk", "kind": "read"}]')
    check("a well-formed array reply still parses to its one verdict", got, {"id": "x", "bucket": "junk", "kind": "read"})
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

try:
    got = run_triage_one_with_stdout('{"id": "x", "bucket": "junk", "kind": "read"}')
    check("a bare object reply (no [ ] wrapper) is accepted, not fatal", got, {"id": "x", "bucket": "junk", "kind": "read"})
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

try:
    run_triage_one_with_stdout("the model rambled and returned no JSON at all")
    check("no-JSON reply should have raised TriageUnavailable", False, True)
except poller.TriageUnavailable:
    check("no-JSON reply raises TriageUnavailable, not SystemExit", True, True)
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

try:
    run_triage_one_with_stdout('[{"id": "x", "bucket": "junk" "kind": "read"}]')  # missing comma
    check("malformed-JSON array reply should have raised TriageUnavailable", False, True)
except poller.TriageUnavailable:
    check("malformed-JSON array reply raises TriageUnavailable, not SystemExit", True, True)
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

try:
    run_triage_one_with_stdout("", returncode=1, stderr="rate limited")
    check("nonzero CLI exit should have raised TriageUnavailable", False, True)
except poller.TriageUnavailable:
    check("nonzero CLI exit raises TriageUnavailable, not SystemExit", True, True)
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

# --- bg_config_dir routes ONLY the triage subprocess to the background account -------------------
# Triage is the one Claude launch that moves accounts; passing a background CLAUDE_CONFIG_DIR must set
# it on this subprocess's env and nowhere else. Absent it, env stays None so the call inherits ambient.
print("\nbackground-account routing")

captured = {}
def capture_run(*a, **k):
    captured["env"] = k.get("env")
    return FakeCompleted(0, json.dumps({"result": '[{"id": "x", "bucket": "junk", "kind": "read"}]'}), "")

demo_item = {"_id": "x", "_source": "demo", "from": "", "subject": "", "preview": ""}

poller.run_subprocess_bounded = capture_run
try:
    poller._triage_one(demo_item, "BRAIN", "R", "M", {}, r"C:\Users\russe\.claude-background")
    env = captured["env"] or {}
    check("bg_config_dir sets CLAUDE_CONFIG_DIR on the triage subprocess env",
          env.get("CLAUDE_CONFIG_DIR"), r"C:\Users\russe\.claude-background")
    check("the triage env still inherits the parent environment (not just the one override)",
          len(env) > 1, True)
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

poller.run_subprocess_bounded = capture_run
try:
    poller._triage_one(demo_item, "BRAIN", "R", "M", {})
    check("no bg_config_dir leaves the triage env unset, so the call inherits the ambient account",
          captured["env"], None)
finally:
    poller.run_subprocess_bounded = real_run_subprocess_bounded

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
