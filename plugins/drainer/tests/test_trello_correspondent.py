"""Tests for providers/trello-adapter.py's correspondent override - every card is its own identity.

The default identity keys on the item's `from`, which the adapter sets to the card's contact labels or,
for a card with none, the board name. Most cards have no contact label, so every card on a board shared
one identity and the poller held all but one of them behind a single open worker. The override keys on
the card id: two cards never hold each other, but one card cannot dispatch twice while its worker is open.

No network or Trello credentials. Run directly:
    python plugins/drainer/tests/test_trello_correspondent.py
"""
import importlib.util
import json
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "trello-adapter.py")
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("trello_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

pspec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(pspec)
pspec.loader.exec_module(poller)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def make_provider():
    p = adapter_mod.Provider.__new__(adapter_mod.Provider)
    p.name = "trello"
    p._fetch_comments = lambda card_id: "(none)"
    return p


def card_item(card_id, board="Job Search Outreach"):
    return {"cardId": card_id, "board": board, "name": f"card {card_id}", "contacts": [],
            "from": board, "list": "To Do"}


def test_cards_on_one_board_get_different_identities():
    print("test: two unlabeled cards on the same board are different correspondents")
    p = make_provider()
    a, b = card_item("5f0000000000000000000001"), card_item("5f0000000000000000000002")
    check("different card ids -> different identities", p.correspondent(a) != p.correspondent(b))
    active = {p.correspondent(a)}
    check("the poller does not hold the second card behind the first",
          not poller.held_for_correspondent(p.correspondent(b), active))


def test_same_card_same_identity():
    print("test: the same card resolves to the same identity every time")
    p = make_provider()
    a = card_item("5f0000000000000000000001")
    check("stable across calls", p.correspondent(a) == p.correspondent(dict(a)))
    check("one card is held while its own worker is open",
          poller.held_for_correspondent(p.correspondent(a), {p.correspondent(a)}))


def test_capture_persists_the_stamped_identity():
    print("test: capture persists the correspondent so a live worker's card reads as open")
    p = make_provider()
    it = card_item("5f0000000000000000000001")
    it["_correspondent"] = p.correspondent(it)
    with tempfile.TemporaryDirectory() as rt:
        path = p.capture(it, "trello-x-000001-nodue", rt)
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    check("persisted correspondent equals the stamped one", rec.get("correspondent") == it["_correspondent"],
          rec.get("correspondent"))


if __name__ == "__main__":
    test_cards_on_one_board_get_different_identities()
    test_same_card_same_identity()
    test_capture_persists_the_stamped_identity()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
