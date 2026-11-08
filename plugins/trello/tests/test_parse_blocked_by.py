"""Test for scripts/trello_utils.py's parse_blocked_by.

Run directly: python plugins/trello/tests/test_parse_blocked_by.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.abspath(os.path.join(HERE, "..", "skills", "trello", "scripts"))
sys.path.insert(0, SCRIPTS_DIR)

from trello_utils import parse_blocked_by  # noqa: E402

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


check(
    "bold Blocked-by with trailing parenthetical",
    parse_blocked_by("**Blocked-by:** LwZZdzeE (Post 1 must publish first)") == ["LwZZdzeE"],
)
check(
    "bold Blocked-by with a trello.com URL",
    parse_blocked_by("**Blocked-by:** https://trello.com/c/LwZZdzeE") == ["LwZZdzeE"],
)
check(
    "plain (non-bold) Blocked-by still matches",
    parse_blocked_by("Blocked-by: zqOeAWEg") == ["zqOeAWEg"],
)
check(
    "multiple bare shortlinks on one line",
    parse_blocked_by("**Blocked-by:** LwZZdzeE, zqOeAWEg") == ["LwZZdzeE", "zqOeAWEg"],
)
check(
    "an 8-letter English word is not mistaken for a shortlink",
    parse_blocked_by("some line with finalize in it, not a blocker") == [],
)
check(
    "no Blocked-by line returns empty",
    parse_blocked_by("**What:** just a normal description") == [],
)

if failures:
    print(f"Verification FAILED - {len(failures)} errors")
    sys.exit(1)
print("Verification passed ✅")
