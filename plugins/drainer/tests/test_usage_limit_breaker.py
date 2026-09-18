"""Tests for the background-account circuit breaker: when a `claude -p` call is refused because the
account is out of usage (or its login lapsed), the cycle stops launching further calls, and later cycles
skip the AI step until the account can serve a call again.

Run directly:
    python plugins/drainer/tests/test_usage_limit_breaker.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)
import usage_limit  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


CENTRAL = timezone(timedelta(hours=-5))  # Central Daylight Time, the zone the refusal messages name
LIMIT_TEXT = "You've hit your weekly limit · resets Sep 17, 5pm (America/Chicago)"
AUTH_TEXT = "Failed to authenticate: OAuth session expired and could not be refreshed"


def envelope(result, is_error=True):
    return json.dumps({"type": "result", "is_error": is_error, "result": result})


# --- recognising a refusal --------------------------------------------------------------------------------
print("detecting a refusal")
check("a usage-limit result is recognised", usage_limit.detect(envelope(LIMIT_TEXT))[0], "usage-limit")
check("an expired login is recognised", usage_limit.detect(envelope(AUTH_TEXT))[0], "auth")
check("a refusal on stderr with a nonzero exit is recognised",
      usage_limit.detect("", LIMIT_TEXT)[0], "usage-limit")
check("an unparseable stdout that opens with the message is recognised",
      usage_limit.detect(LIMIT_TEXT)[0], "usage-limit")
check("the message line is kept", usage_limit.detect(envelope(LIMIT_TEXT))[1], LIMIT_TEXT)
check("a real verdict is not a refusal",
      usage_limit.detect(envelope('[{"id": "x", "bucket": "fyi"}]', is_error=False)), None)
check("a verdict quoting a limit notice in its reason is not a refusal",
      usage_limit.detect(envelope('[{"id": "x", "bucket": "fyi", "reason": "Anthropic email: You\'ve hit your '
                                  'weekly limit"}]', is_error=False)), None)
check("a plain rate-limit failure is left as an ordinary failure",
      usage_limit.detect("", "API Error: 429 rate limited"), None)

# --- when to retry --------------------------------------------------------------------------------------------
print("\nwhen to retry")
now = datetime(2026, 9, 16, 0, 10, tzinfo=CENTRAL)
check("a dated reset resolves to just after that moment",
      usage_limit.retry_at("usage-limit", LIMIT_TEXT, now),
      datetime(2026, 9, 17, 17, 1, tzinfo=CENTRAL))
check("a time-only reset later today is today",
      usage_limit.retry_at("usage-limit", "You've hit your limit · resets 5pm (America/Chicago)", now),
      datetime(2026, 9, 16, 17, 1, tzinfo=CENTRAL))
check("a time-only reset already past today is tomorrow",
      usage_limit.retry_at("usage-limit", "You've hit your limit · resets 12:05am (America/Chicago)", now),
      datetime(2026, 9, 17, 0, 6, tzinfo=CENTRAL))
check("an unparseable reset backs off an hour",
      usage_limit.retry_at("usage-limit", "You've hit your weekly limit", now), now + timedelta(minutes=60))
check("a reset absurdly far away is treated as unparseable",
      usage_limit.retry_at("usage-limit", "You've hit your limit · resets Dec 30, 5pm", now),
      now + timedelta(minutes=60))
check("an auth failure backs off half an hour", usage_limit.retry_at("auth", AUTH_TEXT, now), now + timedelta(minutes=30))

# --- _triage_one / _screen_one turn a refusal into UsageLimitReached ------------------------------------------
print("\nthe per-item calls raise UsageLimitReached")


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


real_run = poller.subprocess.run
demo = {"_id": "x", "_source": "demo", "from": "", "subject": "", "preview": ""}

for fn_name, call in (("triage", lambda: poller._triage_one(demo, "BRAIN", "R", "M", {})),
                      ("screen", lambda: poller._screen_one(demo, "BRAIN", "R", "M", {}))):
    for label, completed in (("an is_error result on a zero exit", FakeCompleted(0, envelope(LIMIT_TEXT))),
                             ("stderr on a nonzero exit", FakeCompleted(1, "", LIMIT_TEXT))):
        poller.subprocess.run = lambda *a, **k: completed
        try:
            call()
            check(f"{fn_name}: {label} should raise", False, True)
        except poller.UsageLimitReached as e:
            check(f"{fn_name}: {label} raises UsageLimitReached", e.kind, "usage-limit")
        finally:
            poller.subprocess.run = real_run

poller.subprocess.run = lambda *a, **k: FakeCompleted(1, "", "rate limited")
try:
    poller._triage_one(demo, "BRAIN", "R", "M", {})
except poller.UsageLimitReached:
    check("an ordinary failure is not a UsageLimitReached", True, False)
except poller.TriageUnavailable:
    check("an ordinary failure stays a plain TriageUnavailable", True, True)
finally:
    poller.subprocess.run = real_run

# --- the first refusal stops the cycle's remaining calls ---------------------------------------------------------
print("\nthe first refusal stops the cycle")
calls = []


def refusing_triage_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    calls.append(("triage", it["_id"]))
    raise poller.UsageLimitReached(f"{it['_id']}: triage call refused (usage-limit): {LIMIT_TEXT}",
                                   "usage-limit", datetime.now().astimezone() + timedelta(hours=40))


def refusing_screen_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    calls.append(("screen", it["_id"]))
    raise poller.UsageLimitReached(f"{it['_id']}: screen call refused (usage-limit): {LIMIT_TEXT}",
                                   "usage-limit", datetime.now().astimezone() + timedelta(hours=40))


poller._triage_brain = lambda items, repo, local_dir, providers_by_name: "BRAIN"
poller._screen_brain = lambda items, local_dir: "BRAIN"
poller._triage_one, poller._screen_one = refusing_triage_one, refusing_screen_one
ids = [f"i{n}" for n in range(8)]
batch = [{"_id": i, "_source": "demo", "from": "", "subject": "", "preview": ""} for i in ids]

rt = tempfile.mkdtemp()
breaker = poller.UsageBreaker(rt)
verdicts, unavailable = poller.triage(batch, "R", "L", "M", {}, None, breaker)
check("only the first (cache-priming) call is launched", calls, [("triage", "i0")])
check("no verdict comes back", verdicts, {})
check("every item is reported unavailable, so it is held", unavailable, set(ids))
check("the breaker is tripped", breaker.tripped, True)

del calls[:]
s_verdicts, s_unavailable = poller.screen_items(batch, "R", "L", "M", {}, None, breaker)
check("a tripped breaker launches no screen call either", calls, [])
check("every item is held by the screen too", s_unavailable, set(ids))

print("\nthe backoff is persisted until the reset")
with open(os.path.join(rt, poller.BACKOFF_FILE), encoding="utf-8") as f:
    saved = json.load(f)
check("the kind is stored", saved["kind"], "usage-limit")
check("the message is stored", "weekly limit" in saved["message"], True)
until = datetime.fromisoformat(saved["until"])

later = datetime.now(timezone.utc)
check("a new cycle before the reset starts backed off", poller.UsageBreaker(rt).check(later), until)
check("a new cycle after the reset probes the account again",
      poller.UsageBreaker(rt).check(until + timedelta(seconds=1)), None)

print("\ndry-run reads the backoff but never writes it")
rt_dry = tempfile.mkdtemp()
dry = poller.UsageBreaker(rt_dry, persist=False)
poller.triage(batch, "R", "L", "M", {}, None, dry)
check("the trip is held in memory", dry.tripped, True)
check("no backoff file is written", os.path.exists(os.path.join(rt_dry, poller.BACKOFF_FILE)), False)

# --- a backed-off cycle skips the AI step but still dispatches cached items ------------------------------------------
print("\na backed-off cycle: skip the AI step, keep serving the cache")
del calls[:]
rt = tempfile.mkdtemp()
CFG = {"background_config_dir": "", "local_dir": "L", "triage_model": "M"}


class Provider:
    name = "demo"

    def triage_text(self, item):
        return item["preview"]


PROVIDERS = {"demo": Provider()}
cache = poller.VerdictCache(rt)
now_utc = datetime.now(timezone.utc)
cached_item = batch[0]
h = poller.content_hash("", "", "")
cache.record(cached_item, h, now_utc, triage={"id": "i0", "bucket": "needs-you", "kind": "reply", "complexity": "simple"},
             screen={"flagged": False, "reason": ""})
cache.save()

breaker = poller.UsageBreaker(rt)
breaker.trip("usage-limit", datetime.now(timezone.utc) + timedelta(hours=3), LIMIT_TEXT)
breaker = poller.UsageBreaker(rt)  # a later cycle loading the persisted state
check("the persisted backoff trips the breaker", breaker.check(datetime.now(timezone.utc)) is not None, True)
verdicts, t_unavail, screens, s_unavail = poller.judge_items(batch[:3], "R", CFG, PROVIDERS,
                                                            poller.VerdictCache(rt), breaker)
check("no claude call is made while backed off", calls, [])
check("the cached item still has its verdicts", ("i0" in verdicts, "i0" in screens), (True, True))
check("an uncached item is held for triage", t_unavail, {"i1", "i2"})
check("an uncached item is held for the screen", s_unavail, {"i1", "i2"})

print("\nonce the backoff elapses the AI step resumes")
recovered = []


def fake_triage_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    recovered.append(("triage", it["_id"]))
    return {"id": it["_id"], "bucket": "needs-you", "kind": "reply", "complexity": "simple"}


def fake_screen_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    recovered.append(("screen", it["_id"]))
    return {"flagged": False, "reason": ""}


poller._triage_one, poller._screen_one = fake_triage_one, fake_screen_one
breaker = poller.UsageBreaker(rt)
check("a check past the reset finds no backoff", breaker.check(datetime.now(timezone.utc) + timedelta(hours=4)), None)
verdicts, t_unavail, screens, s_unavail = poller.judge_items(batch[:3], "R", CFG, PROVIDERS,
                                                            poller.VerdictCache(rt), breaker)
check("the uncached items are judged again", sorted(recovered),
      [("screen", "i1"), ("screen", "i2"), ("triage", "i1"), ("triage", "i2")])
check("nothing is held", (t_unavail, s_unavail), (set(), set()))

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
