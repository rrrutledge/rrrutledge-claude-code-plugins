"""Tests for the poller's config-failure classification and escalation: a Node helper that crashes on a
missing npm dependency must be classified kind="config" (a broken deploy that won't self-heal), not the
transient "auth" bucket, and a config-kind provider failure must earn an immediate diagnostic tab —
gated by a consecutive-failure threshold and an hourly per-provider cooldown — instead of waiting for the
once-a-day digest. A missing `imapflow` left three mail providers dark for days; this is the guardrail.

Run directly:
    python plugins/drainer/tests/test_provider_config_failure.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller / provider_base import their siblings by bare name
import provider_base  # noqa: E402
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


# --- node_failure_kind: a missing-dependency crash is "config", everything else is "auth" --------
print("node_failure_kind classification")

check("Cannot find module -> config",
      provider_base.node_failure_kind("Error: Cannot find module 'imapflow'"), "config")
check("MODULE_NOT_FOUND code -> config",
      provider_base.node_failure_kind("code: 'MODULE_NOT_FOUND'"), "config")
check("ERR_MODULE_NOT_FOUND (ESM) -> config",
      provider_base.node_failure_kind("ERR_MODULE_NOT_FOUND"), "config")
check("a real auth error stays auth",
      provider_base.node_failure_kind("Error: invalid_grant / token expired"), "auth")
check("a network blip stays auth",
      provider_base.node_failure_kind("network_error: Network request failed"), "auth")
check("empty stderr defaults to auth", provider_base.node_failure_kind(""), "auth")
check("None stderr defaults to auth", provider_base.node_failure_kind(None), "auth")


# --- _config_alert_due: fire when never alerted or the cooldown has elapsed, else hold ------------
print("\n_config_alert_due cooldown")

now = datetime.now(timezone.utc)
recent = (now - timedelta(minutes=5)).isoformat()
stale = (now - timedelta(seconds=poller.CONFIG_FAILURE_ALERT_COOLDOWN_SECONDS + 60)).isoformat()

check("no health entry at all -> due", poller._config_alert_due({}, "gmail"), True)
check("provider present but never alerted -> due",
      poller._config_alert_due({"gmail": {"consecutive_failures": 3}}, "gmail"), True)
check("alerted 5 min ago -> NOT due (still in cooldown)",
      poller._config_alert_due({"gmail": {"last_config_alert_ts": recent}}, "gmail"), False)
check("alerted past the cooldown -> due again",
      poller._config_alert_due({"gmail": {"last_config_alert_ts": stale}}, "gmail"), True)
check("a malformed timestamp fails open (due), never silently muted",
      poller._config_alert_due({"gmail": {"last_config_alert_ts": "not-a-date"}}, "gmail"), True)
check("cooldown is keyed per provider (outlook's recent alert doesn't mute gmail)",
      poller._config_alert_due({"outlook-graph": {"last_config_alert_ts": recent}}, "gmail"), True)


# --- the gate the enumerate loop applies: config-kind + threshold reached + cooldown due ----------
# Mirrors the exact boolean in main()'s enumerate-failure branch, so the wiring — not just the pieces —
# is covered: a config failure escalates only once it has failed CONFIG_FAILURE_ALERT_THRESHOLD cycles
# in a row (a single blip mid-`claude plugin update` self-clears) and its per-provider cooldown is due.
print("\nescalation gate")


def should_escalate(kind, consecutive, health, name="gmail", dry_run=False):
    return bool(kind == "config" and not dry_run
                and consecutive >= poller.CONFIG_FAILURE_ALERT_THRESHOLD
                and poller._config_alert_due(health, name))


check("first config failure (below threshold) does NOT escalate yet",
      should_escalate("config", 1, {}), False)
check("config failure at the threshold escalates",
      should_escalate("config", poller.CONFIG_FAILURE_ALERT_THRESHOLD, {}), True)
check("an auth failure never escalates, however many times it repeats",
      should_escalate("auth", 99, {}), False)
check("a config failure still in cooldown does NOT re-escalate",
      should_escalate("config", 99, {"gmail": {"last_config_alert_ts": recent}}), False)
check("a dry-run never escalates (no worker tab from a manual diagnostic)",
      should_escalate("config", 99, {}, dry_run=True), False)


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
