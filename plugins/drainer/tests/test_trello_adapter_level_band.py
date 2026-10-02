"""Test for providers/trello-adapter.py's band ranking (_referral_band, _level_band, and provider_base's
band_rank — the (priority_band, level_band, referral_band) queue-order policy that both
trello-adapter._enumerate and run-poller.py's cross-source needs-you sort read). No network/Trello
credentials required — _priority_band/_referral_band/_level_band and band_rank are pure functions of a
card dict.
Run directly:
    python plugins/drainer/tests/test_trello_adapter_level_band.py
"""
import importlib.util
import os
import sys

try:  # labels under test carry emoji; keep printing them safe on a non-UTF-8 console (Windows cp1252)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "trello-adapter.py")
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("trello_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

import provider_base  # noqa: E402 — SCRIPTS is on sys.path above; band_rank is the shared queue-order policy

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def card(desc="", labels=None):
    return {"desc": desc, "labels": labels or []}


def test_level_band_reads_desc_line():
    print("test: _level_band parses the 'Priority: ... · <level>' desc line")
    Provider = adapter_mod.Provider
    check("Director/VP-level -> 0 (shared neutral level with email/Slack)",
          Provider._level_band(card("Priority: P1 · Developer enablement · Director/VP-level")) == 0)
    check("IC-level -> -1",
          Provider._level_band(card("Priority: P1 · Developer enablement · IC-level")) == -1)
    check("no priority line -> 0 (non-job-search board)", Provider._level_band(card("just a normal card")) == 0)
    check("empty desc -> 0", Provider._level_band(card(None)) == 0)


def test_referral_band_reads_label():
    print("test: _referral_band reads the Referral label - 1 with, 0 without")
    Provider = adapter_mod.Provider
    check("canonical '🤝 Referral' label -> 1", Provider._referral_band(card(labels=[{"name": "🤝 Referral"}])) == 1)
    check("bare 'Referral' without the emoji prefix -> 0 (only the canonical form counts)",
          Provider._referral_band(card(labels=[{"name": "Referral"}])) == 0)
    check("no referral label -> 0", Provider._referral_band(card(labels=[{"name": "🎯 P1"}])) == 0)
    check("contact name containing 'referral' does NOT trip it -> 0",
          Provider._referral_band(card(labels=[{"name": "Referral from Dana"}])) == 0)
    check("no labels -> 0", Provider._referral_band(card()) == 0)


def test_referral_label_held_out_of_contacts():
    print("test: the Referral label is not misread as a contact name")
    prov = adapter_mod.Provider.__new__(adapter_mod.Provider)
    prov.channels, prov.features, prov.status_labels = set(), set(), {"blocked", "waiting"}
    _, _, contacts, _ = prov._classify_labels(card(labels=[{"name": "🤝 Referral"}, {"name": "Dana Whitfield"}]))
    check("Referral held out; a real person stays a contact", contacts == ["Dana Whitfield"], contacts)


# The real queue-order policy under test — the shared band_rank tuple, plus the trailing date key the
# adapter/poller each append. Reads through band_rank so the test can never drift from the live order.
SORT_KEY = lambda it: (*provider_base.band_rank(it), it["_sort_dt"])


def test_sort_key_orders_level_within_band():
    print("test: (priority_band, level_band, referral_band, date) sort puts Director/VP ahead of IC within the same band")
    Provider = adapter_mod.Provider
    # Contoso (P1 Director/VP, older date) must outrank
    # Northwind (P1 IC, newer date) even though Northwind's date alone would sort first.
    p1_band = adapter_mod._PRIORITY_BAND[1]
    contoso = {
        "name": "Contoso", "_priority_band": p1_band, "_referral_band": 0,
        "_level_band": Provider._level_band(card("Priority: P1 · Eng leadership · Director/VP-level")),
        "_sort_dt": "2026-07-22",
    }
    northwind = {
        "name": "Northwind", "_priority_band": p1_band, "_referral_band": 0,
        "_level_band": Provider._level_band(card("Priority: P1 · Platform · IC-level")),
        "_sort_dt": "2026-08-15",
    }
    ranked = sorted([northwind, contoso], key=SORT_KEY, reverse=True)
    check("Director/VP-level card dispatches before a same-band, newer-dated IC-level card",
          ranked[0]["name"] == "Contoso", [it["name"] for it in ranked])


def test_band_rank_is_priority_then_level_then_referral():
    print("test: band_rank returns (priority, level, referral) — the one place the queue order is defined")
    it = {"_priority_band": 5, "_level_band": -1, "_referral_band": 1}
    check("band_rank orders priority, then level, then referral", provider_base.band_rank(it) == (5, -1, 1),
          provider_base.band_rank(it))
    check("missing bands fall back to neutral", provider_base.band_rank({}) ==
          (provider_base.NEUTRAL_PRIORITY_BAND, 0, 0), provider_base.band_rank({}))


def test_level_leads_referral_within_band():
    print("test: within a fit tier, level outranks referral — a cold leadership role ahead of a referral IC role")
    p1 = adapter_mod._PRIORITY_BAND[1]
    # Russell's stated order within a tier: referral+Director → cold+Director → referral+IC → cold+IC.
    # A leadership role without a referral is worked before an IC role even with one.
    referral_ic = {"name": "referral IC", "_priority_band": p1, "_referral_band": 1, "_level_band": -1,
                   "_sort_dt": "2026-01-01"}
    cold_director = {"name": "cold Director", "_priority_band": p1, "_referral_band": 0, "_level_band": 0,
                     "_sort_dt": "2026-08-01"}
    ranked = sorted([cold_director, referral_ic], key=SORT_KEY, reverse=True)
    check("a cold Director/VP-level role outranks a same-band referral IC-level role",
          ranked[0]["name"] == "cold Director", [it["name"] for it in ranked])

    referral_director = {"name": "referral Director", "_priority_band": p1, "_referral_band": 1,
                         "_level_band": 0, "_sort_dt": "2026-01-01"}
    full = sorted([cold_director, referral_ic, referral_director,
                   {"name": "cold IC", "_priority_band": p1, "_referral_band": 0, "_level_band": -1,
                    "_sort_dt": "2026-08-01"}], key=SORT_KEY, reverse=True)
    check("full within-tier order is referral-Director, cold-Director, referral-IC, cold-IC",
          [it["name"] for it in full] == ["referral Director", "cold Director", "referral IC", "cold IC"],
          [it["name"] for it in full])


def test_priority_band_still_leads_referral_band():
    print("test: a higher priority band still beats a lower band's referral")
    p1 = adapter_mod._PRIORITY_BAND[1]
    p2 = adapter_mod._PRIORITY_BAND[2]
    p1_cold_ic = {"name": "P1 cold IC", "_priority_band": p1, "_referral_band": 0, "_level_band": -1,
                  "_sort_dt": "2026-01-01"}
    p2_referral_director = {"name": "P2 referral Director", "_priority_band": p2, "_referral_band": 1,
                            "_level_band": 0, "_sort_dt": "2026-08-01"}
    ranked = sorted([p2_referral_director, p1_cold_ic], key=SORT_KEY, reverse=True)
    check("P1 cold IC still outranks a P2 referral Director/VP (tier leads referral)",
          ranked[0]["name"] == "P1 cold IC", [it["name"] for it in ranked])


def test_startable_gate_start_is_the_only_date():
    print("test: _startable - Start is the only date; a future Start is the sole way to defer a card")
    Provider = adapter_mod.Provider
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    past = now - timedelta(days=30)
    future = now + timedelta(days=6)

    # A future Start defers, full stop - nothing else can pull the card into play early. This is what
    # keeps a card deferred to "next Monday" from surfacing before then (the Fabrikam failure was a
    # stale Due dragging a future-Start card in early, which no longer exists as a concept).
    check("future Start -> deferred (not startable)", Provider._startable(future, now) is False)
    check("past Start -> startable", Provider._startable(past, now) is True)
    check("Start exactly now -> startable", Provider._startable(now, now) is True)
    check("no Start -> startable immediately (hungry to start)",
          Provider._startable(None, now) is True)


def test_priority_band_still_leads_level_band():
    print("test: a higher priority band still beats a lower band regardless of level")
    Provider = adapter_mod.Provider
    p1_ic = {"name": "P1 IC", "_priority_band": adapter_mod._PRIORITY_BAND[1], "_referral_band": 0,
             "_level_band": -1, "_sort_dt": "2026-01-01"}
    p2_director = {"name": "P2 Director", "_priority_band": adapter_mod._PRIORITY_BAND[2], "_referral_band": 0,
                    "_level_band": 0, "_sort_dt": "2026-08-01"}
    ranked = sorted([p2_director, p1_ic], key=SORT_KEY, reverse=True)
    check("P1 IC still outranks P2 Director/VP", ranked[0]["name"] == "P1 IC", [it["name"] for it in ranked])


if __name__ == "__main__":
    test_level_band_reads_desc_line()
    test_referral_band_reads_label()
    test_referral_label_held_out_of_contacts()
    test_band_rank_is_priority_then_level_then_referral()
    test_sort_key_orders_level_within_band()
    test_level_leads_referral_within_band()
    test_startable_gate_start_is_the_only_date()
    test_priority_band_still_leads_level_band()
    test_priority_band_still_leads_referral_band()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
