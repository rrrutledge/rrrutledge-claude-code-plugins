"""Test for search_around.py's session search: a loose term match (case, spacing, CamelCase), the
title and snippets of a hit, and the three things it skips - this session's own transcript, a session
about the same captured item, and a live-session listing that merely names the item's subject - and
its provider-records search: only an enabled provider that keeps records is searched.

Run directly:
    python plugins/drainer/tests/test_search_around.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "drainer", "scripts"))
import search_around  # noqa: E402

failures = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got:  {got!r}\n         want: {want!r}")
        failures.append(name)


def write_session(projects, sid, title, messages):
    d = projects / "C--repo"
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "custom-title", "customTitle": title, "sessionId": sid}]
    for role, text in messages:
        lines.append({"type": role, "message": {"role": role, "content": text}})
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")


check("CamelCase splits", search_around.term_words("AcmeCorp"), ["Acme", "Corp"])
check("hyphen splits", search_around.term_words("acme-corp"), ["acme", "corp"])
check("lowercase-led brand stays whole", search_around.term_words("eBay"), ["eBay"])
check("acronym run splits", search_around.term_words("HSABank"), ["HSA", "Bank"])
check("term matches whole words only",
      bool(search_around.compile_terms(["Dan"])[0][1].search("Jordan danced")), False)
check("empty terms dropped", [t for t, _ in search_around.compile_terms(["", "-", "Acme"])], ["Acme"])

with tempfile.TemporaryDirectory() as tmp:
    projects = Path(tmp)
    search_around.transcripts = lambda: projects.glob("*/*.jsonl")
    write_session(projects, "aaaa1111", "Find short-term coverage for the kids", [
        ("user", "Find the kids short-term coverage"),
        ("assistant", "Submitted the Acme Corp Basic policy for Jordan; the eSignature goes to Outlook."),
    ])
    write_session(projects, "eeee5555", "Background check", [
        ("user", "Start the background check"),
        ("assistant", "- Sent the AcmeCorp form (background check pending)"),
    ])
    write_session(projects, "bbbb2222", "Outlook: AcmeCorp has sent you an invite", [
        ("user", "Handle the item outlook-graph-20261124-mail-acmecorp"),
        ("assistant", "AcmeCorp invite for Jordan"),
    ])
    write_session(projects, "cccc3333", "Gmail: unrelated sponsor reply", [
        ("user", "Handle the sponsor email.\nLive sessions:\n- Outlook: AcmeCorp has sent you an invite to sig (background, blocked)\n"),
        ("user", "Peer sessions (3):\n  Outlook: AcmeCorp has sent you an invite to sig [2173ea]  ·  bg  ·  idle  ·  started 5m ago\n"),
    ])
    write_session(projects, "gggg7777", "Paused peer listing", [
        ("user", "Other work.\n- Outlook: AcmeCorp has sent you an invite (paused, resume id 1234)\n"
                 "You were handed off from `Outlook: AcmeCorp invite`; it is not a duplicate"),
    ])
    write_session(projects, "dddd4444", "this worker", [("user", "acmecorp")])
    os.environ["CLAUDE_CODE_SESSION_ID"] = "dddd4444"

    hits = search_around.search_sessions(search_around.compile_terms(["AcmeCorp", "Jordan"]),
                                         "outlook-graph-20261124-mail-acmecorp")
    check("the sign-up sessions match", [h["id"] for h in hits], ["aaaa1111", "eeee5555"])
    check("title carried", hits[0]["title"], "Find short-term coverage for the kids")
    check("both terms matched", sorted(hits[0]["matched"]), ["AcmeCorp", "Jordan"])
    check("snippet shows the spaced form", "Acme Corp Basic" in hits[0]["snippets"][0], True)

    hits = search_around.search_sessions(search_around.compile_terms(["AcmeCorp"]), None)
    check("without --item the same-item session shows", sorted(h["id"] for h in hits),
          ["aaaa1111", "bbbb2222", "eeee5555"])

    write_session(projects, "ffff6666", "Accent case", [("user", "x"), ("assistant", "met with ÉLAN today")])
    hits = search_around.search_sessions(search_around.compile_terms(["élan"]), None)
    check("non-ASCII term found across case", [h["id"] for h in hits], ["ffff6666"])

ADAPTER = '''from provider_base import ProviderBase, ProviderError
class Provider(ProviderBase):
    name = "{name}"
{body}
'''
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "drainer.local.md").write_text(
        "---\nproviders:\n  sheet: {}\n  plain: {}\n  broken: {}\n  gone: {}\nlocal_dir: local\n---\n", encoding="utf-8")
    adapters = repo / "local" / "providers"
    adapters.mkdir(parents=True)
    (adapters / "sheet-adapter.py").write_text(ADAPTER.format(name="sheet", body=(
        "    def search_records(self, terms):\n"
        "        return [{'title': 'Acme row', 'url': 'u', 'lines': list(terms)}]")), encoding="utf-8")
    (adapters / "plain-adapter.py").write_text(ADAPTER.format(name="plain", body="    pass"), encoding="utf-8")
    (adapters / "broken-adapter.py").write_text(ADAPTER.format(name="broken", body=(
        "    def __init__(self):\n        raise ProviderError('no token', kind='auth')")), encoding="utf-8")
    check("a record-keeping provider is searched; one that keeps none and a missing one are skipped; an unloaded one is named",
          search_around.search_records(str(repo), ["Acme", "jo@acme.com"]),
          ([("sheet", [{"title": "Acme row", "url": "u", "lines": ["Acme", "jo@acme.com"]}], None)],
           [("broken", "no token")]))

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nall passed")
