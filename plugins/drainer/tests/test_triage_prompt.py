"""Tests for run-poller.py's AUTO-HANDLE assembly: the local overlay and the trigger gate.

Run directly:
    python plugins/drainer/tests/test_auto_handle_rules.py
"""
import importlib.util
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


SHIPPED = """# demo provider

## AUTO-HANDLE
Standing rules preamble.

1. **Shipped rule** - does a thing.
   - **Trigger:** `subject=^Weekly report`
   - **Action:** do it.

## CLEAR
whatever
"""

OVERLAY = """# machine-local rules for demo

## AUTO-HANDLE
Overlay preamble that should not displace the shipped one.

1. **Personal rule** - vendor-specific thing.
   - **Trigger:** `from=fred@example\\.com; subject=^Your meeting recap`
   - **Action:** run the helper.
"""


def workspace(with_overlay=True):
    """A providers dir holding the shipped file, plus a local_dir holding the overlay."""
    root = tempfile.mkdtemp(prefix="autohandle-")
    providers = os.path.join(root, "providers")
    local = os.path.join(root, "local")
    os.makedirs(providers)
    os.makedirs(os.path.join(local, "providers"))
    with open(os.path.join(providers, "demo-provider.md"), "w", encoding="utf-8") as f:
        f.write(SHIPPED)
    if with_overlay:
        p = os.path.join(local, "providers", "demo-provider.local.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(OVERLAY)
    return providers, local


def item(**kw):
    base = {"_source": "demo", "from": "", "subject": "", "preview": ""}
    base.update(kw)
    return base


# --- overlay ---------------------------------------------------------------
print("local overlay")
providers, local = workspace()
preamble, rules = poller._auto_handle_section(providers, local, "demo")
check("shipped preamble is kept, heading and all", preamble, "## AUTO-HANDLE\nStanding rules preamble.")
check("the overlay's preamble does not displace it", "Overlay preamble" in preamble, False)
check("both rules are collected", len(rules), 2)
check("shipped rule present", any("Shipped rule" in r for r in rules), True)
check("overlay rule present", any("Personal rule" in r for r in rules), True)

providers_only, local_only = workspace(with_overlay=False)
_, rules_no_overlay = poller._auto_handle_section(providers_only, local_only, "demo")
check("no overlay means only the shipped rule", len(rules_no_overlay), 1)

# The section must survive a local_dir that has no providers dir at all.
check(
    "missing overlay dir is not an error",
    poller._auto_handle_section(providers_only, os.path.join(local_only, "nope"), "demo")[1].__len__(),
    1,
)

# A shipped provider with no standing rules of its own, given some by the machine, is the main case
# this overlay exists for.
overlay_only_providers = os.path.join(tempfile.mkdtemp(prefix="autohandle-plain-"), "providers")
os.makedirs(overlay_only_providers)
with open(os.path.join(overlay_only_providers, "demo-provider.md"), "w", encoding="utf-8") as f:
    f.write("# demo provider\n\n## CLEAR\nnothing standing here\n")
pre_only, rules_only = poller._auto_handle_section(overlay_only_providers, local, "demo")
check("overlay supplies the rule when the shipped provider has none", len(rules_only), 1)
check("and supplies the preamble too", "Overlay preamble" in pre_only, True)

# --- trigger parsing -------------------------------------------------------
print("\ntrigger parsing - anything unparseable must load unconditionally")
check("no trigger declared", poller._parse_trigger("1. **X** - a rule with no trigger."), None)
check(
    "malformed pair (no '=')",
    poller._parse_trigger("1. **X**\n   - **Trigger:** `subject`\n"),
    None,
)
check(
    "invalid regex",
    poller._parse_trigger("1. **X**\n   - **Trigger:** `subject=[unclosed`\n"),
    None,
)
parsed = poller._parse_trigger("1. **X**\n   - **Trigger:** `from=a@b\\.com; subject=^Hi`\n")
check("well-formed trigger yields both conditions", len(parsed), 2)
check("fields are lowercased", [f for f, _ in parsed], ["from", "subject"])

# --- gating ----------------------------------------------------------------
print("\ntrigger gate")
_, rules = poller._auto_handle_section(providers, local, "demo")

check("no items means nothing fires", poller._section_fires(rules, []), False)
check(
    "an unrelated item does not fire the section",
    poller._section_fires(rules, [item(subject="Lunch?", **{"from": "bob@x.com"})]),
    False,
)
check(
    "the shipped rule's trigger fires it",
    poller._section_fires(rules, [item(subject="Weekly report for July")]),
    True,
)
check(
    "the overlay rule's trigger fires it",
    poller._section_fires(
        rules, [item(subject="Your meeting recap - Jul 20", **{"from": "fred@example.com"})]
    ),
    True,
)
check(
    "both conditions must hold - right sender, wrong subject",
    poller._section_fires(rules, [item(subject="Newsletter", **{"from": "fred@example.com"})]),
    False,
)
check(
    "both conditions must hold - right subject, wrong sender",
    poller._section_fires(
        rules, [item(subject="Your meeting recap - Jul 20", **{"from": "someone@else.com"})]
    ),
    False,
)
check("matching is case-insensitive", poller._section_fires(rules, [item(subject="WEEKLY REPORT")]), True)

untriggered = ["1. **Always on** - no trigger here.\n   - **Action:** do it."]
check(
    "one untriggered rule keeps the whole section always-on",
    poller._section_fires(untriggered + rules, []),
    True,
)

# --- end to end ------------------------------------------------------------
# _auto_handle_rules resolves the shipped provider through the module-level PROVIDERS_DIR, so point
# that at the fixture instead of the real plugin dir.
print("\n_auto_handle_rules end to end")
poller.PROVIDERS_DIR = providers
by_name = {"demo": object()}
out_quiet = poller._auto_handle_rules(by_name, local, items=[item(subject="Lunch?")])
check("nothing relevant in the batch sends no rules at all", out_quiet, "")

out_fires = poller._auto_handle_rules(
    by_name, local, items=[item(subject="Your meeting recap", **{"from": "fred@example.com"})]
)
check("a matching item sends the section", "### demo" in out_fires, True)
check("and it carries both rules", "Personal rule" in out_fires and "Shipped rule" in out_fires, True)

other_source = poller._auto_handle_rules(
    by_name,
    local,
    items=[item(_source="somewhere-else", subject="Weekly report")],
)
check("an item from another source does not fire this provider", other_source, "")

check(
    "items=None keeps the old always-load behaviour",
    "Personal rule" in poller._auto_handle_rules(by_name, local),
    True,
)

# --- context.md section gating ---------------------------------------------
print("\ncontext.md section gating")

CONTEXT = """# drainer context — the shared brain

Preamble that belongs in every prompt.

## Preferences (always relevant)
Judgment-shaped world knowledge, no trigger, always sent.

## Fireflies recap emails
**Trigger:** `from=fred@example\\.com; subject=^Your meeting recap`

How to handle a Fireflies recap.

## GitHub notification triage
**Trigger:** `from=notifications@github\\.com`

How to handle GitHub mail.
"""

ctx_dir = tempfile.mkdtemp(prefix="ctx-")
with open(os.path.join(ctx_dir, "context.md"), "w", encoding="utf-8") as f:
    f.write(CONTEXT)

everything = poller._context_for_batch(ctx_dir, None)
check("items=None sends the whole file", "GitHub notification triage" in everything, True)

quiet = poller._context_for_batch(ctx_dir, [item(subject="Lunch?", **{"from": "bob@x.com"})])
check("untriggered preamble always survives", "shared brain" in quiet, True)
check("untriggered section always survives", "Preferences (always relevant)" in quiet, True)
check("unmatched section is dropped", "Fireflies recap emails" in quiet, False)
check("other unmatched section is dropped too", "GitHub notification triage" in quiet, False)

ff = poller._context_for_batch(
    ctx_dir, [item(subject="Your meeting recap - Jul 20", **{"from": "fred@example.com"})]
)
check("a matching item pulls its section in", "How to handle a Fireflies recap" in ff, True)
check("but not the unrelated one", "How to handle GitHub mail" in ff, False)

both = poller._context_for_batch(
    ctx_dir,
    [
        item(subject="Your meeting recap", **{"from": "fred@example.com"}),
        item(**{"from": "notifications@github.com"}),
    ],
)
check("two matching items pull both sections", "How to handle GitHub mail" in both and "How to handle a Fireflies recap" in both, True)

check(
    "gating actually shrinks the payload",
    len(quiet) < len(everything),
    True,
)

# A trigger may match on the provider name, for a section that is about a whole source.
SRC_CONTEXT = """# brain

## Slack etiquette
**Trigger:** `source=^slack$`

Only when a Slack item is in hand.
"""
src_dir = tempfile.mkdtemp(prefix="ctx-src-")
with open(os.path.join(src_dir, "context.md"), "w", encoding="utf-8") as f:
    f.write(SRC_CONTEXT)
check(
    "source= matches the provider name",
    "Only when a Slack item" in poller._context_for_batch(src_dir, [item(_source="slack")]),
    True,
)
check(
    "source= does not match a different provider",
    "Only when a Slack item" in poller._context_for_batch(src_dir, [item(_source="gmail")]),
    False,
)

check(
    "a missing context.md is not an error",
    poller._context_for_batch(os.path.join(ctx_dir, "nope"), [item()]),
    "",
)

# --- write_worker_context: the per-item slice a worker reads at step 0 -----
print("write_worker_context")
wc_dir = tempfile.mkdtemp(prefix="wc-")
with open(os.path.join(wc_dir, "context.md"), "w", encoding="utf-8") as f:
    f.write(SRC_CONTEXT)  # one always preamble + a `source=^slack$`-gated section
items_dir = tempfile.mkdtemp(prefix="wc-items-")
json_file = os.path.join(items_dir, "abc123.json")

slack_ctx = poller.write_worker_context(item(_source="slack"), wc_dir, json_file)
check("slice is written beside the item json", slack_ctx, os.path.join(items_dir, "abc123.context.md"))
slack_text = open(slack_ctx, encoding="utf-8").read()
check("a slack item's slice keeps the slack section", "Only when a Slack item" in slack_text, True)

trello_ctx = poller.write_worker_context(item(_source="trello"), wc_dir, json_file)
trello_text = open(trello_ctx, encoding="utf-8").read()
check("a non-slack item's slice drops the slack section", "Only when a Slack item" in trello_text, False)
check("but keeps the always-loaded preamble", trello_text.startswith("# brain"), True)
check("the slice is smaller than the full file", len(trello_text) < len(slack_text), True)

check(
    "no context.md means no slice file (seed falls back to the full file)",
    poller.write_worker_context(item(_source="slack"), os.path.join(wc_dir, "nope"), json_file),
    None,
)

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
