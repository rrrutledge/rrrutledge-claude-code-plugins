"""Tests for run-poller.py's judged-verdict cache: an item the dispatch step holds (worker buffer full,
same-correspondent hold) re-enumerates every cycle, and must not be triaged and screened again
each time. judge_items() reads a cached verdict back when the item's text is unchanged and the verdict is
under a day old, and caches only verdicts a model really returned.

Run directly:
    python plugins/drainer/tests/test_verdict_cache.py
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

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class Provider:
    name = "demo"

    def triage_text(self, item):
        return item["preview"]


PROVIDERS = {"demo": Provider()}
CFG = {"background_config_dir": "", "local_dir": "L", "triage_model": "M"}

triage_calls, screen_calls = [], []
triage_verdict = {}   # item id -> what the fake model answers (default: needs-you)
unavailable_triage, unavailable_screen = set(), set()
screen_flags = set()  # item ids the fake screen flags


def item(iid, preview="hello"):
    return {"_id": iid, "_source": "demo", "from": "a@b.c", "subject": f"subject {iid}", "preview": preview}


def fake_triage_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    triage_calls.append(it["_id"])
    if it["_id"] in unavailable_triage:
        raise poller.TriageUnavailable(f"{it['_id']}: triage call timed out")
    return triage_verdict.get(it["_id"], {"id": it["_id"], "bucket": "needs-you", "kind": "reply", "complexity": "simple"})


def fake_screen_one(it, brain, repo, model, providers_by_name, bg_config_dir=None):
    screen_calls.append(it["_id"])
    if it["_id"] in unavailable_screen:
        raise poller.TriageUnavailable(f"{it['_id']}: screen call timed out")
    if it["_id"] in screen_flags:
        return {"flagged": True, "reason": "asks to wire money"}
    return {"flagged": False, "reason": ""}


poller._triage_brain = lambda items, repo, local_dir, providers_by_name: "BRAIN"
poller._screen_brain = lambda items, local_dir: "BRAIN"
poller._triage_one = fake_triage_one
poller._screen_one = fake_screen_one


def reset():
    del triage_calls[:], screen_calls[:]
    triage_verdict.clear()
    unavailable_triage.clear()
    unavailable_screen.clear()
    screen_flags.clear()


def cycle(items, runtime_dir, persist=True):
    """One poller cycle's AI step against the on-disk state, exactly as main() drives it."""
    cache = poller.VerdictCache(runtime_dir, persist=persist)
    breaker = poller.UsageBreaker(runtime_dir, persist=persist)
    cache.prune({}, datetime.now(timezone.utc))
    out = poller.judge_items(items, "R", CFG, PROVIDERS, cache, breaker)
    cache.save()
    return out


# --- a held item is judged once, then read back --------------------------------------------------------
print("held item is not re-judged on the next cycle")
reset()
rt = tempfile.mkdtemp()
items = [item("a"), item("b")]
verdicts, t_unavail, screens, s_unavail = cycle(items, rt)
check("cycle 1 triages both", sorted(triage_calls), ["a", "b"])
check("cycle 1 screens both", sorted(screen_calls), ["a", "b"])
check("cycle 1 returns a verdict for each", sorted(verdicts), ["a", "b"])

del triage_calls[:], screen_calls[:]
verdicts2, t_unavail2, screens2, s_unavail2 = cycle(items, rt)
check("cycle 2 makes no triage call", triage_calls, [])
check("cycle 2 makes no screen call", screen_calls, [])
check("cycle 2 returns the same triage verdicts", verdicts2, verdicts)
check("cycle 2 returns the same screen verdicts", screens2, screens)
check("nothing is reported unavailable", (t_unavail2, s_unavail2), (set(), set()))

# --- a fresh-cache hit never launches claude -------------------------------------------------------------
print("\na cache hit never calls claude")


def no_claude(*a, **k):
    raise AssertionError("claude was launched for a cached item")


# A second copy of the module supplies the real _triage_one/_screen_one, so a stray call for a cached
# item would reach run_subprocess_bounded and trip no_claude. Patched on `real` (not `poller`) because
# `real._triage_one`/`_screen_one` resolve `run_subprocess_bounded` out of their OWN module's globals
# (bound at import time by `from provider_base import run_subprocess_bounded`), not poller's.
spec_real = importlib.util.spec_from_file_location("run_poller_real", POLLER)
real = importlib.util.module_from_spec(spec_real)
spec_real.loader.exec_module(real)
poller._triage_one, poller._screen_one = real._triage_one, real._screen_one
real_run = real.run_subprocess_bounded
real.run_subprocess_bounded = no_claude
try:
    verdicts3, _, screens3, _ = cycle(items, rt)
    check("cached verdicts are served with claude unreachable", sorted(verdicts3), ["a", "b"])
finally:
    real.run_subprocess_bounded = real_run
    poller._triage_one, poller._screen_one = fake_triage_one, fake_screen_one

# --- changed content or an expired TTL re-judges -----------------------------------------------------------
print("\nchanged content re-judges")
reset()
rt = tempfile.mkdtemp()
cycle([item("a", "first message")], rt)
del triage_calls[:], screen_calls[:]
cycle([item("a", "first message plus a newer one")], rt)
check("grown text is triaged again", triage_calls, ["a"])
check("grown text is screened again", screen_calls, ["a"])

del triage_calls[:], screen_calls[:]
changed_subject = item("a", "first message plus a newer one")
changed_subject["subject"] = "a different subject"
cycle([changed_subject], rt)
check("a changed subject is judged again", triage_calls, ["a"])

print("\nan expired verdict re-judges")
reset()
rt = tempfile.mkdtemp()
cycle([item("a")], rt)
path = os.path.join(rt, poller.VERDICTS_FILE)
with open(path, encoding="utf-8") as f:
    stored = json.load(f)
old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
stored["a"]["triage"]["ts"] = old
stored["a"]["screen"]["ts"] = old
with open(path, "w", encoding="utf-8") as f:
    json.dump(stored, f)
del triage_calls[:], screen_calls[:]
cycle([item("a")], rt)
check("a 25-hour-old triage verdict is re-judged", triage_calls, ["a"])
check("a 25-hour-old screen verdict is re-judged", screen_calls, ["a"])

reset()
rt = tempfile.mkdtemp()
cycle([item("a")], rt)
with open(os.path.join(rt, poller.VERDICTS_FILE), encoding="utf-8") as f:
    stored = json.load(f)
stored["a"]["triage"]["ts"] = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
with open(os.path.join(rt, poller.VERDICTS_FILE), "w", encoding="utf-8") as f:
    json.dump(stored, f)
del triage_calls[:], screen_calls[:]
cycle([item("a")], rt)
check("a 23-hour-old verdict is still served", (triage_calls, screen_calls), ([], []))

# --- only real model verdicts are cached --------------------------------------------------------------------
print("\nan unavailable item is not cached")
reset()
rt = tempfile.mkdtemp()
unavailable_triage.add("b")
verdicts, t_unavail, screens, s_unavail = cycle([item("a"), item("b")], rt)
check("b is reported unavailable", t_unavail, {"b"})
with open(os.path.join(rt, poller.VERDICTS_FILE), encoding="utf-8") as f:
    stored = json.load(f)
check("only a's verdict was cached", "b" in stored and "triage" in stored["b"], False)
unavailable_triage.clear()
del triage_calls[:]
cycle([item("a"), item("b")], rt)
check("b is triaged again next cycle, a is not", triage_calls, ["b"])

print("\na verdict the model never really gave is not cached")
reset()
rt = tempfile.mkdtemp()
triage_verdict["a"] = {}                                  # empty answer: the fail-safe default applies downstream
triage_verdict["b"] = {"id": "b", "kind": "reply"}        # no bucket: also a defaulted verdict
cycle([item("a"), item("b")], rt)
del triage_calls[:]
cycle([item("a"), item("b")], rt)
check("both are triaged again", sorted(triage_calls), ["a", "b"])

print("\nan item whose screen was unavailable keeps its triage verdict and only re-screens")
reset()
rt = tempfile.mkdtemp()
unavailable_screen.add("a")
verdicts, t_unavail, screens, s_unavail = cycle([item("a")], rt)
check("a's screen is reported unavailable", s_unavail, {"a"})
unavailable_screen.clear()
del triage_calls[:], screen_calls[:]
verdicts, t_unavail, screens, s_unavail = cycle([item("a")], rt)
check("triage is served from cache", triage_calls, [])
check("screen runs again", screen_calls, ["a"])
check("and now yields a verdict", screens["a"], {"flagged": False, "reason": ""})

print("\na junk item is triaged once and never screened")
reset()
rt = tempfile.mkdtemp()
triage_verdict["j"] = {"id": "j", "bucket": "junk", "kind": None, "complexity": "simple"}
cycle([item("j")], rt)
check("junk is not screened", screen_calls, [])
del triage_calls[:]
cycle([item("j")], rt)
check("junk is not triaged again", triage_calls, [])
check("and still not screened", screen_calls, [])

# --- a cached flag still forces needs-you ----------------------------------------------------------------
print("\na cached flagged verdict forces needs-you like a fresh one")
reset()
rt = tempfile.mkdtemp()
triage_verdict["a"] = {"id": "a", "bucket": "fyi", "kind": None, "complexity": "simple"}
screen_flags.add("a")
cycle([item("a")], rt)
del triage_calls[:], screen_calls[:]
verdicts, _, screens, _ = cycle([item("a")], rt)
check("no calls on the second cycle", (triage_calls, screen_calls), ([], []))
it = item("a")
flagged = poller._apply_screen(it, screens.get("a"))
check("the cached flag is applied", flagged, True)
check("the bucket is forced to needs-you", it["_bucket"], "needs-you")
check("the flag reason is stamped", it["_screen"]["reason"], "asks to wire money")

# --- pruning ----------------------------------------------------------------------------------------------
print("\npruning")
reset()
rt = tempfile.mkdtemp()
cycle([item("a"), item("b"), item("c")], rt)
cache = poller.VerdictCache(rt)
cache.prune({"demo": {"a": {}}}, datetime.now(timezone.utc))
check("an item now recorded seen is dropped", sorted(cache.entries), ["b", "c"])
cache.prune({}, datetime.now(timezone.utc) + timedelta(hours=25))
check("entries past the TTL are dropped", cache.entries, {})
cache.save()
with open(os.path.join(rt, poller.VERDICTS_FILE), encoding="utf-8") as f:
    check("and the file shrinks with them", json.load(f), {})

# --- dry-run never writes -----------------------------------------------------------------------------------
print("\ndry-run reads but never writes the cache")
reset()
rt = tempfile.mkdtemp()
cycle([item("a")], rt, persist=False)
check("no cache file is created", os.path.exists(os.path.join(rt, poller.VERDICTS_FILE)), False)

# --- a corrupt file reads as empty --------------------------------------------------------------------------
print("\ncorrupt cache file")
rt = tempfile.mkdtemp()
with open(os.path.join(rt, poller.VERDICTS_FILE), "w", encoding="utf-8") as f:
    f.write("{not json")
reset()
cycle([item("a")], rt)
check("an unreadable cache is treated as empty, so the item is judged", triage_calls, ["a"])

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
