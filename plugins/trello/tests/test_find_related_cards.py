"""Test for scripts/trello_utils.py's get_board_snapshot and find_related_cards - the board-wide lookup
for a card on the same thing.

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


def cid(created, n):
    """A Trello-shaped id: 8 hex digits of creation time, then a serial."""
    return f"{created:08x}{n:016x}"


NEW = cid(200, 1)
LISTS = [{"id": "L-id", "name": "Identified"}, {"id": "L-ab", "name": "Abandoned"},
         {"id": "L-tp", "name": "📋 Templates"}, {"id": "L-old", "name": "Interested", "closed": True}]
CARDS = [
    {"id": NEW, "name": "H-E-B — Staff Software Engineer-Web Platform", "idList": "L-id",
     "closed": False, "shortUrl": "u/new",
     "desc": "[posting](https://careers.example.com/jobs/251065?lang=en-us) "
             "referrer https://www.linkedin.com/in/jane-doe-4b211223"},
    {"id": cid(100, 2), "name": "HEB — Staff Software Engineer-Web Platform (onsite)", "idList": "L-ab",
     "closed": False, "shortUrl": "u/dup", "desc": "https://www.indeed.com/viewjob?jk=ab12&req=251065"},
    {"id": cid(300, 3), "name": "H.E.B — staff software engineer web platform", "idList": "L-id",
     "closed": False, "shortUrl": "u/twin", "desc": ""},
    {"id": cid(50, 4), "name": "HEB — Sr Network Engineer", "idList": "L-id", "closed": True,
     "shortUrl": "u/sibling", "desc": "https://www.linkedin.com/in/jane-doe-4b211223"},
    {"id": cid(60, 5), "name": "Acme — Staff Engineer", "idList": "L-id", "closed": False,
     "shortUrl": "u/other", "desc": "mentions H-E-B in passing"},
    {"id": cid(70, 6), "name": "H-E-B — Staff Software Engineer-Web Platform", "idList": "L-id",
     "closed": False, "shortUrl": "u/template", "desc": "", "isTemplate": True},
    {"id": cid(80, 7), "name": "H-E-B — Staff Software Engineer-Web Platform", "idList": "L-tp",
     "closed": False, "shortUrl": "u/templist", "desc": ""},
    {"id": cid(90, 8), "name": "Globex — Platform Lead", "idList": "L-id", "closed": False,
     "shortUrl": "u/shared-noise",
     "desc": "https://careers.example.com/jobs/251065x https://www.linkedin.com/company/251065 "
             "https://zoom.us/j/251065 https://www.linkedin.com/jobs/search?geoId=251065"},
    {"id": cid(95, 9), "name": "HEB — Platform Lead", "idList": "L-old", "closed": False,
     "shortUrl": "u/oldlist", "desc": "https://boards.example.com/apply?gh_jid=251065"},
]


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def fake_request(cards, calls):
    def request(method, path, session, params=None, body=None):
        calls.append(dict(params or {}))
        if path.endswith("/lists"):
            return LISTS
        if path.endswith("/cards/all"):
            before = (params or {}).get("before")
            page = sorted((c for c in cards if not before or c["id"] < before), key=lambda c: c["id"], reverse=True)
            return page[:int(params["limit"])]
        raise AssertionError(f"unexpected request {method} {path}")
    return request


def test_tiers():
    print("test: a shared link id or same squashed name is the same item; a same employer is a sibling")
    trello_utils.trello_request = fake_request(CARDS, [])
    same, head = trello_utils.find_related_cards(CARDS[0], trello_utils.get_board_snapshot("B1", ("k", "t")))
    by_url = {c["url"]: c for c in same}
    dup, twin = by_url.get("u/dup", {}), by_url.get("u/twin", {})
    check("the abandoned re-sourced card is the same item", dup.get("why") == "shares link id 251065", dup)
    check("it is decided and older", dup.get("decided") is True and dup.get("older") is True, dup)
    check("an open card with the same name ignoring punctuation is the same item", twin.get("why") == "same name")
    check("it is open and newer", twin.get("decided") is False and twin.get("older") is False, twin)
    check("template cards and Templates-list cards are left out",
          not {"u/template", "u/templist"} & {c["url"] for c in same + head})
    sib = head[0] if head else {}
    check("another role at the same employer is a sibling", [c["url"] for c in head] == ["u/sibling"])
    check("digits inside a profile slug are not a shared link id", sib.get("why") == "same name head", sib)
    check("an archived card on an open list is decided", sib.get("decided") is True, sib)
    check("company, meeting, and search-filter numbers are not a shared link id", "u/shared-noise" not in by_url)
    oldlist = by_url.get("u/oldlist", {})
    check("a posting id under a posting-named query key is shared", oldlist.get("why") == "shares link id 251065")
    check("an open card on an archived list is decided", oldlist.get("decided") is True, oldlist)
    check("a card only mentioning the employer in its description is not matched",
          all(c["url"] != "u/other" for c in same + head))


def test_paging():
    print("test: the snapshot pages past Trello's per-response cap")
    old_page = trello_utils._CARDS_PAGE
    trello_utils._CARDS_PAGE = 3
    calls = []
    try:
        trello_utils.trello_request = fake_request(CARDS, calls)
        _lists, cards = trello_utils.get_board_snapshot("B1", ("k", "t"))
    finally:
        trello_utils._CARDS_PAGE = old_page
    check("every non-template card comes back", len(cards) == 7, len(cards))
    check("later pages ask for cards before the oldest one seen", any("before" in c for c in calls))


def test_paging_stops_when_the_cursor_does_not_move():
    print("test: a server that ignores the paging cursor ends the loop instead of spinning")
    old_page = trello_utils._CARDS_PAGE
    trello_utils._CARDS_PAGE = 2
    calls = []

    def stuck(method, path, session, params=None, body=None):
        calls.append(path)
        return LISTS if path.endswith("/lists") else CARDS[:2]

    try:
        trello_utils.trello_request = stuck
        trello_utils.get_board_snapshot("B1", ("k", "t"))
    finally:
        trello_utils._CARDS_PAGE = old_page
    check("it stops after the repeated page", calls.count("/boards/B1/cards/all") == 2, len(calls))


if __name__ == "__main__":
    test_tiers()
    test_paging()
    test_paging_stops_when_the_cursor_does_not_move()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
