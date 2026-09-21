"""Regression test for spawn_worker: it writes the whole seed prompt (ending on the repo-tracked-config line)
and the summary file, and hands the tab to spawn_tab, without raising. A stray leading `+` on the last
`f.write(` argument once made every worker spawn crash with a TypeError.

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


def spawn(triage, config_repo):
    """Run spawn_worker against a temp runtime dir with spawn_tab captured; return (error, prompt, summary_exists, spawned)."""
    spawned = []
    poller.spawn_tab = lambda cmd, cwd=None: spawned.append(cmd)
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
    return error, prompt, summary_exists, spawned


for triage in ("needs-you", "auto-handle"):
    print(f"\nspawn_worker ({triage}) writes the full seed, the summary, and opens the tab")
    error, prompt, summary_exists, spawned = spawn(triage, "C:/cfg/repo")
    check("does not raise", error, None)
    check("prompt ends with the repo-tracked config line",
          prompt.endswith("from the merged-main config repo `C:/cfg/repo`.\n"), True)
    check("prompt names the repo-tracked config", "Read repo-tracked drainer config" in prompt, True)
    check("summary file exists", summary_exists, True)
    check("spawn_tab called once", len(spawned), 1)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
