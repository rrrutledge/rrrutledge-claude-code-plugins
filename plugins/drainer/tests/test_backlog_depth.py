"""Tests for the digest's backlog-depth barometer: provider_base.pending_summary (count + oldest,
minus the digest-queue ids), received_dt parsing, the shared load_providers loader, and run-digest's
compute_backlog aggregation + format_backlog rendering. Pure functions of in-memory listings - no
network or credentials needed.

Run directly:
    python plugins/drainer/tests/test_backlog_depth.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)  # the scripts import their siblings by bare name

import provider_base  # noqa: E402

digest_spec = importlib.util.spec_from_file_location("run_digest", os.path.join(SCRIPTS, "run-digest.py"))
digest = importlib.util.module_from_spec(digest_spec)
digest_spec.loader.exec_module(digest)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class FakeProvider(provider_base.ProviderBase):
    """A provider whose enumerate returns a canned listing, so pending_summary is exercised without a
    real source. stable_id is the item's own id."""

    def __init__(self, name, items, email=False):
        self.name = name
        self._items = items
        self.email_backlog = email

    def enumerate(self, limit):
        return self._items[:limit]

    def stable_id(self, item):
        return item["id"]


# --- received_dt: parse real timestamps, skip sentinels / junk ------------------------------------
print("received_dt")
check("Z suffix parses to UTC", provider_base.received_dt({"received": "2026-08-03T14:05:00Z"}).year, 2026)
check("offset timestamp parses", provider_base.received_dt({"received": "2026-08-03T14:05:00+00:00"}).day, 3)
check("naive timestamp is read as tz-aware",
      provider_base.received_dt({"received": "2026-08-03T14:05:00"}).tzinfo is not None, True)
check("trello '(no date)' sentinel -> None", provider_base.received_dt({"received": "(no date)"}), None)
check("missing received -> None", provider_base.received_dt({}), None)
check("non-string received -> None", provider_base.received_dt({"received": 12345}), None)


# --- pending_summary: count the listing, oldest of the dated items ---------------------------------
print("\npending_summary")
items = [
    {"id": "a", "received": "2026-09-01T10:00:00Z"},
    {"id": "b", "received": "2026-08-03T09:00:00Z"},  # oldest
    {"id": "c", "received": "2026-09-15T12:00:00Z"},
]
p = FakeProvider("mail", items, email=True)
s = p.pending_summary()
check("count is the full listing", s["count"], 3)
check("oldest_received is the earliest dated item", s["oldest_received"][:10], "2026-08-03")

# exclude the oldest id -> count drops AND oldest recomputes to the next earliest
s2 = p.pending_summary(exclude_ids={"b"})
check("excluded id drops the count", s2["count"], 2)
check("excluding the oldest recomputes oldest", s2["oldest_received"][:10], "2026-09-01")

# a source with no dated items yields oldest None but still a count
undated = FakeProvider("trello", [{"id": "x", "received": "(no date)"}, {"id": "y"}])
su = undated.pending_summary()
check("undated source still counts", su["count"], 2)
check("undated source oldest is None", su["oldest_received"], None)

# an empty listing
check("empty listing counts zero", FakeProvider("slack", []).pending_summary()["count"], 0)

# a listing that fills the whole page is flagged capped ("N+")
check("full page is not capped under a high limit", p.pending_summary(limit=500)["capped"], False)
check("a listing filling the limit is capped", p.pending_summary(limit=3)["capped"], True)


# --- load_providers: search dirs, configure, and per-provider isolation ----------------------------
print("\nload_providers")
with tempfile.TemporaryDirectory() as d:
    with open(os.path.join(d, "good-adapter.py"), "w", encoding="utf-8") as f:
        f.write(
            "class Provider:\n"
            "    name = 'good'\n"
            "    configured = False\n"
            "    def configure(self, cfg):\n"
            "        self.configured = True\n"
        )
    with open(os.path.join(d, "boom-adapter.py"), "w", encoding="utf-8") as f:
        f.write(
            "class Provider:\n"
            "    def __init__(self):\n"
            "        raise ValueError('kaboom')\n"
        )
    errs = []
    loaded = provider_base.load_providers(
        ["good", "boom", "absent"], [d], cfg={"repo": "x"},
        on_error=lambda n, m, k: errs.append((n, k)))
    check("only the good provider loads", [p.name for p in loaded], ["good"])
    check("configure ran on the loaded provider", loaded[0].configured, True)
    check("a missing adapter reports kind 'missing'", ("absent", "missing") in errs, True)
    check("a construction error reports kind 'config'", ("boom", "config") in errs, True)


# --- compute_backlog: aggregate across providers, isolate a dark source ----------------------------
print("\ncompute_backlog")
# Monkeypatch load_providers in the digest module so compute_backlog drives our fakes without file I/O.
mail = FakeProvider("mail", items, email=True)
board = FakeProvider("trello", [{"id": "x", "received": "(no date)"}, {"id": "y", "received": "(no date)"}])


class DarkProvider(FakeProvider):
    def enumerate(self, limit):
        raise provider_base.ProviderError("token expired", kind="auth")


dark = DarkProvider("slack", [])
junk = FakeProvider("outlook-graph-junk", [{"id": f"j{i}", "received": "2026-01-01T00:00:00Z"}
                                           for i in range(500)])
junk.count_in_backlog = False  # a standing-noise scanner: skipped entirely by the barometer
orig_load = digest.load_providers
digest.load_providers = lambda names, dirs, cfg=None, on_error=None: [mail, board, dark, junk]
try:
    # queue parks one mail id (fyi awaiting the digest) so it doesn't inflate the needs-you count
    queue = [{"id": "a", "source": "mail", "item": {"triage": "fyi"}}]
    backlog = digest.compute_backlog("rt", {"providers": [], "local_dir": ""}, queue)
finally:
    digest.load_providers = orig_load

by_name = {s["name"]: s for s in backlog["sources"]}
check("mail count drops the parked id", by_name["mail"]["count"], 2)
check("trello counts its startable cards", by_name["trello"]["count"], 2)
check("a dark source is unavailable, not zero", by_name["slack"]["count"], None)
check("dark source carries its error", "token expired" in (by_name["slack"]["error"] or ""), True)
check("a standing-noise scanner is skipped entirely", "outlook-graph-junk" in by_name, False)
check("grand total sums only the reachable, counted sources", backlog["total"], 4)

rendered = digest.format_backlog(backlog)
check("render shows the grand total", "Grand total: 4 pending across 2 source(s)." in rendered, True)
check("render shows the email barometer", "Email barometer: 2 pending" in rendered, True)
check("render marks the dark source unavailable", "slack: unavailable this run" in rendered, True)


print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
