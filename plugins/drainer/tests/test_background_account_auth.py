"""Tests for detecting and alerting on the drainer's background triage/screen account going dark: its
OAuth session expiring with no working refresh token silently starved every AI-triaged source (email,
Slack, Zoom) for two days before this existed, because the failure never touched provider-health.json
and the once-a-day digest had nothing to surface. See run-poller.py's BackgroundAccountAuthError and
BACKGROUND_ACCOUNT_HEALTH_KEY.

Run directly:
    python plugins/drainer/tests/test_background_account_auth.py
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

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


# --- _is_account_auth_failure: the real captured shape, and what must NOT trip it -----------------
print("_is_account_auth_failure classification")

auth_json = json.dumps({
    "is_error": True,
    "result": "Failed to authenticate: OAuth session expired and could not be refreshed",
})
check("the real captured auth-failure payload at exit 1 -> True",
      poller._is_account_auth_failure(1, auth_json, ""), True)
check("the same phrase surfacing in stderr instead of stdout -> True (fallback scan)",
      poller._is_account_auth_failure(1, "", "OAuth session expired and could not be refreshed"), True)
check("exit code 0 is never an auth failure, even with the phrase present",
      poller._is_account_auth_failure(0, auth_json, ""), False)
check("a plain network timeout (empty output) -> False",
      poller._is_account_auth_failure(1, "", ""), False)
check("an unparseable / non-JSON stdout -> False, not a crash",
      poller._is_account_auth_failure(1, "not json at all {{{", "connection reset"), False)
check("a successful-shaped JSON envelope with is_error unset -> False",
      poller._is_account_auth_failure(1, json.dumps({"result": "some other CLI error"}), ""), False)
check("a rate-limit error is not misclassified as an account auth failure",
      poller._is_account_auth_failure(1, json.dumps({"is_error": True, "result": "rate limited"}), ""), False)


# --- _triage_one / _screen_one only classify when this call was actually routed to the background
# account (bg_config_dir set) — the phrase match alone must not fire for a main-account call ---------
print("\n_triage_one / _screen_one gate the classification on bg_config_dir")


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


demo_item = {"_id": "x", "_source": "demo", "from": "", "subject": "", "preview": ""}
real_subprocess_run = poller.subprocess.run


def run_with_auth_failure():
    poller.subprocess.run = lambda *a, **k: FakeCompleted(1, auth_json, "")


try:
    run_with_auth_failure()
    poller._triage_one(demo_item, "BRAIN", "R", "M", {})  # no bg_config_dir
    check("no bg_config_dir: auth-shaped failure should have raised something", False, True)
except poller.BackgroundAccountAuthError:
    check("no bg_config_dir: auth-shaped failure is NOT classified as BackgroundAccountAuthError", False, True)
except poller.TriageUnavailable:
    check("no bg_config_dir: auth-shaped failure stays a plain TriageUnavailable", True, True)
finally:
    poller.subprocess.run = real_subprocess_run

try:
    run_with_auth_failure()
    poller._triage_one(demo_item, "BRAIN", "R", "M", {}, r"C:\Users\russe\.claude-background")
    check("bg_config_dir set: auth-shaped failure should have raised BackgroundAccountAuthError", False, True)
except poller.BackgroundAccountAuthError:
    check("bg_config_dir set: auth-shaped failure raises BackgroundAccountAuthError", True, True)
finally:
    poller.subprocess.run = real_subprocess_run

try:
    run_with_auth_failure()
    poller._screen_one(demo_item, "BRAIN", "R", "M", {}, r"C:\Users\russe\.claude-background")
    check("_screen_one: auth-shaped failure should have raised BackgroundAccountAuthError", False, True)
except poller.BackgroundAccountAuthError:
    check("_screen_one: auth-shaped failure raises BackgroundAccountAuthError", True, True)
finally:
    poller.subprocess.run = real_subprocess_run

try:
    poller.subprocess.run = lambda *a, **k: FakeCompleted(1, "", "network unreachable")
    poller._triage_one(demo_item, "BRAIN", "R", "M", {}, r"C:\Users\russe\.claude-background")
    check("bg_config_dir set: an ordinary network failure should have raised something", False, True)
except poller.BackgroundAccountAuthError:
    check("bg_config_dir set: an ordinary network failure is NOT misclassified as account auth", False, True)
except poller.TriageUnavailable:
    check("bg_config_dir set: an ordinary network failure stays a plain TriageUnavailable", True, True)
finally:
    poller.subprocess.run = real_subprocess_run

# BackgroundAccountAuthError IS-A TriageUnavailable, so existing fail-closed callers that only know
# about the base class still catch it.
check("BackgroundAccountAuthError is a TriageUnavailable subclass",
      issubclass(poller.BackgroundAccountAuthError, poller.TriageUnavailable), True)


# --- _background_account_alert_due: same cooldown shape as _config_alert_due ----------------------
print("\n_background_account_alert_due cooldown")

now = datetime.now(timezone.utc)
recent = (now - timedelta(minutes=5)).isoformat()
stale = (now - timedelta(seconds=poller.BACKGROUND_ACCOUNT_ALERT_COOLDOWN_SECONDS + 60)).isoformat()
KEY = poller.BACKGROUND_ACCOUNT_HEALTH_KEY

check("no health entry at all -> due", poller._background_account_alert_due({}), True)
check("never alerted -> due", poller._background_account_alert_due({KEY: {"consecutive_failures": 3}}), True)
check("alerted 5 min ago -> NOT due (still in cooldown)",
      poller._background_account_alert_due({KEY: {"last_alert_ts": recent}}), False)
check("alerted past the cooldown -> due again",
      poller._background_account_alert_due({KEY: {"last_alert_ts": stale}}), True)
check("a malformed timestamp fails open (due), never silently muted",
      poller._background_account_alert_due({KEY: {"last_alert_ts": "not-a-date"}}), True)


# --- the gate main() applies: consecutive failures reach the threshold + cooldown is due -----------
print("\nescalation gate")


def should_escalate(consecutive, health, dry_run=False):
    return bool(not dry_run
                and consecutive >= poller.BACKGROUND_ACCOUNT_ALERT_THRESHOLD
                and poller._background_account_alert_due(health))


check("first auth failure (below threshold) does NOT escalate yet", should_escalate(1, {}), False)
check("auth failure at the threshold escalates",
      should_escalate(poller.BACKGROUND_ACCOUNT_ALERT_THRESHOLD, {}), True)
check("still in cooldown does NOT re-escalate",
      should_escalate(99, {KEY: {"last_alert_ts": recent}}), False)
check("a dry-run never escalates (no worker tab from a manual diagnostic)",
      should_escalate(99, {}, dry_run=True), False)


# --- record_health_failure / record_health_ok round-trip under the new key -------------------------
print("\nhealth record round-trip")

health = {}
poller.record_health_failure(health, KEY, "background triage account: OAuth session expired", "auth")
check("a failure increments the streak", health[KEY]["consecutive_failures"], 1)
check("kind is recorded as auth (self-heals once Russell re-logs in)", health[KEY]["last_error_kind"], "auth")
poller.record_health_failure(health, KEY, "still expired", "auth")
check("a second consecutive failure increments again", health[KEY]["consecutive_failures"], 2)
poller.record_health_ok(health, KEY)
check("a recovered cycle resets the streak", health[KEY]["consecutive_failures"], 0)
check("last_ok_ts is stamped on recovery", health[KEY]["last_ok_ts"] is not None, True)


print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
