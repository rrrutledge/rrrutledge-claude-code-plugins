"""Tests for the on-demand drain of one Slack conversation (run-poller.py --channel / --now / --resume-me):
the flag rules, the resume-the-launcher instruction appended to a worker's seed, and a worker that
can't carry it staying unlaunched.

Run directly:
    python plugins/drainer/tests/test_on_demand_drain.py
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

sys.path.insert(0, SCRIPTS)
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


def exit_code(argv):
    """main()'s exit code for argv when it stops at argument parsing, else None. No launching session id
    is set, so --resume-me always lacks one here."""
    saved_argv, saved_env = sys.argv, os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    sys.argv = ["run-poller.py", "--repo", "."] + argv
    try:
        poller.main()
    except SystemExit as e:
        return e.code
    except Exception:
        return None
    finally:
        sys.argv = saved_argv
        if saved_env:
            os.environ["CLAUDE_CODE_SESSION_ID"] = saved_env
    return None


print("\nflag rules: --now and --resume-me only apply to an on-demand --channel run")
check("--now without --channel is refused", exit_code(["--now"]), 2)
check("--resume-me without --channel is refused", exit_code(["--resume-me"]), 2)
check("--resume-me with no launching session id is refused", exit_code(["--channel=D1", "--resume-me"]), 2)

rt = tempfile.mkdtemp(prefix="on-demand-")
os.makedirs(os.path.join(rt, "seeds"))
os.makedirs(os.path.join(rt, "items"))

print("\n_append_resume_launcher: the worker seed gets session-mgr's resume instruction")
seed = os.path.join(rt, "seeds", "item-a.prompt.txt")
with open(seed, "w", encoding="utf-8") as f:
    f.write("You are a drainer worker handling ONE item.\n")
command = poller._append_resume_launcher(seed, "launcher-guid", "Slack DM from Addie")
with open(seed, encoding="utf-8") as f:
    body = f.read()
check("a resume command comes back", bool(command), True)
check("it resumes the launching session", "--resume launcher-guid" in (command or ""), True)
check("the seed carries the resume heading",
      "## When this is done, resume the session that launched you" in body, True)
check("the seed carries the command", (command or "") in body, True)
check("the wake note sits beside the seed", os.path.exists(seed + ".resume-note.md"), True)

print("\nspawn_worker with --resume-me: a worker that can't carry the resume instruction never launches")
launches, marks = [], []
poller.spawn_bg = lambda *a, **k: launches.append(a) or "bg-1"
poller.write_receipt = lambda path, ident: ident
poller._append_resume_launcher = lambda *a: None
poller._mark_launcher_resume = lambda *a: marks.append(a)
item_json = os.path.join(rt, "items", "item-c.json")
with open(item_json, "w", encoding="utf-8") as f:
    json.dump({"id": "c", "source": "slack", "subject": "DM from Addie"}, f)
local_dir = os.path.join(rt, "config")
os.makedirs(local_dir)
got = poller.spawn_worker("item-c", item_json, rt, rt, "sonnet", local_dir, rt, {"_source": "slack"},
                          resume_launcher="launcher-guid")
check("spawn_worker reports no launch", got, False)
check("nothing launched", launches, [])
poller._append_resume_launcher = lambda *a: "python spawn-session.py --resume launcher-guid"
got = poller.spawn_worker("item-c", item_json, rt, rt, "sonnet", local_dir, rt, {"_source": "slack"},
                          resume_launcher="launcher-guid")
check("with the instruction in place it launches", (got, len(launches)), (True, 1))
check("and records the launcher's resume marker", [m[0] for m in marks], ["launcher-guid"])

if failures:
    print(f"\n{len(failures)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
