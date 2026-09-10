"""Tests for the Trello crash-recovery reconcile: the trello adapter's still_in_inbox_ids() (the set of
currently-startable card ids the reconcile reads completion off), the messageId it writes at capture, and
the end-to-end reconcile behavior over a real trello adapter driven by fake boards.

A Trello worker that crashes or is closed before it runs CLEAR leaves its card recorded-as-seen but never
released. This wiring lets the reconcile re-queue that card on its own, exactly as it already does for a
crashed email worker, while never re-queuing a card whose worker DID run CLEAR (CLEAR bumps the card's
Start, minting a new id, so the old id drops out of the startable set).

Run directly:
    python plugins/drainer/tests/test_trello_reconcile.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
PROVIDERS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers")
POLLER = os.path.join(SCRIPTS, "run-poller.py")
ADAPTER = os.path.join(PROVIDERS, "trello-adapter.py")

sys.path.insert(0, SCRIPTS)  # run-poller and the adapter import their siblings by bare name


def _load(mod_name, path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


poller = _load("run_poller", POLLER)
trello = _load("trello_adapter", ADAPTER)
Provider = trello.Provider

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


CFG = {"orphan_grace_minutes": 15}
GRACE_S = CFG["orphan_grace_minutes"] * 60

# 24-hex-char Mongo-style card ids (first 8 hex decode to a creation time, last 6 land in the stable_id).
ID_STARTABLE = "aaaaaaaa0000000000001111"
ID_BLOCKED = "aaaaaaaa0000000000002222"
ID_ASSIGNED = "aaaaaaaa0000000000003333"
ID_CLEARED = "aaaaaaaa0000000000004444"
ID_TEMPLATE = "aaaaaaaa0000000000005555"

PAST = "2026-07-01T00:00:00.000Z"      # a Start that has arrived -> startable
FUTURE = "2099-01-01T00:00:00.000Z"    # a Start CLEAR deferred into the future -> not startable
CLEARED_OLD_START = "2026-06-01T00:00:00.000Z"  # the cleared card's Start BEFORE its CLEAR bump


class FakeUtils:
    """Stand-in for trello_utils: hands back canned lists/cards, or raises to simulate an API failure."""

    def __init__(self, lists, cards, fail=False):
        self._lists = lists
        self._cards = cards
        self.fail = fail

    def get_board_lists(self, bid, session):
        if self.fail:
            raise RuntimeError("board fetch blew up")
        return self._lists.get(bid, [])

    def get_board_cards(self, bid, session, fields=None):
        if self.fail:
            raise RuntimeError("board fetch blew up")
        return self._cards.get(bid, [])

    def sweep_unblock(self, board_ids, session):
        return []


def card(cid, name, list_id, start=None, labels=None, members=None):
    return {"id": cid, "name": name, "idList": list_id, "start": start,
            "labels": labels or [], "idMembers": members or []}


BOARD = {"id": "B1", "name": "Job Search Outreach"}
LISTS = {"B1": [{"id": "L1", "name": "To Do"}, {"id": "LT", "name": "\U0001F4CB Templates"}]}

# The board as it stands NOW: one startable card, plus four the gate must exclude.
CARDS_NOW = {"B1": [
    card(ID_STARTABLE, "Huntress Director Engineering", "L1", start=PAST),
    card(ID_BLOCKED, "Blocked Upstream", "L1", start=PAST,
         labels=[{"name": "⛔ Blocked", "color": "red"}]),
    card(ID_ASSIGNED, "Someone Elses Card", "L1", start=PAST, members=["other-member"]),
    card(ID_CLEARED, "Just Cleared Card", "L1", start=FUTURE),  # worker ran CLEAR -> Start now future
    card(ID_TEMPLATE, "Template Row", "LT", start=PAST),        # on a skip-list (Templates)
]}


def make_provider(fake_utils, boards=(BOARD,)):
    """A real trello Provider wired to fake boards, skipping every network touch."""
    orig = Provider._import_trello_utils
    Provider._import_trello_utils = staticmethod(lambda: None)
    try:
        p = Provider()
    finally:
        Provider._import_trello_utils = orig
    p._utils = fake_utils
    p._session = object()      # skip get_trello_session()
    p._my_member_id = "me"     # skip the /members/me lookup
    p.boards = list(boards)
    return p


def sid(cid, name, start):
    """The stable_id the adapter would mint for a card — the test's own copy of the identity, so the
    assertions read a card by the same handle the adapter files it under."""
    stub = types.SimpleNamespace(name=Provider.name)  # stable_id only needs self.name
    return Provider.stable_id(stub, {"name": name, "cardId": cid, "start": start})


# --------------------------------------------------------------------- still_in_inbox_ids: the gate
print("still_in_inbox_ids returns exactly the currently-startable card ids")
p = make_provider(FakeUtils(LISTS, CARDS_NOW))
got = p.still_in_inbox_ids()
want = {sid(ID_STARTABLE, "Huntress Director Engineering", PAST)}
check("only the startable card is 'still outstanding'", got, want)
check("a Blocked-labeled card is not outstanding",
      sid(ID_BLOCKED, "Blocked Upstream", PAST) in got, False)
check("a card assigned to someone else is not outstanding",
      sid(ID_ASSIGNED, "Someone Elses Card", PAST) in got, False)
check("a card CLEAR deferred into the future is not outstanding",
      sid(ID_CLEARED, "Just Cleared Card", FUTURE) in got, False)
check("a skip-list (Templates) card is not outstanding",
      sid(ID_TEMPLATE, "Template Row", PAST) in got, False)

print("\nstill_in_inbox_ids fails safe: a board-fetch failure returns None, not an empty set")
p = make_provider(FakeUtils(LISTS, CARDS_NOW, fail=True))
check("None so reconcile skips trello rather than reading 'every card gone'",
      p.still_in_inbox_ids(), None)

print("\nno boards -> None (nothing to reconcile, never an empty 'all gone' set)")
p = make_provider(FakeUtils(LISTS, CARDS_NOW), boards=())
check("no configured boards -> None", p.still_in_inbox_ids(), None)

print("\ncapture writes messageId == the stable_id, the handle reconcile checks")
rt_cap = tempfile.mkdtemp(prefix="trello-capture-")
p = make_provider(FakeUtils(LISTS, CARDS_NOW))
p._fetch_comments = lambda cid: "(none)"  # skip the comments API call
iid = sid(ID_STARTABLE, "Huntress Director Engineering", PAST)
item = {"cardId": ID_STARTABLE, "name": "Huntress Director Engineering", "start": PAST,
        "board": "Job Search Outreach", "list": "To Do", "_bucket": "needs-you", "_kind": "work"}
p.capture(item, iid, rt_cap)
rec = json.load(open(os.path.join(rt_cap, "items", f"{iid}.json"), encoding="utf-8"))
check("messageId equals the item id", rec.get("messageId"), iid)


# --------------------------------------------------------------------- stable_id: time-of-day
print("\nstable_id folds Start's time-of-day in: a same-day reschedule is a distinct item")
MORNING = "2026-09-10T06:00:00.000Z"
AFTERNOON = "2026-09-10T19:00:00.000Z"
check("same card, same Start -> identical id (idempotent)",
      sid(ID_STARTABLE, "Prep for Moms birthday", MORNING),
      sid(ID_STARTABLE, "Prep for Moms birthday", MORNING))
check("same card, same day, different time -> different id (re-dispatchable)",
      sid(ID_STARTABLE, "Prep for Moms birthday", MORNING)
      != sid(ID_STARTABLE, "Prep for Moms birthday", AFTERNOON), True)
check("sub-minute jitter in Start is ignored -> identical id",
      sid(ID_STARTABLE, "Prep for Moms birthday", "2026-09-10T19:00:00.000Z"),
      sid(ID_STARTABLE, "Prep for Moms birthday", "2026-09-10T19:00:59.999Z"))
check("an undated card stamps to the nodue sentinel",
      sid(ID_STARTABLE, "Prep for Moms birthday", None).endswith("-nodue"), True)


# --------------------------------------------------------------------- end-to-end reconcile
def _iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def workspace(seen, items):
    rt = tempfile.mkdtemp(prefix="trello-reconcile-")
    with open(os.path.join(rt, "seen.json"), "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.makedirs(os.path.join(rt, "items"), exist_ok=True)
    for iid_, rec_ in items.items():
        with open(os.path.join(rt, "items", f"{iid_}.json"), "w", encoding="utf-8") as f:
            json.dump(rec_, f)
    return rt


def write_session(rt, iid_, guid, launched_ago_s=3 * GRACE_S):
    seeds = os.path.join(rt, "seeds")
    os.makedirs(seeds, exist_ok=True)
    path = os.path.join(seeds, f"{iid_}.prompt.txt.session")
    with open(path, "w", encoding="utf-8") as f:
        f.write(guid)
    stamp = time.time() - launched_ago_s
    os.utime(path, (stamp, stamp))


def run(rt, providers, live=()):
    requeued = []
    real_live, real_seen_state = poller.live_session_ids, poller.seen_state

    class Result:
        stdout = ""

    def fake_seen_state(*args):
        if args and args[0] == "requeue":
            requeued.append((args[2], args[3]))
            return Result()
        if args and args[0] == "queue-list":
            r = Result()
            r.stdout = "[]"
            return r
        return real_seen_state(*args)

    poller.live_session_ids = lambda: set(live) if live is not None else None
    poller.seen_state = fake_seen_state
    try:
        n = poller.reconcile_unhandled(rt, CFG, providers)
    finally:
        poller.live_session_ids, poller.seen_state = real_live, real_seen_state
    return n, requeued


ID_CRASHED = sid(ID_STARTABLE, "Huntress Director Engineering", PAST)
ID_CLEARED_OLD = sid(ID_CLEARED, "Just Cleared Card", CLEARED_OLD_START)


def trello_workspace():
    """A runtime holding two seen Trello items: a startable card whose worker crashed, and a card whose
    worker already ran CLEAR (its OLD id, since its Start has since moved into the future)."""
    return workspace(
        seen={"trello": {ID_CRASHED: {"triage": "needs-you"},
                         ID_CLEARED_OLD: {"triage": "needs-you"}}},
        items={ID_CRASHED: {"messageId": ID_CRASHED, "ts": _iso_ago(3 * GRACE_S)},
               ID_CLEARED_OLD: {"messageId": ID_CLEARED_OLD, "ts": _iso_ago(3 * GRACE_S)}},
    )


print("\nreconcile: a crashed worker's startable card is re-queued; a cleared card is left alone")
rt = trello_workspace()
n, requeued = run(rt, [make_provider(FakeUtils(LISTS, CARDS_NOW))])
check("the uncleared startable card is re-queued", requeued, [(trello.Provider.name, ID_CRASHED)])
check("the cleared card's old id is NOT re-queued", ID_CLEARED_OLD in [r[1] for r in requeued], False)
check("and the cleared id is memoized handled",
      json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")),
      {"trello": [ID_CLEARED_OLD]})

print("\nreconcile: the same card WITH a live worker is left alone")
rt = trello_workspace()
write_session(rt, ID_CRASHED, "11111111-2222-3333-4444-555555555555")
n, requeued = run(rt, [make_provider(FakeUtils(LISTS, CARDS_NOW))],
                  live=["11111111-2222-3333-4444-555555555555"])
check("a live worker holds the card", requeued, [])

print("\nreconcile: a board-fetch failure skips trello — nothing is re-queued")
rt = trello_workspace()
n, requeued = run(rt, [make_provider(FakeUtils(LISTS, CARDS_NOW, fail=True))])
check("trello skipped on fetch failure", (n, requeued), (0, []))
check("and nothing is memoized (the whole provider was skipped)",
      os.path.exists(os.path.join(rt, poller.HANDLED_FILE))
      and json.load(open(os.path.join(rt, poller.HANDLED_FILE), encoding="utf-8")).get("trello"),
      None)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
