"""Tests for the 48-hour acknowledgement sweep (scripts/ack_sweep.py): the business-hours clock, the
eligibility vetoes, the live-thread verdict and the ack ledger, the poller-side sweep that writes the due
list, the review's re-check / send / skip / hand-done steps, the worker's ledger lookup, the reserved
seen.json key, and the digest launcher starting the review only when something is due.

Run directly:
    python plugins/drainer/tests/test_ack_sweep.py
"""
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)

import ack_sweep  # noqa: E402
from provider_base import ProviderBase, load_seen, load_ack_ledger, received_dt  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def local(y, mo, d, h, mi=0):
    """A moment in the machine's local zone, the zone the clock counts business hours in."""
    return datetime(y, mo, d, h, mi).astimezone()


# 2026-10-16 is a Friday.
FRI_9 = local(2026, 10, 16, 9)


# --- the business-hours clock --------------------------------------------------------------------------
print("business-hours clock")
check("Friday 9am to Tuesday 9am is 48 business hours",
      round(ack_sweep.business_hours_between(FRI_9, local(2026, 10, 20, 9)), 3), 48.0)
check("Friday 9am to Monday 9am is only 24 (the weekend doesn't count)",
      round(ack_sweep.business_hours_between(FRI_9, local(2026, 10, 19, 9)), 3), 24.0)
check("a Saturday-to-Sunday span counts nothing",
      ack_sweep.business_hours_between(local(2026, 10, 17, 8), local(2026, 10, 18, 20)), 0.0)
check("Wednesday 9am to Friday 9am is 48",
      round(ack_sweep.business_hours_between(local(2026, 10, 14, 9), FRI_9), 3), 48.0)
check("an end before the start is zero", ack_sweep.business_hours_between(FRI_9, FRI_9 - timedelta(hours=1)), 0.0)


# --- a fake source -----------------------------------------------------------------------------------------
class Demo(ProviderBase):
    name = "demo"

    def __init__(self):
        self.thread_calls, self.threads, self.sent, self.cleared, self.listing = [], {}, [], [], []

    def ack_conversation(self, item):
        return item.get("conv")

    def ack_is_bot(self, item):
        return bool(item.get("bot"))

    def correspondent(self, item):
        return item.get("corr")

    def ack_thread(self, item):
        self.thread_calls.append(item["_id"])
        if item["conv"] == "unreadable":
            return None
        return self.threads.get(item["conv"], {"messages": [], "hasDraft": False, "listUnsubscribe": False,
                                               "link": f"https://x/{item['conv']}"})

    def ack_message_text(self, item):
        return f"text of {item['_id']}"

    def ack_send(self, item, body_file):
        self.sent.append((item["_id"], body_file))
        return {"draftId": f"d-{item['_id']}"}

    def clear(self, item):
        self.cleared.append(item["_id"])
        return True

    def enumerate(self, limit):
        return [dict(it) for it in self.listing]

    def stable_id(self, item):
        return item["sid"]


def item(iid, conv, received, corr=None, **kw):
    it = {"_id": iid, "sid": iid, "_source": "demo", "conv": conv, "received": received.astimezone(timezone.utc).isoformat(),
          "from": f"{iid} person <{iid}@example.com>", "fromAddress": f"{iid}@example.com", "subject": f"about {iid}",
          "_bucket": "needs-you", "_sender": "person", "corr": corr or f"{iid}@example.com"}
    it.update(kw)
    return it


NOW = local(2026, 10, 21, 10)  # Wednesday
OLD = local(2026, 10, 16, 9)   # past 48 business hours by NOW
NEW = local(2026, 10, 20, 15)  # Tuesday afternoon: under the threshold


# --- vetoes --------------------------------------------------------------------------------------------
print("\nvetoes")
demo = Demo()
check("a plain person's needs-you item passes", ack_sweep.veto(demo, item("a", "c1", OLD)), None)
check("triage saying automated rules it out", ack_sweep.veto(demo, item("a", "c1", OLD, _sender="automated")),
      "sender not judged a person")
check("an old verdict with no sender field rules it out (fail closed)",
      ack_sweep.veto(demo, item("a", "c1", OLD, _sender=None)), "sender not judged a person")
check("a no-reply address is vetoed even when triage says person",
      ack_sweep.veto(demo, item("a", "c1", OLD, fromAddress="no-reply@shop.example")), "no-reply sender")
check("a donotreply relay with a person behind it is not vetoed",
      ack_sweep.veto(demo, item("a", "c1", OLD, fromAddress="donotreply@jpay.com", corr="relay|securus|sam rivera")), None)
check("a bot sender is vetoed", ack_sweep.veto(demo, item("a", "c1", OLD, bot=True)), "bot or app sender")
check("a self-email is vetoed", ack_sweep.veto(demo, item("a", "c1", OLD, fromMe=True, toMe=True)), "self-email")
check("a screen-flagged item is vetoed",
      ack_sweep.veto(demo, item("a", "c1", OLD, _screen={"flagged": True})), "flagged by the security screen")
check("an fyi item is vetoed", ack_sweep.veto(demo, item("a", "c1", OLD, _bucket="fyi")), "not needs-you")


# --- the live-thread verdict ------------------------------------------------------------------------------
print("\nlive-thread verdict and the ledger")
clock = OLD.astimezone(timezone.utc)


def mine(dt, draft=False):
    return {"fromMe": True, "sent": dt.astimezone(timezone.utc).isoformat(), "isDraft": draft}


empty = {"messages": [], "listUnsubscribe": False}
check("no reply, no ledger entry: eligible", ack_sweep.thread_verdict(empty, clock, None), None)
check("a reply from Russell after arrival rules it out",
      ack_sweep.thread_verdict({"messages": [mine(OLD + timedelta(hours=3))]}, clock, None), "Russell already replied")
check("a staged draft from Russell is not a reply",
      ack_sweep.thread_verdict({"messages": [mine(OLD + timedelta(hours=3), draft=True)]}, clock, None), None)
check("a reply from before the message arrived doesn't count",
      ack_sweep.thread_verdict({"messages": [mine(OLD - timedelta(days=2))]}, clock, None), None)
sent_entry = {"status": "sent", "at": (OLD - timedelta(days=3)).astimezone(timezone.utc).isoformat()}
check("a ledger entry blocks the conversation",
      ack_sweep.thread_verdict({"messages": [mine(OLD - timedelta(days=3))]}, clock, sent_entry),
      "already in the ack ledger (sent)")
check("a real reply after the ledger entry supersedes it, so a new wait can be acked",
      ack_sweep.thread_verdict({"messages": [mine(OLD - timedelta(days=3)), mine(OLD - timedelta(days=1))]},
                               clock, sent_entry), None)
check("a skipped entry stays skipped with no reply since",
      ack_sweep.thread_verdict(empty, clock, {"status": "skipped", "at": sent_entry["at"]}),
      "already in the ack ledger (skipped)")
check("List-Unsubscribe rules a direct sender out",
      ack_sweep.thread_verdict({"messages": [], "listUnsubscribe": True}, clock, None), "bulk mail (List-Unsubscribe)")
check("List-Unsubscribe on a relay notification (LinkedIn) doesn't",
      ack_sweep.thread_verdict({"messages": [], "listUnsubscribe": True}, clock, None, relay=True), None)


# --- the poller-side sweep --------------------------------------------------------------------------------
print("\nsweep")


def runtime(ledger=None):
    rt = tempfile.mkdtemp(prefix="acksweep-")
    with open(os.path.join(rt, "seen.json"), "w", encoding="utf-8") as f:
        json.dump({"demo": {}, "_acks": ledger or {}}, f)
    return rt


demo = Demo()
rt = runtime()
held = [item("old", "c-old", OLD), item("fresh", "c-fresh", NEW)]
due = ack_sweep.sweep(held, {"demo": demo}, rt, set(), now=NOW)
check("only the item past 48 business hours is due", [d["itemId"] for d in due], ["old"])
check("the thread is read only for the item past the threshold", demo.thread_calls, ["old"])
with open(os.path.join(rt, ack_sweep.DUE_FILE), encoding="utf-8") as f:
    written = json.load(f)
check("the due list is written to the runtime dir", [d["key"] for d in written["due"]], ["demo|c-old"])
check("the entry carries the link, wait, and send path",
      (written["due"][0]["link"], written["due"][0]["businessHours"] >= 48, written["due"][0]["sendPath"]),
      ("https://x/c-old", True, "api"))

del demo.thread_calls[:]
ack_sweep.sweep(held, {"demo": demo}, rt, set(), now=NOW + timedelta(minutes=5))
check("a second cycle within the cache window doesn't re-read the thread", demo.thread_calls, [])

demo = Demo()
rt = runtime()
due = ack_sweep.sweep([item("old", "c-old", OLD, corr="pat@example.com")], {"demo": demo}, rt,
                      {"pat@example.com"}, now=NOW)
check("an item whose correspondent has a worker open gets no ack", due, [])

demo = Demo()
rt = runtime()
due = ack_sweep.sweep([item("u", "unreadable", OLD)], {"demo": demo}, rt, set(), now=NOW)
check("an unreadable thread is skipped, not acked on a guess", due, [])

demo = Demo()
rt = runtime({"demo|c-old": {"status": "sent", "at": OLD.astimezone(timezone.utc).isoformat()}})
due = ack_sweep.sweep([item("old", "c-old", OLD)], {"demo": demo}, rt, set(), now=NOW)
check("an already-acked conversation isn't proposed again", due, [])

demo = Demo()
rt = runtime()
demo.threads["c-replied"] = {"messages": [mine(OLD + timedelta(hours=1))], "hasDraft": False,
                             "listUnsubscribe": False, "link": ""}
due = ack_sweep.sweep([item("r", "c-replied", OLD)], {"demo": demo}, rt, set(), now=NOW)
check("a conversation Russell answered from his phone isn't proposed", due, [])

demo = Demo()
rt = runtime()
held = [item("p1", "c-a", OLD, corr="pat@example.com"),
        item("p2", "c-b", OLD + timedelta(hours=2), corr="pat@example.com")]
due = ack_sweep.sweep(held, {"demo": demo}, rt, set(), now=NOW)
check("one ack per person, on their most recent waiting thread", [d["itemId"] for d in due], ["p2"])
check("their other waiting thread rides along as covered", [c["key"] for c in due[0]["covers"]], ["demo|c-a"])

demo = Demo()
rt = runtime()
held = [item("m1", "c-same", OLD), item("m2", "c-same", OLD + timedelta(days=1), corr="m1@example.com")]
due = ack_sweep.sweep(held, {"demo": demo}, rt, set(), now=NOW)
check("two waiting messages in one conversation make one entry", len(due), 1)
check("it replies to the newest and clocks from the oldest",
      (due[0]["itemId"], due[0]["clockStart"]), ("m2", OLD.astimezone(timezone.utc).isoformat()))

demo = Demo()
rt = runtime()
due = ack_sweep.sweep([item("li", "c-li", OLD, corr="relay|linkedin|sam rivera", fromAddress="messages-noreply@linkedin.com")],
                      {"demo": demo}, rt, set(), now=NOW)
check("a relayed person's message is due and sent by hand", [d["sendPath"] for d in due], ["by-hand"])

rt = runtime()
ack_sweep.sweep([item("old", "c-old", OLD)], {"demo": Demo()}, rt, set(), now=NOW, dry_run=True)
check("a dry run writes no due list", os.path.exists(os.path.join(rt, ack_sweep.DUE_FILE)), False)

out_of_scope = ProviderBase()
out_of_scope.name = "trello"
rt = runtime()
due = ack_sweep.sweep([{"_id": "card", "_source": "trello", "_bucket": "needs-you", "_sender": "person",
                        "received": OLD.isoformat()}], {"trello": out_of_scope}, rt, set(), now=NOW)
check("a source with no conversation (Trello) is out of scope", due, [])


# --- the reserved seen.json key --------------------------------------------------------------------------
print("\nreserved seen.json key")
rt = runtime({"demo|c": {"status": "sent", "at": "x"}})
check("the ledger reads back from seen.json", load_ack_ledger(rt), {"demo|c": {"status": "sent", "at": "x"}})
check("load_seen never returns the ledger as a source", load_seen(rt, "_acks"), {})
check("a real source still reads normally", load_seen(rt, "demo"), {})
with open(os.path.join(rt, "seen.json"), "w", encoding="utf-8") as f:
    f.write("{ corrupt")
check("a corrupt seen.json reads as an empty ledger", load_ack_ledger(rt), {})


# --- the review's re-check, send, skip, and hand-done ----------------------------------------------------
print("\nreview")
sent_outcomes = []
ack_sweep.has_receipt = lambda body: "unreviewed" not in body
ack_sweep.record_outcome = lambda *a: sent_outcomes.append(a) or "recorded"


def review_for(due_entries, listing, opened=frozenset()):
    rt = runtime()
    with open(os.path.join(rt, ack_sweep.DUE_FILE), "w", encoding="utf-8") as f:
        json.dump({"due": due_entries}, f)
    demo = Demo()
    demo.listing = listing
    r = object.__new__(ack_sweep.Review)
    r.runtime_dir, r.providers, r._listings, r._open = rt, {"demo": demo}, {}, set(opened)
    return r, demo


def due_entry(it, covers=()):
    return {"key": f"demo|{it['conv']}", "source": "demo", "itemId": it["_id"], "from": it["from"],
            "subject": it["subject"], "correspondent": it["corr"], "clockStart": received_dt(it).isoformat(),
            "businessHours": 72.0, "link": "", "hasDraft": False, "sendPath": "api", "item": it,
            "covers": list(covers)}


waiting = item("w", "c-w", OLD)
r, demo = review_for([due_entry(waiting)], [waiting])
current, reason, _ = r.recheck(r.entry("demo|c-w"))
check("a still-waiting item re-checks as eligible", (current and current["_id"], reason), ("w", None))

r, demo = review_for([due_entry(waiting)], [])
check("an item gone from the source is dropped", r.recheck(r.entry("demo|c-w"))[1],
      "no longer waiting (dispatched, handled, or read)")

r, demo = review_for([due_entry(waiting)], [waiting], opened={"w@example.com"})
check("an item whose person now has a worker open is dropped", r.recheck(r.entry("demo|c-w"))[1],
      "a worker is already open for this person")

r, demo = review_for([due_entry(waiting)], [waiting])
demo.threads["c-w"] = {"messages": [mine(NOW - timedelta(minutes=10))], "listUnsubscribe": False}
check("a reply Russell sent while reading drops the ack", r.recheck(r.entry("demo|c-w"))[1], "Russell already replied")

import contextlib  # noqa: E402
import io  # noqa: E402

r, demo = review_for([due_entry(waiting), due_entry(item("gone", "c-gone", OLD))], [waiting])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ack_sweep.cmd_prepare(r)
rows = json.loads(buf.getvalue())
check("prepare marks the waiting entry ready and the vanished one dropped",
      [(row["key"], row["status"]) for row in rows], [("demo|c-w", "ready"), ("demo|c-gone", "dropped")])
check("a ready row carries what the person said", rows[0]["message"], "text of w")
check("score channels map per source and send path",
      (ack_sweep.score_channel({"source": "outlook-graph", "sendPath": "api"}),
       ack_sweep.score_channel({"source": "slack", "sendPath": "api"}),
       ack_sweep.score_channel({"source": "gmail", "sendPath": "by-hand"}, linkedin=True),
       ack_sweep.score_channel({"source": "gmail", "sendPath": "by-hand"})),
      ("outlook-personal", "slack", "linkedin", "other"))

body_dir = tempfile.mkdtemp()
body = os.path.join(body_dir, "ack.md")
with open(body, "w", encoding="utf-8") as f:
    f.write("Thanks for sending this - I'll take a look.\n")

covered = item("w0", "c-w0", OLD)
r, demo = review_for([due_entry(waiting, covers=[{"key": "demo|c-w0", "itemId": "w0", "subject": "x"}])], [waiting])
try:
    r_out = ack_sweep.cmd_send(r, "demo|c-w", body)
except SystemExit as e:
    r_out = e
ledger = load_ack_ledger(r.runtime_dir)
check("send goes through the source's send path", demo.sent, [("w", body)])
check("the ledger records the sent ack with its reference and text",
      (ledger["demo|c-w"]["status"], ledger["demo|c-w"]["ref"], ledger["demo|c-w"]["text"]),
      ("sent", {"draftId": "d-w"}, "Thanks for sending this - I'll take a look."))
check("the person's other waiting thread is written as covered", ledger["demo|c-w0"]["status"], "covered")
check("the item itself is left alone (not cleared, not recorded seen)",
      (demo.cleared, load_seen(r.runtime_dir, "demo")), ([], {}))
check("the send-confidence outcome is recorded as sent", sent_outcomes[-1][-2:], ("--sent-file", body))

r, demo = review_for([due_entry(waiting)], [waiting])
ack_sweep.cmd_send(r, "demo|c-w", body, handles=True)
check("a reply that handles the item clears it", demo.cleared, ["w"])
check("and records it seen so it never dispatches", "w" in load_seen(r.runtime_dir, "demo"), True)
check("and never reaches the ledger", load_ack_ledger(r.runtime_dir), {})

unreviewed = os.path.join(body_dir, "unreviewed.md")
with open(unreviewed, "w", encoding="utf-8") as f:
    f.write("hi")
r, demo = review_for([due_entry(waiting)], [waiting])
try:
    ack_sweep.cmd_send(r, "demo|c-w", unreviewed)
    refused = False
except SystemExit:
    refused = True
check("a body with no review receipt is refused before anything is sent", (refused, demo.sent), (True, []))

r, demo = review_for([due_entry(waiting)], [])
try:
    ack_sweep.cmd_send(r, "demo|c-w", body)
    code = 0
except SystemExit as e:
    code = e.code
check("the pre-send re-check stops a send for an item no longer waiting", (code, demo.sent), (3, []))

r, demo = review_for([dict(due_entry(waiting), sendPath="by-hand")], [waiting])
try:
    ack_sweep.cmd_send(r, "demo|c-w", body)
    refused = False
except SystemExit:
    refused = True
check("a by-hand entry has no programmatic send", (refused, demo.sent), (True, []))
ack_sweep.cmd_hand_done(r, "demo|c-w")
check("hand-done records it sent by hand", load_ack_ledger(r.runtime_dir)["demo|c-w"]["ref"], {"byHand": True})

r, demo = review_for([due_entry(waiting)], [waiting])
ack_sweep.cmd_skip(r, "demo|c-w", body)
check("skip writes a skipped ledger entry", load_ack_ledger(r.runtime_dir)["demo|c-w"]["status"], "skipped")
check("and records the draft as discarded and moot", ("--moot" in sent_outcomes[-1], "--discarded" in sent_outcomes[-1]),
      (True, True))


# --- the worker's ledger lookup ----------------------------------------------------------------------------
print("\nworker lookup")
rt = runtime({"demo|c-w": {"status": "sent", "at": "t", "text": "Thanks"}})
check("a captured record finds its conversation's ack",
      ack_sweep.lookup(rt, {"demo": Demo()}, {"source": "demo", "conv": "c-w"}),
      {"key": "demo|c-w", "status": "sent", "at": "t", "text": "Thanks"})
check("a conversation with no ack finds nothing", ack_sweep.lookup(rt, {"demo": Demo()}, {"source": "demo", "conv": "c-z"}), None)


# --- the digest launcher starts the review only when something is due ------------------------------------
print("\ndigest launcher")
spec = importlib.util.spec_from_file_location("run_digest", os.path.join(SCRIPTS, "run-digest.py"))
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)
launches = []
digest.spawn_bg = lambda seed, model, cwd, name, **kw: launches.append(name) or "bg-1"
digest.write_receipt = lambda prompt_file, bg_id: bg_id
rt = runtime()
check("no due list: no review session", (digest.launch_ack_review(rt, "R", {"digest_model": "m"}), launches), (None, []))
with open(os.path.join(rt, ack_sweep.DUE_FILE), "w", encoding="utf-8") as f:
    json.dump({"due": [due_entry(waiting)]}, f)
check("a non-empty due list launches the review", digest.launch_ack_review(rt, "R", {"digest_model": "m"}), "bg-1")
check("named for the session list", launches, ["Drainer ack review"])
with open(os.path.join(rt, "seeds", "ack-review.prompt.txt"), encoding="utf-8") as f:
    seed = f.read()
check("the seed points at the ack-review engine doc and the script", ("ack-review.md" in seed, "ack_sweep.py" in seed),
      (True, True))

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
