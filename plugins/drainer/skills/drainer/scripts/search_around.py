"""Search recent Claude sessions and every Trello board for an item's names, in one command.

A worker runs this from worker-core §2's "Search around before concluding".

Each `--term` is a name or identifier: a company, platform, person, order or document number.
A term matches loosely: case is ignored, and spaces, hyphens, and CamelCase joins are optional, so
`AcmeCorp` also finds "Acme Corp" and "acme-corp".

Sessions: every top-level transcript under ~/.claude/projects modified in the last `--days`, except
this session's own (CLAUDE_CODE_SESSION_ID) and any session whose first prompt names `--item`'s id
(the drainer's own triage and worker runs for this same item).
Each hit prints the session's title, id, last-active time, the terms it matched, and a few snippets.

Trello: Trello's own search over all of Russell's boards (cards and comments) for each term, printing
the card, its link, its last activity, and the matching comment text.

CLI:
    python search_around.py --term <name> [--term <name> ...] [--item <item.json>] [--days 14]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from provider_base import find_skill_script  # noqa: E402

PROJECTS = Path.home() / ".claude" / "projects"
SNIPPETS_PER_SESSION = 3
SNIPPET_RADIUS = 160
# Stripped before matching: a seed's list of live sessions ("- <title> (background, blocked)") and a ListAgents listing
# ("<title> [9147b8] · bg · idle · ...") name every live session's title, so the item's own subject
# would otherwise match every session launched while that item's worker was live.
SESSION_LIST_LINE = re.compile(
    r"(?m)^(?:\s*\d+\t)?(?:- [^\n]{1,120} \((?:background|interactive)[^)\n]*\)"
    r"|[^\n]{1,120} \[[0-9a-f]{6,8}\]\s+·[^\n]*)\s*$")


def term_words(term):
    """`AcmeCorp` / `Acme Corp` / `acme-corp` -> ['Acme', 'Corp']."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", term)
    return [w for w in re.split(r"[\s_\-]+", spaced) if w]


def term_pattern(term):
    return r"[\s_\-]*".join(re.escape(w) for w in term_words(term))


def compile_terms(terms):
    return [(t, re.compile(term_pattern(t), re.I)) for t in terms]


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if "text" in block:
                parts.append(str(block["text"]))
            elif "input" in block:
                parts.append(json.dumps(block["input"], ensure_ascii=False))
            elif "content" in block:
                parts.append(_text(block["content"]))
        return "\n".join(parts)
    return ""


def _local(ts):
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %I:%M %p %Z")


def scan_transcript(path, compiled, first_words, exclude_id):
    """One transcript's hits, or None when it has none or is the item's own triage/worker run."""
    raw = path.read_bytes()
    if not any(w in raw.lower() for w in first_words):
        return None
    title, first_prompt, matched, snippets = None, None, set(), []
    for line in raw.split(b"\n"):
        is_title = b'"custom-title"' in line
        if first_prompt is not None and not is_title and not any(w in line.lower() for w in first_words):
            continue
        try:
            rec = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if rec.get("type") == "custom-title":
            title = rec.get("customTitle") or title
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        text = SESSION_LIST_LINE.sub("", _text((rec.get("message") or {}).get("content")))
        if first_prompt is None and rec.get("type") == "user":
            first_prompt = text
            if exclude_id and exclude_id in text:
                return None
        for term, rx in compiled:
            m = rx.search(text)
            if not m:
                continue
            matched.add(term)
            s = " ".join(text[max(0, m.start() - SNIPPET_RADIUS):m.end() + SNIPPET_RADIUS].split())
            if len(snippets) < SNIPPETS_PER_SESSION and s not in snippets:
                snippets.append(s)
    if not matched:
        return None
    return {"title": title, "matched": matched, "snippets": snippets}


def search_sessions(terms, days, exclude_id):
    compiled = compile_terms(terms)
    # A plain lowercase substring test on each term's first word is the fast prefilter; a
    # case-insensitive regex over ~1.6 GB of transcripts takes minutes.
    first_words = {term_words(t)[0].lower().encode("utf-8") for t in terms}
    own = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    cutoff = time.time() - days * 86400
    hits = []
    for path in PROJECTS.glob("*/*.jsonl"):
        mtime = path.stat().st_mtime
        if mtime < cutoff or path.stem == own:
            continue
        hit = scan_transcript(path, compiled, first_words, exclude_id)
        if hit:
            hit.update(id=path.stem, mtime=mtime)
            hits.append(hit)
    hits.sort(key=lambda h: (len(h["matched"]), h["mtime"]), reverse=True)
    return hits


def search_trello(terms):
    path = find_skill_script(__file__, "trello", os.path.join("scripts", "trello_utils.py"))
    if not path:
        raise RuntimeError("the trello skill's trello_utils.py was not found")
    sys.path.insert(0, os.path.dirname(path))
    import trello_utils
    session = trello_utils.get_trello_session()
    cards = {}
    for term in terms:
        res = trello_utils.trello_request("GET", "/search", session, params={
            "query": " ".join(term_words(term)), "partial": "true", "modelTypes": "cards,actions",
            "cards_limit": 20, "card_fields": "name,shortUrl,dateLastActivity,closed",
            "actions_limit": 50,
        })
        for c in res.get("cards", []):
            card = cards.setdefault(c["id"], {"card": c, "matched": set(), "comments": []})
            card["matched"].add(term)
        for a in res.get("actions", []):
            if a.get("type") != "commentCard":
                continue
            c = (a.get("data") or {}).get("card") or {}
            card = cards.setdefault(c.get("id"), {"card": c, "matched": set(), "comments": []})
            card["matched"].add(term)
            card["comments"].append((a.get("date", ""), a["data"].get("text", "")))
    out = sorted(cards.values(), key=lambda h: (len(h["matched"]), h["card"].get("dateLastActivity", "")),
                 reverse=True)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="search_around.py")
    p.add_argument("--term", action="append", required=True)
    p.add_argument("--item", help="the captured item json; sessions about this same item are skipped")
    p.add_argument("--days", type=float, default=14)
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    exclude_id = None
    if args.item:
        with open(args.item, encoding="utf-8") as f:
            exclude_id = json.load(f).get("id")

    hits = search_sessions(args.term, args.days, exclude_id)
    print(f"== Claude sessions, last {args.days:g} days: {len(hits)} with a match")
    for h in hits:
        print(f"- {h['title'] or '(untitled)'} [{h['id'][:8]}], last active {_local(h['mtime'])}, "
              f"matched: {', '.join(sorted(h['matched']))}")
        for s in h["snippets"]:
            print(f"    ...{s}...")

    print()
    try:
        cards = search_trello(args.term)
    except Exception as e:
        print(f"== Trello: search failed ({e})")
        return
    print(f"== Trello cards: {len(cards)} with a match")
    for h in cards:
        c = h["card"]
        state = " (archived)" if c.get("closed") else ""
        print(f"- {c.get('name')}{state} {c.get('shortUrl') or c.get('shortLink', '')}, "
              f"last activity {(c.get('dateLastActivity') or '')[:10]}, matched: {', '.join(sorted(h['matched']))}")
        for date, text in h["comments"][:SNIPPETS_PER_SESSION]:
            print(f"    {date[:10]}: {' '.join(text.split())[:2 * SNIPPET_RADIUS]}")


if __name__ == "__main__":
    main()
