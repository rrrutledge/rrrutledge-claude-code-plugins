"""Tests for providers/zoom-adapter.py's summary-finality logic — the next_steps-presence signal that keeps
a meeting's action items from being dropped when its recap is read before the action items land.

The bug this guards against: the adapter used to freeze a summary as final as soon as it had any content
and had been quiet for the cooldown, so a read that saw a recap with no next_steps yet cached the recap
and never re-fetched — the action items, added later, were lost for good once the meeting aged out.

The fix keys on a concrete Zoom signal (verified against a month of real meetings): Zoom omits the
`next_steps` key entirely while a summary is still generating and includes it — as a populated list — once
generation finishes, never as a stray empty list. So the presence of `next_steps` is the "done" signal; a
summary whose `next_steps` is absent is still generating and is re-fetched, not frozen.

No network or Zoom credentials: a fake Zoom client returns scripted `(status, body)` responses, and a
temp runtime_dir holds the summary cache. Run directly:
    python plugins/drainer/tests/test_zoom_adapter_summary_freeze.py
"""
import importlib.util
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "zoom-adapter.py")
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("zoom_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

OWNER = ["Russell", "Russ", "Rutledge"]
UUID = "cX1pAzroT/+SRJE5oy/KxA=="
TOPIC = "2026 InnerSource Summit Planning"
NUMERIC_ID = "82127920509"

OWNER_STEPS = [f"Russell: {t}" for t in (
    "Complete the Summit CFP reviews", "Draft the sponsor prospectus", "Confirm the keynote speaker",
    "Book the venue walkthrough", "Send the save-the-date", "Finalize the review rubric")]
OTHER_STEPS = ["Priya: publish the registration page", "Sam: line up the AV vendor"]

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeZoom:
    """Routes the four endpoints the adapter calls. `summary_seq` is the list of `(status, body)` the
    meeting_summary endpoint returns on successive fetches (the last entry repeats), so a test can model a
    summary that gains its next_steps between cycles. `summary_calls` counts real summary fetches, which is
    how a test proves the cache did (or didn't) skip the network."""

    def __init__(self, start_time, summary_seq):
        self.start_time = start_time
        self.summary_seq = summary_seq
        self.summary_calls = 0

    def double_encode(self, uuid):
        return uuid

    def get(self, path):
        if path.startswith("/v2/users/me/meetings"):
            return 200, {"meetings": [{"topic": TOPIC, "id": NUMERIC_ID, "uuid": UUID,
                                       "start_time": self.start_time}], "next_page_token": ""}
        if path.startswith("/v2/past_meetings/") and path.endswith("/instances"):
            return 200, {"meetings": [{"uuid": UUID, "start_time": self.start_time}]}
        if "/meeting_summary" in path:
            resp = self.summary_seq[min(self.summary_calls, len(self.summary_seq) - 1)]
            self.summary_calls += 1
            return resp
        return 404, {}


def make_provider(runtime_dir, fake):
    p = adapter_mod.Provider.__new__(adapter_mod.Provider)
    p.runtime_dir = runtime_dir
    p._zoom = fake
    p.owner_names = list(OWNER)
    p._me_names = None
    p.lookback_hours = 48
    p.cooldown_minutes = 0        # not what these tests exercise; keep the finality cooldown out of the way
    p.poll_interval_minutes = 0   # never self-throttle between the cycles a test runs back to back
    return p


def generated(overview, steps):
    """A finished summary: next_steps present as a list."""
    return {"summary_overview": overview, "next_steps": list(steps),
            "summary_last_modified_time": iso(datetime.now(timezone.utc) - timedelta(hours=1)),
            "meeting_end_time": iso(datetime.now(timezone.utc) - timedelta(hours=2))}


def shell():
    """A still-generating summary: Zoom omits the next_steps key (and the recap)."""
    return {"summary_title": "shell",
            "summary_last_modified_time": iso(datetime.now(timezone.utc) - timedelta(hours=2)),
            "meeting_end_time": iso(datetime.now(timezone.utc) - timedelta(hours=2))}


def kinds(items):
    return [it.get("kind") for it in items]


def test_summary_generated_helper():
    print("test: _summary_generated keys on the presence of next_steps, not on its contents")
    g = adapter_mod.Provider._summary_generated
    check("next_steps absent -> not generated", g({"summary_overview": "recap"}) is False)
    check("next_steps null -> not generated", g({"next_steps": None}) is False)
    check("next_steps empty list -> generated (a meeting with genuinely no action items)",
          g({"next_steps": []}) is True)
    check("next_steps populated -> generated", g({"next_steps": OWNER_STEPS}) is True)


def test_recap_before_next_steps_is_not_frozen():
    print("test: a summary read before next_steps land does NOT freeze — the action items surface later")
    now = datetime.now(timezone.utc)
    rt = tempfile.mkdtemp()
    fake = FakeZoom(iso(now - timedelta(hours=3)), [
        (200, shell()),                                        # cycle 1: still generating, no next_steps key
        (200, generated("recap", OWNER_STEPS + OTHER_STEPS)),  # cycle 2: next_steps have landed
    ])
    p = make_provider(rt, fake)

    first = p.enumerate(100)
    check("cycle 1 emits nothing while next_steps is absent", first == [], kinds(first))
    check("cycle 1 does not cache the ungenerated summary (so it will be re-fetched)",
          UUID not in p._load_summary_cache(), list(p._load_summary_cache().keys()))

    second = p.enumerate(100)
    action = [it for it in second if it.get("kind") == "action-item"]
    check("cycle 2 surfaces all 6 owner action items once next_steps arrive (the bug is fixed)",
          len(action) == 6, kinds(second))
    check("cycle 2 emits the recap alongside them",
          any(it.get("kind") == "recap" for it in second), kinds(second))
    check("the summary is now cached as final", UUID in p._load_summary_cache())


def test_recap_without_next_steps_key_never_freezes():
    print("test: even a recap present WITHOUT a next_steps key is treated as still-generating, not final")
    now = datetime.now(timezone.utc)
    rt = tempfile.mkdtemp()
    # A hypothetical Zoom state the survey never actually produced — a recap but no next_steps key. The gate
    # must still wait: presence of next_steps, not of the recap, is the done signal.
    recap_only = {"summary_overview": "recap only",
                  "summary_last_modified_time": iso(now - timedelta(hours=1)),
                  "meeting_end_time": iso(now - timedelta(hours=2))}
    fake = FakeZoom(iso(now - timedelta(hours=3)),
                    [(200, recap_only), (200, generated("recap only", OWNER_STEPS))])
    p = make_provider(rt, fake)
    first = p.enumerate(100)
    check("recap-without-next_steps emits nothing and is not cached",
          first == [] and UUID not in p._load_summary_cache(), kinds(first))
    second = p.enumerate(100)
    check("the owner items surface once next_steps land",
          len([it for it in second if it.get("kind") == "action-item"]) == 6, kinds(second))


def test_generated_summary_freezes_and_serves_from_cache():
    print("test: the healthy path — a generated summary is captured and then served from cache")
    now = datetime.now(timezone.utc)
    rt = tempfile.mkdtemp()
    fake = FakeZoom(iso(now - timedelta(hours=3)),
                    [(200, generated("recap", OWNER_STEPS + OTHER_STEPS))])
    p = make_provider(rt, fake)

    first = p.enumerate(100)
    check("emits 6 owner action items", len([i for i in first if i.get("kind") == "action-item"]) == 6, kinds(first))
    check("caches the summary on first read", UUID in p._load_summary_cache())
    check("one summary fetch so far", fake.summary_calls == 1, fake.summary_calls)

    p.enumerate(100)
    check("second cycle serves from cache without re-fetching", fake.summary_calls == 1, fake.summary_calls)


def test_genuinely_actionless_summary_is_final():
    print("test: a generated summary with an empty next_steps list is final (recap only), not held open")
    now = datetime.now(timezone.utc)
    rt = tempfile.mkdtemp()
    fake = FakeZoom(iso(now - timedelta(hours=3)), [(200, generated("recap", []))])
    p = make_provider(rt, fake)

    first = p.enumerate(100)
    check("emits the recap", kinds(first) == ["recap"], kinds(first))
    check("no action items (the meeting genuinely has none)",
          not any(it.get("kind") == "action-item" for it in first))
    check("caches it as final", UUID in p._load_summary_cache())
    p.enumerate(100)
    check("does not re-fetch a final actionless summary", fake.summary_calls == 1, fake.summary_calls)


def test_cached_shell_is_refetched():
    print("test: a legacy cached shell (next_steps absent) is re-fetched, not resurfaced as an empty recap")
    now = datetime.now(timezone.utc)
    rt = tempfile.mkdtemp()
    p0 = make_provider(rt, FakeZoom(iso(now), []))
    p0._save_summary_cache({UUID: {"cached_at": iso(now), "summary": shell()}})  # a shell from an older build

    fake = FakeZoom(iso(now - timedelta(hours=3)), [(200, generated("recap", OWNER_STEPS))])
    p = make_provider(rt, fake)
    items = p.enumerate(100)
    check("the shell is re-fetched (a real network read happens)", fake.summary_calls == 1, fake.summary_calls)
    check("and the now-generated summary's owner items surface",
          len([it for it in items if it.get("kind") == "action-item"]) == 6, kinds(items))


if __name__ == "__main__":
    test_summary_generated_helper()
    test_recap_before_next_steps_is_not_frozen()
    test_recap_without_next_steps_key_never_freezes()
    test_generated_summary_freezes_and_serves_from_cache()
    test_genuinely_actionless_summary_is_final()
    test_cached_shell_is_refetched()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
