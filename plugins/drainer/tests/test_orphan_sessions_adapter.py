"""Test for providers/orphan-sessions-adapter.py's resolver + find-orphans.py integration.

Runs against the real ~/.claude/session-mgr/live-sessions.json using a unique throwaway
session id (seeded and removed within the test). Run directly:
    python plugins/drainer/tests/test_orphan_sessions_adapter.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
ADAPTER = os.path.join(PLUGIN_ROOT, "skills", "drainer", "providers", "orphan-sessions-adapter.py")
REGISTRY_HOOK = os.path.abspath(os.path.join(
    PLUGIN_ROOT, "..", "session-mgr", "hooks", "session_registry.py"))
REGISTRY_PATH = os.path.expanduser("~/.claude/session-mgr/live-sessions.json")

spec = importlib.util.spec_from_file_location("orphan_sessions_adapter", ADAPTER)
adapter_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_mod)

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}: {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def fire_event(event, session_id, cwd="C:/fake/repo"):
    payload = json.dumps({"hook_event_name": event, "session_id": session_id, "cwd": cwd})
    subprocess.run([sys.executable, REGISTRY_HOOK], input=payload, text=True, check=True)


def registry_has(session_id):
    if not os.path.exists(REGISTRY_PATH):
        return False
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return session_id in json.load(f)


def test_resolver_finds_find_orphans():
    print("test: adapter resolver locates a real find-orphans.py")
    script = adapter_mod._find_orphans_script()
    check("path exists", os.path.isfile(script), script)
    check("correct filename", os.path.basename(script) == "find-orphans.py", script)


def test_enumerate_returns_seeded_orphan():
    print("test: enumerate() surfaces a seeded registry entry via the real find-orphans.py")
    session_id = f"test-adapter-{uuid.uuid4()}"
    fire_event("SessionStart", session_id, cwd="C:/fake/repo/adapter-test")
    # The hook records the nearest claude.exe ancestor's pid - the live session running this test,
    # when there is one - which would make the seeded entry look alive. Clear it so the entry reads
    # as a session whose process is gone.
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    registry[session_id]["pid"] = None
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    try:
        provider = adapter_mod.Provider()
        items = provider.enumerate(50)
        ids = {it["session_id"] for it in items}
        check("seeded orphan present", session_id in ids, f"got {len(items)} items")
        match = next((it for it in items if it["session_id"] == session_id), None)
        check("cwd carried through", bool(match) and match["cwd"] == "C:/fake/repo/adapter-test")
        check("stable_id is deterministic",
              bool(match) and provider.stable_id(match) == provider.stable_id(match))
        check("stable_id has orphan- prefix",
              bool(match) and provider.stable_id(match).startswith("orphan-"))
    finally:
        if registry_has(session_id):
            fire_event("SessionEnd", session_id)


def test_capture_writes_json():
    print("test: capture() writes items/<id>.json with the documented shape")
    provider = adapter_mod.Provider()
    item = {"session_id": "abc-123", "cwd": "C:/fake/repo", "started_at": "2026-07-20T00:00:00",
            "_bucket": "needs-you", "_kind": "resume"}
    iid = provider.stable_id(item)
    with tempfile.TemporaryDirectory() as runtime_dir:
        json_file = provider.capture(item, iid, runtime_dir)
        check("file created", os.path.isfile(json_file))
        with open(json_file, encoding="utf-8") as f:
            record = json.load(f)
        check("source field", record.get("source") == "orphan-sessions")
        check("session_id carried", record.get("session_id") == "abc-123")
        check("triage needs-you", record.get("triage") == "needs-you")


if __name__ == "__main__":
    test_resolver_finds_find_orphans()
    test_enumerate_returns_seeded_orphan()
    test_capture_writes_json()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all tests passed")
