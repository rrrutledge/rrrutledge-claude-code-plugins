"""Tests for the Junk-folder rescue: a Junk item never dispatches a worker. Genuine junk and screen-flagged
items stay in Junk; everything else is un-junked into the Inbox for the outlook-graph provider to work, so one
email is never worked from two providers at once.

Run directly:
    python plugins/drainer/tests/test_junk_rescue.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")

sys.path.insert(0, SCRIPTS)  # run-poller and the adapters import their siblings by bare name
import provider_base  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller = _load("run_poller", os.path.join(SCRIPTS, "run-poller.py"))
junk = _load("outlook_graph_junk_adapter", os.path.join(PROVIDERS, "outlook-graph-junk-adapter.py"))

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class _Res:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


print("\nsplit_junk_items routes each Junk-folder item by triage and screen")
items = [
    {"_id": "j-junk", "_source": "outlook-graph-junk", "_bucket": "junk"},
    {"_id": "j-need", "_source": "outlook-graph-junk", "_bucket": "needs-you"},
    {"_id": "j-fyi", "_source": "outlook-graph-junk", "_bucket": "fyi"},
    {"_id": "j-auto", "_source": "outlook-graph-junk", "_bucket": "auto-handle"},
    {"_id": "j-flag", "_source": "outlook-graph-junk", "_bucket": "needs-you", "_screen": {"flagged": True}},
    {"_id": "i-need", "_source": "outlook-graph", "_bucket": "needs-you"},
    {"_id": "t-card", "_source": "trello", "_bucket": "needs-you"},
]
stay, rescue, rest = poller.split_junk_items(items)
check("genuine junk and the screen-flagged one stay in Junk", [i["_id"] for i in stay], ["j-junk", "j-flag"])
check("non-junk, unflagged items are rescued", [i["_id"] for i in rescue], ["j-need", "j-fyi", "j-auto"])
check("other providers' items pass through untouched", [i["_id"] for i in rest], ["i-need", "t-card"])

print("\nJunk adapter rescue() un-junks via mail.js --not-junk and reports success/failure")
jp = junk.Provider.__new__(junk.Provider)  # skip __init__ (which locates mail.js)
jp.mailjs = "MAILJS"
calls = []
junk.run_node = lambda args, **kw: (calls.append(args) or _Res(0))
check("rescue -> True on rc 0", jp.rescue({"id": "MID1"}), True)
check("called mail.js --not-junk=<id>", calls and calls[0], ["MAILJS", "--not-junk=MID1"])
junk.run_node = lambda args, **kw: _Res(1)
check("rescue -> False on failure (poller leaves it unrecorded)", jp.rescue({"id": "MID1"}), False)

print("\nProviderBase.rescue default is None (a provider that dispatches its own items)")
check("base rescue -> None", provider_base.ProviderBase().rescue({"id": "x"}), None)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
