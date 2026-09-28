"""Tests for the poller's auth-failure self-heal + escalation: a provider whose `attempt_recovery()`
fixes an auth-kind failure gets a silent retry in the SAME cycle (no failure recorded at all), while a
provider whose recovery mechanism actively tried and failed earns an immediate diagnostic session —
gated by a consecutive-failure threshold and an hourly per-provider cooldown, mirroring the config-failure
escalation. A provider with no recovery mechanism at all (the default `ProviderBase.attempt_recovery`,
which every adapter except slack-adapter.py inherits unchanged) must see NO behavior change: this feature
is strictly additive for the one provider that opts in.

Run directly:
    python plugins/drainer/tests/test_auth_recovery.py
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


# --- has_recovery: exactly the check the poller's loop uses to opt a provider in --------------------
print("has_recovery detection (type(provider).attempt_recovery is not ProviderBase.attempt_recovery)")


class PlainProvider(provider_base.ProviderBase):
    name = "plain"


class SelfHealingProvider(provider_base.ProviderBase):
    name = "selfheal"

    def attempt_recovery(self, error_message):
        return "invalid_auth" in (error_message or "")


plain = PlainProvider()
healing = SelfHealingProvider()

check("a provider that never overrides attempt_recovery is NOT opted in",
      type(plain).attempt_recovery is not provider_base.ProviderBase.attempt_recovery, False)
check("a provider that overrides attempt_recovery IS opted in",
      type(healing).attempt_recovery is not provider_base.ProviderBase.attempt_recovery, True)
check("the default attempt_recovery always returns False (nothing to try)",
      plain.attempt_recovery("anything"), False)
check("an overriding provider's own logic runs, not the base no-op",
      healing.attempt_recovery("auth.test failed: invalid_auth"), True)
check("an overriding provider can still decline for an error it doesn't target",
      healing.attempt_recovery("network_error: Network request failed"), False)


# --- _auth_alert_due: same cooldown shape as _config_alert_due, on its own health key ----------------
print("\n_auth_alert_due cooldown")

now = datetime.now(timezone.utc)
recent = (now - timedelta(minutes=5)).isoformat()
stale = (now - timedelta(seconds=poller.AUTH_FAILURE_ALERT_COOLDOWN_SECONDS + 60)).isoformat()

check("no health entry at all -> due", poller._auth_alert_due({}, "slack"), True)
check("provider present but never alerted -> due",
      poller._auth_alert_due({"slack": {"consecutive_failures": 3}}, "slack"), True)
check("alerted 5 min ago -> NOT due (still in cooldown)",
      poller._auth_alert_due({"slack": {"last_auth_alert_ts": recent}}, "slack"), False)
check("alerted past the cooldown -> due again",
      poller._auth_alert_due({"slack": {"last_auth_alert_ts": stale}}, "slack"), True)
check("cooldown is keyed per provider (gmail's recent alert doesn't mute slack)",
      poller._auth_alert_due({"gmail": {"last_auth_alert_ts": recent}}, "slack"), True)
check("the config-alert and auth-alert cooldowns are independent keys on the same provider entry",
      poller._auth_alert_due({"slack": {"last_config_alert_ts": recent}}, "slack"), True)


# --- the escalation gate the enumerate loop applies after a FAILED recovery attempt ------------------
# Mirrors the exact boolean in main()'s enumerate-failure branch: escalate only when the failure is
# auth-kind, the provider actually has (and used) a recovery mechanism, the failure streak has reached
# the threshold, and the per-provider cooldown is due. A provider with no recovery mechanism must NEVER
# hit this path, however many consecutive auth failures it racks up — that is the "strictly additive,
# zero behavior change for everyone else" guarantee this whole feature depends on.
print("\nauth-recovery-failure escalation gate")


def should_escalate_auth(kind, has_recovery, consecutive, health, name="slack", dry_run=False):
    return bool(kind == "auth" and has_recovery and not dry_run
                and consecutive >= poller.AUTH_FAILURE_ALERT_THRESHOLD
                and poller._auth_alert_due(health, name))


check("a plain provider's auth failure NEVER escalates, however many times it repeats",
      should_escalate_auth("auth", False, 999, {}), False)
check("a self-healing provider's first failed attempt (below threshold) does NOT escalate yet",
      should_escalate_auth("auth", True, 1, {}), False)
check("a self-healing provider's failure at the threshold escalates",
      should_escalate_auth("auth", True, poller.AUTH_FAILURE_ALERT_THRESHOLD, {}), True)
check("a config-kind failure never takes this path, even with has_recovery True",
      should_escalate_auth("config", True, 999, {}), False)
check("still in cooldown does NOT re-escalate",
      should_escalate_auth("auth", True, 999, {"slack": {"last_auth_alert_ts": recent}}), False)
check("a dry-run never escalates (no worker tab from a manual diagnostic)",
      should_escalate_auth("auth", True, 999, {}, dry_run=True), False)


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
