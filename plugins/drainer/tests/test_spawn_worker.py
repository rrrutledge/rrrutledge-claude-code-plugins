"""Regression test for spawn_worker: it writes the whole seed prompt (ending on the repo-tracked-config line)
and the summary file, and hands the worker to spawn_bg as a headless `claude --bg` session, recording the
returned short id in the per-item receipt, without raising. A stray leading `+` on the last `f.write(`
argument once made every worker spawn crash with a TypeError.

Run directly:
    python plugins/drainer/tests/test_spawn_worker.py
"""
import importlib.util
import json
import os
import sys
import tempfile

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


def spawn(triage, config_repo, bg_id="abc123ef"):
    """Run spawn_worker against a temp runtime dir with spawn_bg captured; return
    (error, prompt, summary_exists, calls, receipt)."""
    calls = []

    def fake_spawn_bg(seed, model, cwd, name):
        calls.append({"seed": seed, "model": model, "cwd": cwd, "name": name})
        return bg_id

    poller.spawn_bg = fake_spawn_bg
    with tempfile.TemporaryDirectory() as tmp:
        runtime = os.path.join(tmp, "runtime")
        local_dir = os.path.join(tmp, "config")
        os.makedirs(local_dir)
        json_file = os.path.join(tmp, "item.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump({"id": "x1", "source": "trello", "subject": "Kirk Strobeck", "triage": triage}, f)
        error = None
        try:
            poller.spawn_worker("item-x1", json_file, tmp, runtime, "sonnet", local_dir, config_repo,
                                {"_source": "trello", "subject": "Kirk Strobeck"})
        except Exception as e:  # the regression under test: any exception here aborts the whole poll cycle
            error = e
        prompt_file = os.path.join(runtime, "seeds", "item-x1.prompt.txt")
        prompt = open(prompt_file, encoding="utf-8").read() if os.path.exists(prompt_file) else ""
        summary_exists = os.path.exists(os.path.join(runtime, "seeds", "item-x1.summary.txt"))
        receipt_path = prompt_file + ".session"
        receipt = open(receipt_path, encoding="utf-8").read() if os.path.exists(receipt_path) else None
    return error, prompt, summary_exists, calls, receipt


for triage in ("needs-you", "auto-handle"):
    print(f"\nspawn_worker ({triage}) writes the full seed, the summary, and spawns a headless worker")
    error, prompt, summary_exists, calls, receipt = spawn(triage, "C:/cfg/repo")
    check("does not raise", error, None)
    check("prompt ends with the repo-tracked config line",
          prompt.endswith("from the merged-main config repo `C:/cfg/repo`.\n"), True)
    check("prompt names the repo-tracked config", "Read repo-tracked drainer config" in prompt, True)
    check("summary file exists", summary_exists, True)
    check("spawn_bg called once", len(calls), 1)
    if calls:
        # The seed is the one-line launcher form: a descriptive lead, then the pointer at the on-disk
        # instructions file - never the whole prompt inline.
        check("seed points the worker at its prompt file",
              "open it and begin immediately without waiting for further input." in calls[0]["seed"], True)
        check("seed passes the worker model", calls[0]["model"], "sonnet")
    check("receipt records the returned bg short id", receipt, "abc123ef")

print("\nspawn_worker leaves no receipt when the headless launch returns no id")
error, prompt, summary_exists, calls, receipt = spawn("needs-you", "C:/cfg/repo", bg_id=None)
check("does not raise on a failed launch", error, None)
check("no receipt written when spawn_bg returns None", receipt, None)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
