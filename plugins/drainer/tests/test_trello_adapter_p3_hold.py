"""Test for providers/trello-adapter.py's P3 hold: _startable_cards keeps P3 application cards off the
queue while any P1/P2 application card is startable on the same board. Plain card dicts, a stubbed
Trello client, no network.
Run directly:
    python plugins/drainer/tests/test_trello_adapter_p3_hold.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

try:
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

failures = []
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


class StubUtils:
    def __init__(self, cards):
        self.cards = cards

    def get_board_lists(self, bid, session):
        return [{"id": "L1", "name": "Identified"}, {"id": "L2", "name": "Applied"},
                {"id": "L3", "name": "Abandoned"}]

    def get_board_cards(self, bid, session, fields="all"):
        return self.cards


def card(cid, labels=(), list_id="L1", start=None):
    return {"id": cid, "idList": list_id, "labels": [{"name": n} for n in labels], "start": start}


def startable_ids(cards):
    prov = adapter_mod.Provider.__new__(adapter_mod.Provider)
    prov._utils = StubUtils(cards)
    prov._session = object()
    prov.skip_lists = {"abandoned"}
    prov.skip_labels = {"blocked"}
    prov.board_in_progress_lists = {"B": ("applied",)}
    return [c["id"] for c, _, _ in prov._startable_cards({"id": "b", "name": "B"}, NOW, "me")]


FUTURE = (NOW + timedelta(days=3)).isoformat()


def test_hold():
    print("test: P3 held while a P1/P2 application card is startable")
    check("P3 held while a P2 exists", startable_ids([card("a", ["🎯 P3"]), card("b", ["🎯 P2"])]) == ["b"])
    check("P3 held while a P1 exists", startable_ids([card("a", ["🎯 P3"]), card("b", ["🎯 P1"])]) == ["b"])
    check("P3 released once only P3 and unlabeled remain",
          startable_ids([card("a", ["🎯 P3"]), card("u")]) == ["a", "u"])


def test_never_held():
    print("test: Contact, in-progress, and unlabeled cards are never held back")
    ids = startable_ids([card("p2", ["🎯 P2"]), card("c", ["👤 Contact", "🎯 P3"]),
                         card("ip", ["🎯 P3"], list_id="L2"), card("u"), card("p3", ["🎯 P3"])])
    check("only the plain P3 is held", ids == ["p2", "c", "ip", "u"], ids)


def test_non_startable_p2_does_not_hold():
    print("test: a blocked, future-Start, or skip-list P2 does not hold P3")
    for name, p2 in (("blocked", card("x", ["🎯 P2", "⛔ Blocked"])),
                     ("future Start", card("x", ["🎯 P2"], start=FUTURE)),
                     ("skip list", card("x", ["🎯 P2"], list_id="L3"))):
        check(f"{name} P2 leaves P3 released", startable_ids([p2, card("a", ["🎯 P3"])]) == ["a"])
    check("a Contact or in-progress P2 does not hold P3 either",
          startable_ids([card("c", ["👤 Contact", "🎯 P2"]), card("ip", ["🎯 P2"], list_id="L2"),
                         card("a", ["🎯 P3"])]) == ["c", "ip", "a"])


if __name__ == "__main__":
    test_hold()
    test_never_held()
    test_non_startable_p2_does_not_hold()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
