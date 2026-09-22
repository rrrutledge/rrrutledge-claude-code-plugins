"""Tests for archive-fyi/junk-at-triage: ProviderBase.clear's default and the two inbox adapters plus
the slack adapter's clear() calling their reversible archive/mark-read CLEAR.

Run directly:
    python plugins/drainer/tests/test_poll_clear.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")

sys.path.insert(0, SCRIPTS)  # the adapters import their siblings (provider_base) by bare name


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import provider_base  # noqa: E402  (on sys.path via SCRIPTS)
outlook = _load("outlook_graph_adapter", os.path.join(PROVIDERS, "outlook-graph-adapter.py"))
gmail = _load("gmail_adapter", os.path.join(PROVIDERS, "gmail-adapter.py"))
slack = _load("slack_adapter", os.path.join(PROVIDERS, "slack-adapter.py"))

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


print("\nProviderBase.clear default is None (a provider with no safe poll-time archive)")
check("base clear -> None", provider_base.ProviderBase().clear({"id": "x"}), None)


print("\noutlook-graph adapter clear() archives via mail.js --delete and reports success/failure")
og = outlook.Provider.__new__(outlook.Provider)  # skip __init__ (which locates mail.js)
og.mailjs = "MAILJS"
calls = []
outlook.run_node = lambda args, **kw: (calls.append(args) or _Res(0))
check("clear -> True on rc 0", og.clear({"id": "MID1"}), True)
check("called mail.js --delete=<id>", calls and calls[0], ["MAILJS", "--delete=MID1"])
outlook.run_node = lambda args, **kw: _Res(1)
check("clear -> False on rc 1", og.clear({"id": "MID1"}), False)


print("\ngmail adapter clear() archives via gmail.js --archive and reports success/failure")
gm = gmail.Provider.__new__(gmail.Provider)
gm.gmailjs = "GMAILJS"
gcalls = []
gmail.run_node = lambda args, **kw: (gcalls.append(args) or _Res(0))
check("clear -> True on rc 0", gm.clear({"id": "GID1"}), True)
check("called gmail.js --archive=<id>", gcalls and gcalls[0], ["GMAILJS", "--archive=GID1"])
gmail.run_node = lambda args, **kw: _Res(2)
check("clear -> False on nonzero rc", gm.clear({"id": "GID1"}), False)


print("\nslack adapter clear() marks read via slack.js --mark and reports success/failure")
sl = slack.Provider.__new__(slack.Provider)  # skip __init__ (which locates slack.js)
sl.slackjs = "SLACKJS"
scalls = []
slack.run_node = lambda args, **kw: (scalls.append(args) or _Res(0))
check("clear -> True on rc 0", sl.clear({"channel": "C1", "ts": "111.1"}), True)
check("called slack.js --mark --channel --ts",
      scalls and scalls[0], ["SLACKJS", "--mark", "--channel=C1", "--ts=111.1"])
scalls.clear()
check("thread item clear -> True on rc 0",
      sl.clear({"channel": "C1", "ts": "111.1", "threadTs": "100.0"}), True)
check("thread clear appends --thread-ts",
      scalls and scalls[0], ["SLACKJS", "--mark", "--channel=C1", "--ts=111.1", "--thread-ts=100.0"])
slack.run_node = lambda args, **kw: _Res(1)
check("clear -> False on rc 1", sl.clear({"channel": "C1", "ts": "111.1"}), False)


print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
