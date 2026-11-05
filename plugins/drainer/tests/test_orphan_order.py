"""Test for run-poller's order_needs: recent orphans dispatch ahead of every other source, older
orphans wait behind every other needs-you item, and each group runs most recently ended first.

Run directly:
    python plugins/drainer/tests/test_orphan_order.py
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.abspath(os.path.join(HERE, "..")), "skills", "drainer", "scripts")
sys.path.insert(0, SCRIPTS)  # run-poller imports its siblings by bare name
spec = importlib.util.spec_from_file_location("run_poller", os.path.join(SCRIPTS, "run-poller.py"))
poller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poller)

failures = []
NOW = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def days_ago(d):
    return (NOW - timedelta(days=d)).isoformat().replace("+00:00", "Z")


def orphan(name, received):
    return {"_id": name, "_source": "orphan-sessions", "received": received}


def mail(name, received):
    return {"_id": name, "_source": "gmail", "received": received}


items = [orphan("old-orphan", days_ago(20)), mail("mail-older", days_ago(3)),
         orphan("recent-orphan", days_ago(2)), orphan("newest-orphan", days_ago(0.1)),
         mail("mail-newer", days_ago(1)), orphan("older-orphan", days_ago(10)),
         orphan("undated-orphan", "")]

print("order_needs")
check("recent orphans, then other sources, then older orphans",
      [it["_id"] for it in poller.order_needs(items, now=NOW)],
      ["newest-orphan", "recent-orphan", "undated-orphan", "mail-newer", "mail-older",
       "older-orphan", "old-orphan"])
check("with no orphans, other sources keep their own order",
      [it["_id"] for it in poller.order_needs([mail("a", days_ago(5)), mail("b", days_ago(1))], now=NOW)],
      ["b", "a"])

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
