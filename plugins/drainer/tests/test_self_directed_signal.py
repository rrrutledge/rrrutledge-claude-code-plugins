"""Tests for the self-directed (Russell-to-Russell) triage signal: provider_base.self_directed, the
ProviderBase.triage_signal default that surfaces it, and run-poller's payload merge that carries it to
the triage model. Pure functions of the enumerate fields — no network or credentials needed.

Run directly:
    python plugins/drainer/tests/test_self_directed_signal.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name

# Import provider_base normally (no hyphen in its name) so it registers in sys.modules and the adapters,
# which do `from provider_base import ...`, share this exact module — the identity check below relies on it.
import provider_base  # noqa: E402

poller_spec = importlib.util.spec_from_file_location("run_poller", os.path.join(SCRIPTS, "run-poller.py"))
poller = importlib.util.module_from_spec(poller_spec)
poller_spec.loader.exec_module(poller)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


# --- self_directed: the from-and-to-me shape -------------------------------------------------------
print("self_directed")
check("both fromMe and toMe -> self-directed", provider_base.self_directed({"fromMe": True, "toMe": True}), True)
check("own outbound reply (fromMe only) is not self-directed",
      provider_base.self_directed({"fromMe": True, "toMe": False}), False)
check("plain inbound (toMe only) is not self-directed",
      provider_base.self_directed({"fromMe": False, "toMe": True}), False)
check("neither field (a platform source) is not self-directed", provider_base.self_directed({}), False)

# --- ProviderBase.triage_signal default flags the self-note ----------------------------------------
print("\nProviderBase.triage_signal default")
base = provider_base.ProviderBase()
check("a self-note is flagged selfEmail", base.triage_signal({"fromMe": True, "toMe": True}), {"selfEmail": True})
check("an own outbound reply carries no signal", base.triage_signal({"fromMe": True, "toMe": False}), None)
check("ordinary inbound mail carries no signal", base.triage_signal({"fromMe": False, "toMe": True}), None)
check("a source without the fields carries no signal", base.triage_signal({"subject": "hi"}), None)

# The email adapters don't override triage_signal, so they inherit the flag straight from the base — the
# same fromMe/toMe fields their enumerate already carries. Assert the inheritance (class attribute, no
# instantiation) so an accidental override that drops it would fail here.
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")


def _load_adapter(name):
    s = importlib.util.spec_from_file_location(f"{name}_adapter", os.path.join(PROVIDERS, f"{name}-adapter.py"))
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod.Provider


for adapter in ("outlook-graph", "gmail"):
    check(f"{adapter} inherits the base triage_signal (no override)",
          _load_adapter(adapter).triage_signal is provider_base.ProviderBase.triage_signal, True)

# --- the payload merge carries the flag to the model ----------------------------------------------
print("\n_triage_payload_item merge")
self_note = {"_id": "outlook-graph-1", "_source": "outlook-graph", "from": "Russell <russell.rutledge@outlook.com>",
             "subject": "New jobs", "received": "2026-09-10T12:00:00Z", "isRead": False,
             "fromMe": True, "toMe": True}
signal = base.triage_signal(self_note)
merged = poller._triage_payload_item(self_note, "stop applying to lower-level jobs", signal)
check("selfEmail lands in the triage payload", merged.get("selfEmail"), True)
check("the body text is carried as preview", merged.get("preview"), "stop applying to lower-level jobs")
check("the envelope from is preserved", merged.get("from"), "Russell <russell.rutledge@outlook.com>")

ordinary = {"_id": "outlook-graph-2", "_source": "outlook-graph", "from": "Bob <bob@x.com>",
            "subject": "Lunch?", "received": "2026-09-10T12:00:00Z", "isRead": False}
plain = poller._triage_payload_item(ordinary, "want to grab lunch?", base.triage_signal(ordinary))
check("ordinary mail gets no selfEmail key", "selfEmail" in plain, False)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
