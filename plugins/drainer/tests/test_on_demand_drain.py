"""Tests for the on-demand drain of one Slack conversation (run-poller.py --channel / --now / --resume-me):
the flag rules, the open-worker lookup that names the session a held item waits behind, and the
resume-the-launcher instruction appended to a worker's seed.

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


def exit_code(argv, env_session=None):
    """main()'s exit code for argv when it stops at argument parsing, else None."""
    saved_argv, saved_env = sys.argv, os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    if env_session:
        os.environ["CLAUDE_CODE_SESSION_ID"] = env_session
    sys.argv = ["run-poller.py", "--repo", "."] + argv
    try:
        poller.main()
    except SystemExit as e:
        return e.code
    except Exception:
        return None
    finally:
        sys.argv = saved_argv
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if saved_env:
            os.environ["CLAUDE_CODE_SESSION_ID"] = saved_env
    return None


print("\nflag rules: --now and --resume-me only apply to an on-demand --channel run")
check("--now without --channel is refused", exit_code(["--now"]), 2)
check("--resume-me without --channel is refused", exit_code(["--resume-me"]), 2)
check("--resume-me with no launching session id is refused",
      exit_code(["--channel=D1", "--resume-me"]), 2)

print("\nlive_worker_items: each live worker with its captured item, so a conversation finds its worker")
rt = tempfile.mkdtemp(prefix="on-demand-")
os.makedirs(os.path.join(rt, "seeds"))
os.makedirs(os.path.join(rt, "items"))
for iid, guid, item in [("item-a", "guid-a", {"channel": "D1", "correspondent": None}),
                        ("item-b", "guid-dead", {"channel": "D2", "correspondent": "bob@ex.com"})]:
    with open(os.path.join(rt, "seeds", f"{iid}.prompt.txt.session"), "w", encoding="utf-8") as f:
        f.write(guid)
    with open(os.path.join(rt, "items", f"{iid}.json"), "w", encoding="utf-8") as f:
        json.dump(item, f)
check("only the live worker comes back, with its item",
      poller.live_worker_items(rt, {"guid-a"}), [({"channel": "D1", "correspondent": None}, "guid-a")])
check("a Slack item with no correspondent holds no correspondent open",
      poller.open_correspondents(rt, {"guid-a"}), set())
check("no live scan -> no live workers", poller.live_worker_items(rt, None), [])

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

if failures:
    print(f"\n{len(failures)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
