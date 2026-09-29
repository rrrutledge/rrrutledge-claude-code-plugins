"""Tests for the Slack known-contact rule: a DM/thread from someone Russell has already messaged is
never junk, and a Slack item's `phishing` kind (an email-shaped marker Slack has no action for) is
always cleared. Covers run-poller.py's `_apply_slack_overrides` (the deterministic post-triage rule)
and slack-adapter.py's `triage_signal` override (the same `knownContact` flag surfaced to the model).

Run directly:
    python plugins/drainer/tests/test_slack_known_contact.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)  # run-poller and the adapters import provider_base by bare name

import provider_base  # noqa: E402

poller_spec = importlib.util.spec_from_file_location("run_poller", os.path.join(SCRIPTS, "run-poller.py"))
poller = importlib.util.module_from_spec(poller_spec)
poller_spec.loader.exec_module(poller)

slack_spec = importlib.util.spec_from_file_location("slack_adapter", os.path.join(PROVIDERS, "slack-adapter.py"))
slack_adapter = importlib.util.module_from_spec(slack_spec)
slack_spec.loader.exec_module(slack_adapter)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def item(**kw):
    base = {"_id": "x1", "_source": "slack", "_bucket": "junk", "_kind": None, "_complexity": "simple"}
    base.update(kw)
    return base


# --- _apply_slack_overrides: a known contact is never junk -------------------------------------------
print("_apply_slack_overrides — a known-contact DM/thread bucketed junk becomes needs-you")

it = item(knownContact=True)
poller._apply_slack_overrides(it)
check("junk + knownContact -> needs-you", it["_bucket"], "needs-you")
check("kind becomes reply", it["_kind"], "reply")
check("complexity becomes simple", it["_complexity"], "simple")

# A phishing-kind junk item from a known contact is rescued the same way — the override doesn't care
# what triage's kind guess was, only that the sender is a known contact.
it = item(knownContact=True, _kind="phishing")
poller._apply_slack_overrides(it)
check("junk + knownContact + phishing kind -> needs-you", it["_bucket"], "needs-you")
check("kind is overwritten to reply, not left as phishing", it["_kind"], "reply")

print("\n_apply_slack_overrides — a first-ever message from a stranger still can be junk")
it = item(knownContact=False)
poller._apply_slack_overrides(it)
check("junk + stranger stays junk", it["_bucket"], "junk")

it = item()  # knownContact absent entirely (older capture, or a channel item) — treated as a stranger
poller._apply_slack_overrides(it)
check("junk + no knownContact key stays junk", it["_bucket"], "junk")

print("\n_apply_slack_overrides — only a junk verdict is overridden, not fyi")
it = item(_bucket="fyi", knownContact=True)
poller._apply_slack_overrides(it)
check("fyi + knownContact is left alone (fyi also poll-time-clears, so junk is the one that must flip)",
      it["_bucket"], "fyi")


# --- _apply_slack_overrides: phishing kind is always cleared on Slack --------------------------------
print("\n_apply_slack_overrides — Slack has no REPORT-PHISHING action, so the kind is always cleared")

it = item(knownContact=False, _kind="phishing")
poller._apply_slack_overrides(it)
check("junk + stranger + phishing -> bucket stays junk", it["_bucket"], "junk")
check("but the phishing kind is cleared to None", it["_kind"], None)

it = item(_bucket="needs-you", _kind="phishing", knownContact=False)
poller._apply_slack_overrides(it)
check("a needs-you item's phishing kind is also cleared", it["_kind"], None)


# --- _apply_slack_overrides: non-Slack sources are untouched -----------------------------------------
print("\n_apply_slack_overrides — a non-Slack source is never touched")

it = item(_source="outlook-graph", _kind="phishing", knownContact=True)
poller._apply_slack_overrides(it)
check("a Gmail/Outlook item's phishing kind survives (the marker still routes to report-phishing there)",
      it["_kind"], "phishing")
check("its bucket is untouched too", it["_bucket"], "junk")


# --- slack-adapter.py Provider.triage_signal: surfaces knownContact to the model ----------------------
print("\nProvider.triage_signal — merges knownContact on top of the base self-email signal")

p = slack_adapter.Provider.__new__(slack_adapter.Provider)  # no slack.js lookup needed for this method
check("a known contact is flagged for the model",
      p.triage_signal({"knownContact": True}), {"knownContact": True})
check("a stranger carries no signal (Slack items have no fromMe/toMe for the base signal either)",
      p.triage_signal({"knownContact": False}), None)
check("no knownContact key at all also carries no signal",
      p.triage_signal({}), None)

# The base ProviderBase.triage_signal only fires on fromMe+toMe, fields Slack items never carry — but
# prove the merge logic itself works so a future base-signal change stays composed correctly.
check("base and knownContact merge when both apply",
      p.triage_signal({"knownContact": True, "fromMe": True, "toMe": True}),
      {"selfEmail": True, "knownContact": True})

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
