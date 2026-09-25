"""Tests for the dispatch hold-by-correspondent throttle: the correspondent identity each provider
resolves (direct sender vs relay), the live-session set that holds a second item open, and the in-cycle
dedup discipline that keeps two duplicates arriving in one cycle from both spawning.

Run directly:
    python plugins/drainer/tests/test_correspondent_hold.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SCRIPTS = os.path.join(PLUGIN_ROOT, "skills", "drainer", "scripts")
POLLER = os.path.join(SCRIPTS, "run-poller.py")

sys.path.insert(0, SCRIPTS)  # run-poller and provider_base import their siblings by bare name
import provider_base  # noqa: E402
spec = importlib.util.spec_from_file_location("run_poller", POLLER)
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


# The real Securus/JPay notification body, as capture writes it to items/<id>.email.md. The From is a
# shared no-reply that fronts every incarcerated contact; the person is named only in the body.
SECURUS_BODY_TYLER = (
    "You have received a new message Hello, You have received a new Message from: TYLER COSSEY "
    "Please login to your Friend & Family account on the mobile app or log in using the website to "
    "view your new message. This is an auto-notification email, please do not reply."
)
SECURUS_BODY_JANE = SECURUS_BODY_TYLER.replace("TYLER COSSEY", "JANE DOE")


def securus_item(body):
    """A Securus enumerate item whose preview carries the name (the common case), so no body fetch."""
    return {"from": "Securus eMessaging <donotreply@jpay.com>", "fromAddress": "donotreply@jpay.com",
            "subject": "New Mail From A Correctional Facility", "preview": body[:300]}


class ProviderStub(provider_base.ProviderBase):
    """A minimal email adapter: inherits the shared `correspondent`, overrides only the body fetch so a
    relay whose name lives past the preview still resolves."""
    name = "stub"

    def __init__(self, body=""):
        self._body = body

    def _fetch_body(self, item):
        return self._body


print("relay correspondent: Securus is keyed on the named person, not the shared From address")
p = ProviderStub()
tyler = p.correspondent(securus_item(SECURUS_BODY_TYLER))
tyler2 = p.correspondent(securus_item(SECURUS_BODY_TYLER))
jane = p.correspondent(securus_item(SECURUS_BODY_JANE))
check("the name is extracted and namespaced", tyler, "relay|securus|tyler cossey")
check("the same person yields the same key across notices", tyler, tyler2)
check("two different people on the same From address do NOT collapse", tyler == jane, False)

print("\nrelay correspondent: the name can live in the body when the preview is truncated before it")
# A preview cut off before the name — extraction must fall through to the fetched body, not the address.
short = {"from": "Securus eMessaging <donotreply@jpay.com>", "fromAddress": "donotreply@jpay.com",
         "subject": "New Mail From A Correctional Facility", "preview": "You have received a new message"}
check("body fetch resolves the correspondent",
      ProviderStub(body=SECURUS_BODY_TYLER).correspondent(short), "relay|securus|tyler cossey")

print("\nrelay correspondent: a recognized relay we can't extract from is NEVER held (no wrong collapse)")
blank = {"from": "Securus eMessaging <donotreply@jpay.com>", "fromAddress": "donotreply@jpay.com",
         "subject": "New Mail From A Correctional Facility", "preview": "You have new mail. Please log in."}
check("no name anywhere -> None (falls through to no hold, never to the shared address)",
      ProviderStub().correspondent(blank), None)

print("\nself-note: fromMe+toMe never holds, even against another self-note from the same address")
selfnote_a = ProviderStub().correspondent({"from": "Russell Rutledge <russell.rutledge@outlook.com>",
                                            "fromAddress": "russell.rutledge@outlook.com",
                                            "fromMe": True, "toMe": True})
selfnote_b = ProviderStub().correspondent({"from": "Russell Rutledge <russell.rutledge@outlook.com>",
                                            "fromAddress": "russell.rutledge@outlook.com",
                                            "fromMe": True, "toMe": True})
check("a self-note has no correspondent identity to hold on", selfnote_a, None)
check("two self-notes from the same address both get None, not a shared key", selfnote_b, None)

print("\ndirect sender: the correspondent IS the From address, so two of their messages share it")
a = ProviderStub().correspondent({"from": "Jane Q <jane@example.com>", "fromAddress": "jane@example.com"})
b = ProviderStub().correspondent({"from": "Jane Q <jane@example.com>", "fromAddress": "jane@example.com"})
check("keyed on the lowercased address", a, "jane@example.com")
check("two messages from the same person share the key", a, b)
check("a From with only a display name and address still resolves",
      ProviderStub().correspondent({"from": "Bob <bob@ex.com>"}), "bob@ex.com")
check("no sender at all -> no identity, never held", ProviderStub().correspondent({}), None)


def workspace(sessions):
    """A runtime_dir with seeds/<id>.prompt.txt.session (guid) + items/<id>.json (correspondent), one
    per (iid, guid, correspondent) triple, mirroring what a dispatched worker leaves behind."""
    rt = tempfile.mkdtemp(prefix="corr-hold-")
    os.makedirs(os.path.join(rt, "seeds"), exist_ok=True)
    os.makedirs(os.path.join(rt, "items"), exist_ok=True)
    for iid, guid, corr in sessions:
        with open(os.path.join(rt, "seeds", f"{iid}.prompt.txt.session"), "w", encoding="utf-8") as f:
            f.write(guid)
        with open(os.path.join(rt, "items", f"{iid}.json"), "w", encoding="utf-8") as f:
            json.dump({"id": iid, "correspondent": corr}, f)
    return rt


print("\nopen_correspondents: a LIVE worker on an item holds that correspondent open")
GUID = "11111111-2222-3333-4444-555555555555"
rt = workspace([("item-a", GUID, "relay|securus|tyler cossey")])
check("live session -> its correspondent is held-open",
      poller.open_correspondents(rt, {GUID}), {"relay|securus|tyler cossey"})

print("\nopen_correspondents: a DEAD worker (guid no longer live) releases the hold within one cycle")
check("guid not in the live set -> not held-open (the crashed-worker fail-safe)",
      poller.open_correspondents(rt, {"99999999-0000-0000-0000-000000000000"}), set())
check("no live scan (None) -> empty, so a cross-cycle hold fails open",
      poller.open_correspondents(rt, None), set())

print("\nopen_correspondents against the real live scan: a full-guid receipt and a legacy short-id one")
# Receipts now hold the full guid (write_receipt resolves it); older ones, or a launch whose guid
# couldn't be resolved, hold the short id. live_session_ids must make both match.
FULL = "6997ef2f-aaaa-bbbb-cccc-dddddddddddd"
rt2 = workspace([("item-full", FULL, "jane@example.com"),
                 ("item-short", "33ddd28a", "relay|securus|tyler cossey"),
                 ("item-dead", "deadbeef", "bob@ex.com")])
real_agents = poller.claude_agents
poller.claude_agents = lambda: [
    {"kind": "background", "id": "6997ef2f", "sessionId": FULL, "pid": 1},
    {"kind": "background", "id": "33ddd28a", "sessionId": "33ddd28a-1111-2222-3333-444444444444", "pid": 2},
    {"kind": "background", "id": "deadbeef", "sessionId": "deadbeef-1111-2222-3333-444444444444"},  # no pid
]
try:
    live = poller.live_session_ids()
finally:
    poller.claude_agents = real_agents
check("full-guid and short-id receipts both held; the pid-less session released",
      poller.open_correspondents(rt2, live), {"jane@example.com", "relay|securus|tyler cossey"})

print("\nin-cycle dedup: two duplicates in ONE cycle -> first dispatches, the rest wait")
# Replays the needs-loop discipline from main: check held_for_correspondent, and register the
# correspondent only on dispatch. Two items sharing a key must not both spawn.
active = set()  # nothing open from a prior cycle
decisions = []
for corr in ["relay|securus|tyler cossey", "relay|securus|tyler cossey", "jane@example.com"]:
    if poller.held_for_correspondent(corr, active):
        decisions.append("hold")
        continue
    decisions.append("dispatch")
    if corr:
        active.add(corr)
check("first of a pair dispatches, the duplicate waits, an unrelated sender dispatches",
      decisions, ["dispatch", "hold", "dispatch"])

print("\nin-cycle dedup: a needs-you item waits behind an already-open correspondent from a prior cycle")
active = {"relay|securus|tyler cossey"}  # a worker from an earlier cycle still open
check("its duplicate is held", poller.held_for_correspondent("relay|securus|tyler cossey", active), True)
check("a None identity is never held", poller.held_for_correspondent(None, active), False)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
