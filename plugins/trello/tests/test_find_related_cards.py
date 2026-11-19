"""Test for scripts/trello_utils.py's find_related_cards - the board-wide lookup for a card on the same thing.

The case it exists for: a job card re-sourced from a second job board while an already-decided card for
the same posting sat in Abandoned, under a differently spelled employer ("HEB" vs "H-E-B").

Run directly: python plugins/trello/tests/test_find_related_cards.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "skills", "trello", "scripts")))

import trello_utils  # noqa: E402

failures = []

LISTS = [{"id": "L-id", "name": "Identified"}, {"id": "L-ab", "name": "Abandoned"}]
CARDS = [
    {"id": "new", "name": "H-E-B — Staff Software Engineer-Web Platform", "idList": "L-id", "closed": False,
     "shortUrl": "u/new", "desc": "[posting](https://careers.example.com/jobs/251065?lang=en-us)"},
    {"id": "dup", "name": "HEB — Staff Software Engineer-Web Platform (onsite)", "idList": "L-ab",
     "closed": False, "shortUrl": "u/dup", "desc": "https://www.indeed.com/viewjob?jk=ab12&req=251065"},
    {"id": "renamed", "name": "H.E.B — staff software engineer web platform", "idList": "L-ab",
     "closed": True, "shortUrl": "u/renamed", "desc": ""},
    {"id": "sibling", "name": "HEB — Sr Network Engineer", "idList": "L-id", "closed": False,
     "shortUrl": "u/sibling", "desc": "https://careers.example.com/jobs/777777"},
    {"id": "other", "name": "Acme — Staff Engineer", "idList": "L-id", "closed": False,
     "shortUrl": "u/other", "desc": "mentions H-E-B in passing"},
]


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def fake_request(method, path, session, params=None, body=None):
    if path.endswith("/lists"):
        return LISTS
    if path.endswith("/cards/all"):
        return CARDS
    raise AssertionError(f"unexpected request {method} {path}")


def test_tiers():
    print("test: a shared link id or same squashed name is the same item; a same employer is a sibling")
    trello_utils.trello_request = fake_request
    same, head = trello_utils.find_related_cards("B1", CARDS[0], ("k", "t"))
    by_id = {c["url"]: c for c in same}
    check("the abandoned re-sourced card is the same item", "u/dup" in by_id)
    check("it says which link id it shares", by_id.get("u/dup", {}).get("why") == "shares link id 251065")
    check("its list is named", by_id.get("u/dup", {}).get("list") == "Abandoned")
    check("an archived card with the same name ignoring punctuation is the same item", "u/renamed" in by_id)
    check("the archived card is marked closed", by_id.get("u/renamed", {}).get("closed") is True)
    check("the card itself is left out", "u/new" not in by_id and all(c["url"] != "u/new" for c in head))
    check("another role at the same employer is a sibling", [c["url"] for c in head] == ["u/sibling"])
    check("a card only mentioning the employer in its description is not matched",
          all(c["url"] != "u/other" for c in same + head))


if __name__ == "__main__":
    test_tiers()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
