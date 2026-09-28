"""Tests for the Slack provider's auth self-heal signal, and the poller's escalation on it.

The actual self-heal (retrying once on invalid_auth by re-deriving fresh creds from the persistent
browser session) lives in the `slack` skill's slack.js, not in Python - there's nothing here to unit-test
for that half. What IS testable on the Python side: slack-adapter.py's enumerate() must tell "slack.js's
own internal self-heal already tried and failed" (kind="auth-unrecoverable") apart from an ordinary auth
blip (kind="auth") by grepping slack.js's exact wording, and the poller's escalation gate must fire only
for the former - a config failure and a plain auth failure both look BEFORE, unchanged. A missing `imapflow`
left three mail providers dark for days before the config-failure escalation existed; this is the
analogous guardrail for a Slack credential that genuinely needs a human, instead of sitting dark until
the once-a-day digest.

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
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller / provider_base / the adapter import their siblings by bare name
import provider_base  # noqa: E402
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


slack = _load("slack_adapter_authrec", os.path.join(PROVIDERS, "slack-adapter.py"))

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class _Res:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- slack-adapter.py enumerate(): classify by whether slack.js already self-healed and still failed ---
print("slack-adapter enumerate() kind classification")

sl = slack.Provider.__new__(slack.Provider)  # skip __init__ (which locates slack.js)
sl.slackjs = "SLACKJS"

slack.run_node = lambda args, **kw: _Res(
    1, stderr="Error: list-unread failed: invalid_auth - auto-refresh did not fix it "
              "(check the persistent browser's own Slack sign-in, or SLACK_TEAM_ID if unset)")
try:
    sl.enumerate(50)
    check("raises on slack.js's own self-heal-failed wording", False, True)
except provider_base.ProviderError as e:
    check("kind is auth-unrecoverable when slack.js already tried and failed", e.kind, "auth-unrecoverable")

slack.run_node = lambda args, **kw: _Res(1, stderr="Error: list-unread failed: invalid_auth")
try:
    sl.enumerate(50)
    check("raises on a plain invalid_auth", False, True)
except provider_base.ProviderError as e:
    check("kind stays plain auth when slack.js never mentions its own self-heal", e.kind, "auth")

slack.run_node = lambda args, **kw: _Res(1, stderr="network_error: Network request failed")
try:
    sl.enumerate(50)
    check("raises on a network blip", False, True)
except provider_base.ProviderError as e:
    check("a plain network blip is also just auth, not auth-unrecoverable", e.kind, "auth")

slack.run_node = lambda args, **kw: _Res(0, stdout="[]")
check("a clean rc 0 enumerate returns normally", sl.enumerate(50), [])


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


# --- the escalation gate the enumerate loop applies -----------------------------------------------
# Mirrors the exact boolean in main()'s enumerate-failure branch: escalate only on kind="auth-unrecoverable"
# (which only a provider whose own transport tried and failed a self-heal ever raises), at the threshold,
# with the cooldown due. A plain "auth" failure — however many times it repeats, from ANY provider — must
# NEVER take this path; that is the "every other provider's behavior is completely unchanged" guarantee.
print("\nauth-unrecoverable escalation gate")


def should_escalate_auth(kind, consecutive, health, name="slack", dry_run=False):
    return bool(kind == "auth-unrecoverable" and not dry_run
                and consecutive >= poller.AUTH_FAILURE_ALERT_THRESHOLD
                and poller._auth_alert_due(health, name))


check("a plain auth failure NEVER escalates via this path, however many times it repeats",
      should_escalate_auth("auth", 999, {}), False)
check("an unrecoverable failure below threshold does NOT escalate yet",
      should_escalate_auth("auth-unrecoverable", 1, {}), False)
check("an unrecoverable failure at the threshold escalates",
      should_escalate_auth("auth-unrecoverable", poller.AUTH_FAILURE_ALERT_THRESHOLD, {}), True)
check("a config-kind failure never takes this path",
      should_escalate_auth("config", 999, {}), False)
check("still in cooldown does NOT re-escalate",
      should_escalate_auth("auth-unrecoverable", 999, {"slack": {"last_auth_alert_ts": recent}}), False)
check("a dry-run never escalates (no worker tab from a manual diagnostic)",
      should_escalate_auth("auth-unrecoverable", 999, {}, dry_run=True), False)


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
