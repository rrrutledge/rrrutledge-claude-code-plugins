"""Regression test for the session names the poller hands to `claude --bg --name`: a worker's (built from
an email subject, Trello card name, or sender) and an orphan resume's (built from a directory name).
A name carries none of `& < > | % " ^ ;`, is cut to 50 characters, and falls back to an id when no text
is left.

Run directly:
    python plugins/drainer/tests/test_session_title.py
"""
import importlib.util
import json
import os
import re
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
UNSAFE = re.compile(r'[&<>|%"^;]')


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def worker_title(rec):
    with tempfile.TemporaryDirectory() as tmp:
        json_file = os.path.join(tmp, "item.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return poller._worker_title("item-x1", json_file)


def resume_call(cwd):
    """spawn_resume's one spawn_bg call, captured (never a real launch)."""
    calls = []
    real = poller.spawn_bg
    poller.spawn_bg = lambda seed, model, cwd, name, resume=None, scratch_only=False: calls.append(
        {"seed": seed, "model": model, "cwd": cwd, "name": name, "resume": resume}) or "r35u0000"
    try:
        poller.spawn_resume("abcd1234-0000", cwd, "C:/repo")
    finally:
        poller.spawn_bg = real
    return calls[0]


print("\n_worker_title removes a semicolon from the subject")
title = worker_title({"source": "gmail", "subject": "Q3 plan; revenue; costs", "from": "Ann <ann@x.com>"})
check("no semicolon", ";" in title, False)
check("subject words kept", title, "Gmail: Q3 plan revenue costs - Ann")

print("\n_worker_title removes a semicolon from the sender and from a Trello card name")
check("no semicolon in sender", ";" in worker_title({"source": "slack", "subject": "hi", "from": "a;b"}), False)
check("Trello card name", worker_title({"source": "trello", "name": "Fix; deploy"}), "Trello: Fix deploy")

print("\n_worker_title still removes the shell metacharacters and keeps the length cap")
title = worker_title({"source": "gmail", "subject": '50% off & <more> | "now" ^ ; end'})
check("no unsafe character", bool(UNSAFE.search(title)), False)
title = worker_title({"source": "gmail", "subject": "x;" * 60})
check("at most 50 characters", len(title) <= 50, True)
check("no semicolon when truncated", ";" in title, False)

print("\n_worker_title with a subject of only semicolons keeps just the label")
check("only semicolons in the subject", worker_title({"source": "gmail", "subject": ";;;"}), "Gmail:")

print("\nthe id stands in when no text is usable")
check("unreadable item", poller._worker_title("item-x1", "no-such-file.json"), "drain:item-x1")
check("only semicolons", poller._session_title(" ; ;; ", "drain:item-x1"), "drain:item-x1")

print("\nspawn_resume reopens the session in the background under a sanitized name")
call = resume_call("C:/Users/me/proj;evil")
check("resumes the given session", call["resume"], "abcd1234-0000")
check("no seed and no model override", (call["seed"], call["model"]), (None, None))
check("runs in the session's own cwd", call["cwd"], "C:/Users/me/proj;evil")
check("no semicolon in the name", ";" in call["name"], False)
check("directory words kept", call["name"], "Resume: proj evil")
check("directory of only semicolons", resume_call("C:/;;;")["name"], "Resume:")
check("no cwd falls back to the repo", resume_call(None)["cwd"], "C:/repo")

print("\nspawn_resume keeps the orphan's own name, read from its transcript")


def name_from(lines, sid="abcd1234-0000"):
    with tempfile.TemporaryDirectory() as home:
        proj = os.path.join(home, ".claude-bg", "projects", "C--proj")
        os.makedirs(proj)
        if lines is not None:
            with open(os.path.join(proj, f"{sid}.jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in lines))
        return poller._orphan_session_name(sid, home=home)


ct = lambda n: {"type": "custom-title", "customTitle": n, "sessionId": "s"}
at = lambda n: {"type": "ai-title", "aiTitle": n}
check("last custom-title wins", name_from([ct("first"), {"type": "user"}, ct("second"), at("ai")]), "second")
check("ai-title is the fallback", name_from([{"type": "user"}, at("Fix the build")]), "Fix the build")
check("no transcript gives None", name_from(None), None)
check("no title records gives None", name_from([{"type": "user"}, "not json"]), None)
check("Resume: prefix stripped", name_from([ct("Resume: personal-ai-pod")]), "personal-ai-pod")
check("only a Resume: prefix gives None", name_from([ct("Resume:")]), None)

real_name = poller._orphan_session_name
poller._orphan_session_name = lambda sid, home=None: "my session"
try:
    check("resumed under the original name", resume_call("C:/Users/me/proj")["name"], "my session")
finally:
    poller._orphan_session_name = real_name

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
