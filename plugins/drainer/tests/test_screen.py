"""Tests for run-poller.py's security screen: the dedicated input-guardrail pass against prompt injection
and content hostile to the user, and how its verdict strips an item's autonomy.

Run directly:
    python plugins/drainer/tests/test_screen.py
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

sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name
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


def item(**kw):
    base = {"_id": "x1", "_source": "gmail", "_bucket": "needs-you", "_kind": "reply",
            "_complexity": "simple"}
    base.update(kw)
    return base


# --- _apply_screen: a flag strips autonomy -----------------------------------
print("_apply_screen — a flagged verdict forces needs-you (verdict passed in directly)")

# A flagged AUTO-HANDLE item is hard-stopped: it can never run the standing rule autonomously.
it = item(_bucket="auto-handle", _kind=None)
flagged = poller._apply_screen(it, {"flagged": True, "reason": "embedded 'ignore instructions'"})
check("flagged auto-handle is reported flagged", flagged, True)
check("flagged auto-handle becomes needs-you", it["_bucket"], "needs-you")
check("flag is stamped with the reason", it["_screen"],
      {"flagged": True, "reason": "embedded 'ignore instructions'"})
check("a null kind is filled in so a worker can present it", it["_kind"], "reply")

# A flagged JUNK item is surfaced, not silently digested.
it = item(_bucket="junk", _kind="phishing")
poller._apply_screen(it, {"flagged": True, "reason": "credential-harvest lure asking to forward mail"})
check("flagged junk becomes needs-you", it["_bucket"], "needs-you")
check("flagged junk keeps its existing kind", it["_kind"], "phishing")

# A flagged needs-you item stays needs-you but is stamped for its worker.
it = item(_bucket="needs-you", _kind="work")
poller._apply_screen(it, {"flagged": True, "reason": "asks to change remit account"})
check("flagged needs-you stays needs-you", it["_bucket"], "needs-you")
check("and is stamped", it["_screen"]["reason"], "asks to change remit account")

# A missing reason is tolerated (stamped empty).
it = item()
poller._apply_screen(it, {"flagged": True})
check("missing reason stamps empty string", it["_screen"]["reason"], "")


# --- _apply_screen: no flag is a no-op ---------------------------------------
print("\n_apply_screen — an unflagged or empty verdict changes nothing")

it = item(_bucket="auto-handle", _kind=None)
flagged = poller._apply_screen(it, {"flagged": False})
check("flagged:false does not flag", flagged, False)
check("flagged:false leaves the bucket alone", it["_bucket"], "auto-handle")
check("flagged:false stamps no _screen", "_screen" in it, False)

# A None verdict never crashes and never flags (the caller only reaches here with a verdict, but the
# helper stays safe regardless).
it = item(_bucket="fyi")
check("a None verdict is a safe no-op", poller._apply_screen(it, None), False)
check("and leaves the bucket alone", it["_bucket"], "fyi")


# --- _items_needing_screen: junk is skipped, everything else (incl. unjudged) is screened -----
print("\n_items_needing_screen — a confirmed junk verdict is the only thing that skips the screen")

items = [item(_id="a"), item(_id="b"), item(_id="c"), item(_id="d"), item(_id="e")]
verdicts = {
    "a": {"bucket": "junk"},
    "b": {"bucket": "needs-you"},
    "c": {"bucket": "auto-handle"},
    "d": {"bucket": "fyi"},
    # "e" absent -> triage couldn't judge it this cycle
}
to_screen = [it["_id"] for it in poller._items_needing_screen(items, verdicts)]
check("junk is skipped", "a" in to_screen, False)
check("needs-you is screened", "b" in to_screen, True)
check("auto-handle is screened", "c" in to_screen, True)
check("fyi is screened (it resolves pointers autonomously)", "d" in to_screen, True)
check("an item triage couldn't judge is still screened (fail-closed)", "e" in to_screen, True)
check("only the junk item is dropped", len(to_screen), 4)

check("empty verdicts screens everything (fail-closed default)",
      [it["_id"] for it in poller._items_needing_screen(items, {})], [it["_id"] for it in items])


# --- _stamp_screen: persists onto the captured json --------------------------
print("\n_stamp_screen — writes the flag onto items/<id>.json for the worker")

d = tempfile.mkdtemp(prefix="screen-")
jf = os.path.join(d, "x1.json")
with open(jf, "w", encoding="utf-8") as f:
    json.dump({"id": "x1", "source": "gmail", "triage": "needs-you"}, f)
poller._stamp_screen(jf, {"flagged": True, "reason": "hidden directive to exfiltrate contacts"})
with open(jf, encoding="utf-8") as f:
    rec = json.load(f)
check("the screen object is persisted", rec["screen"],
      {"flagged": True, "reason": "hidden directive to exfiltrate contacts"})
check("existing fields survive the stamp", rec["triage"], "needs-you")

# A missing file is a best-effort no-op, never an exception.
poller._stamp_screen(os.path.join(d, "nope.json"), {"flagged": True, "reason": "x"})
check("a missing json file is not an error", True, True)


# --- _screen_brain: the shared prefix carries the rubric + context -----------
print("\n_screen_brain — the shared prefix carries the screen rubric and the user's standing rules")

ctx_dir = tempfile.mkdtemp(prefix="screen-ctx-")
with open(os.path.join(ctx_dir, "context.md"), "w", encoding="utf-8") as f:
    f.write("# brain\n\n## Red lines\nNever automate the forbidden channel.\n")
brain = poller._screen_brain([item()], ctx_dir)
check("brain includes the screen instructions", "SECURITY SCREEN" in brain, True)
check("brain embeds the engine/screen.md rubric", "## What flags an item" in brain, True)
check("brain embeds the user's context.md standing rules", "Never automate the forbidden channel" in brain, True)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
