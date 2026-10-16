"""Test for scripts/trello_utils.py's archive guard and retire_card.

Run directly: python plugins/trello/tests/test_archive_guard.py
"""
import json
import os
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.abspath(os.path.join(HERE, "..", "skills", "trello", "scripts"))
sys.path.insert(0, SCRIPTS_DIR)

import trello_utils  # noqa: E402
from trello_utils import ArchiveRefused, retire_card, trello_request, update_card, delete_card  # noqa: E402

failures = []
SESSION = ("key", "token")


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


class FakeTrello:
    """Stands in for urlopen: answers the few GETs the guard and retire_card make, records writes."""

    def __init__(self, card_list="In Progress", board_lists=("To Do", "In Progress", "Abandoned")):
        self.card_list = card_list
        self.board_lists = list(board_lists)
        self.writes = []

    def __call__(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        path = url.path.removeprefix("/1")
        if req.get_method() == "GET":
            if path.endswith("/list") or path.startswith("/lists/"):
                payload = {"name": self.card_list}
            elif path.endswith("/lists"):
                payload = [{"id": f"L-{n}", "name": n} for n in self.board_lists]
            else:
                payload = {"id": "C1", "idBoard": "B1"}
        else:
            body = dict(urllib.parse.parse_qsl((req.data or b"").decode()))
            self.writes.append((req.get_method(), path, body))
            payload = {"id": "NEW"}
        return _Resp(json.dumps(payload))


class _Resp:
    def __init__(self, raw):
        self.raw = raw.encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(fake, fn):
    trello_utils.urllib.request.urlopen = fake
    try:
        fn()
        return None
    except ArchiveRefused as e:
        return e


print("archive guard")
fake = FakeTrello("In Progress")
err = run(fake, lambda: update_card("C1", {"closed": "true"}, SESSION))
check("update_card refuses to archive an In Progress card", err is not None and not fake.writes)
check("refusal names retire_card", err is not None and "retire_card" in str(err))

fake = FakeTrello("In Progress")
err = run(fake, lambda: trello_request("PUT", "/cards/C1/closed", SESSION, body={"value": True}))
check("raw PUT /cards/{id}/closed is refused too", err is not None and not fake.writes)

fake = FakeTrello("To Do")
err = run(fake, lambda: delete_card("C1", SESSION))
check("delete_card refuses an unfinished card", err is not None and not fake.writes)

fake = FakeTrello("Interested")
err = run(fake, lambda: trello_request("POST", "/lists/L1/archiveAllCards", SESSION))
check("archiving every card in an active list is refused", err is not None and not fake.writes)

for terminal in ("Finished", "Abandoned", "Adopted", "Templates"):
    fake = FakeTrello(terminal)
    err = run(fake, lambda: update_card("C1", {"closed": "true"}, SESSION))
    check(f"archiving from {terminal} goes through", err is None and len(fake.writes) == 1)

fake = FakeTrello("In Progress")
err = run(fake, lambda: update_card("C1", {"closed": "false"}, SESSION))
check("unarchiving is never blocked", err is None and len(fake.writes) == 1)

fake = FakeTrello("In Progress")
err = run(fake, lambda: update_card("C1", {"start": "2026-10-20"}, SESSION))
check("an ordinary update is untouched", err is None and len(fake.writes) == 1)

fake = FakeTrello("In Progress")
err = run(fake, lambda: trello_request("DELETE", "/cards/C1/idLabels/LB1", SESSION))
check("removing a label is not mistaken for a card delete", err is None and len(fake.writes) == 1)

print("retire_card")
fake = FakeTrello("In Progress")
err = run(fake, lambda: retire_card("C1", "Moved to the Physical Tasks calendar.", SESSION))
comment = [w for w in fake.writes if w[1].endswith("/actions/comments")]
move = [w for w in fake.writes if w[0] == "PUT" and w[1] == "/cards/C1"]
check("retire_card comments the reason", comment and comment[0][2].get("text") == "Moved to the Physical Tasks calendar.")
check("retire_card moves the card to Abandoned", move and move[0][2] == {"idList": "L-Abandoned"})
check("retire_card never archives", not any("closed" in w[2] or w[1].endswith("/closed") for w in fake.writes))

fake = FakeTrello("To Do", board_lists=("To Do", "Done"))
run(fake, lambda: retire_card("C1", "Duplicate of abc123.", SESSION))
check("retire_card creates the outcome list when the board lacks it", any(w[1] == "/lists" for w in fake.writes))

try:
    retire_card("C1", "x", SESSION, outcome="In Progress")
    check("retire_card rejects a non-terminal outcome", False)
except ValueError:
    check("retire_card rejects a non-terminal outcome", True)

print()
if failures:
    print(f"Verification FAILED - {len(failures)} errors")
    sys.exit(1)
print("Verification passed ✅")
