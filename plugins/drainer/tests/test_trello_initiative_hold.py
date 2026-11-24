"""Tests for the initiative hold: an `initiatives/<slug>.md` front-matter line `hold_lists: [A, B]` keeps
that initiative's cards on those lists out of the trello adapter's queue (enumerate) and out of the
reconcile's startable set (still_in_inbox_ids), with nothing written to the held cards, and deleting the
line releases them on the next call.

Run directly:
    python plugins/drainer/tests/test_trello_initiative_hold.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "trello-adapter.py")
sys.path.insert(0, SCRIPTS)

spec = importlib.util.spec_from_file_location("trello_adapter", ADAPTER)
trello = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trello)
Provider = trello.Provider

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


class FakeUtils:
    """Stand-in for trello_utils; records every write so the test can prove a hold writes nothing."""

    def __init__(self, lists, cards):
        self._lists, self._cards, self.writes = lists, cards, []

    def get_board_lists(self, bid, session):
        return self._lists.get(bid, [])

    def get_board_cards(self, bid, session, fields=None):
        return self._cards.get(bid, [])

    def sweep_unblock(self, board_ids, session):
        return []

    def trello_request(self, method, path, session, **kw):
        self.writes.append((method, path))
        return {}

    def add_comment(self, *a, **kw):
        self.writes.append(("comment",))


PAST = "2026-07-01T00:00:00.000Z"
JOB, PERSONAL = {"id": "B1", "name": "Job Search Outreach"}, {"id": "B2", "name": "Personal Follow-Up"}
LISTS = {"B1": [{"id": "LI", "name": "Identified"}, {"id": "LR", "name": "Applied / Reached Out"},
                {"id": "LC", "name": "In Conversation"}],
         "B2": [{"id": "LP", "name": "Identified"}]}
YELLOW_JOB = [{"name": "Job Search", "color": "yellow"}]


def card(cid, list_id, labels=None):
    return {"id": f"aaaaaaaa00000000000{cid}", "name": f"card {cid}", "idList": list_id, "start": PAST,
            "labels": labels or [], "idMembers": []}


CARDS = {"B1": [card("00001", "LI"), card("00002", "LR"), card("00003", "LC")],
         "B2": [card("00004", "LP"), card("00005", "LP", YELLOW_JOB)]}


def repo_with(job_search_doc):
    repo = tempfile.mkdtemp(prefix="initiative-hold-")
    os.makedirs(os.path.join(repo, "initiatives"))
    with open(os.path.join(repo, "initiatives", "job-search.md"), "w", encoding="utf-8") as f:
        f.write(job_search_doc)
    return repo


def make_provider(repo):
    orig = Provider._import_trello_utils
    Provider._import_trello_utils = staticmethod(lambda: None)
    try:
        p = Provider()
    finally:
        Provider._import_trello_utils = orig
    p._utils = FakeUtils(LISTS, CARDS)
    p._session = object()
    p._my_member_id = "me"
    p.boards = [JOB, PERSONAL]
    p.board_initiatives = {"Job Search Outreach": "job-search"}
    p.repo = repo
    return p


def names(items):
    return sorted(it["name"] for it in items)


HELD_DOC = "---\nhold_lists: [Identified, Applied / Reached Out]\n---\n# Job Search\n"

print("a hold_lists line holds the initiative's cards on those lists, and only those")
repo = repo_with(HELD_DOC)
p = make_provider(repo)
got = names(p.enumerate(50))
check("held lists drop out; In Conversation and the other board's plain card stay",
      got, ["card 00003", "card 00004"])
check("a per-card initiative label on another board is held too",
      "card 00005" in got, False)
check("the reconcile's startable set applies the same hold",
      len(p.still_in_inbox_ids()), 2)
check("nothing is written to a held card", p._utils.writes, [])

print("\ndeleting the line releases every held card on the next call, no restart")
with open(os.path.join(repo, "initiatives", "job-search.md"), "w", encoding="utf-8") as f:
    f.write("# Job Search\n")
check("all five cards are back", len(p.enumerate(50)), 5)

print("\nno front matter, a front matter without hold_lists, or a hold_lists line in the body holds nothing")
for label, doc in [("no front matter", "# Job Search\n"),
                   ("front matter without hold_lists", "---\nsource: x\n---\n# Job Search\n"),
                   ("hold_lists in the body", "# Job Search\nhold_lists: [Identified]\n")]:
    check(label, len(make_provider(repo_with(doc)).enumerate(50)), 5)

print("\nCRLF front matter parses the same")
check("CRLF doc holds the same cards",
      names(make_provider(repo_with(HELD_DOC.replace("\n", "\r\n"))).enumerate(50)),
      ["card 00003", "card 00004"])

print("\nno config repo or no initiatives folder holds nothing")
check("repo None", len(make_provider(None).enumerate(50)), 5)
check("missing folder", len(make_provider(tempfile.mkdtemp()).enumerate(50)), 5)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
