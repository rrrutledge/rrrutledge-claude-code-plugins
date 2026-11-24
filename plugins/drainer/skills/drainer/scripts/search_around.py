"""Search recent Claude sessions and every Trello board for an item's names, in one command.

A worker runs this from worker-core §2's "Search around before concluding".

Each `--term` is a name or identifier: a company, platform, person, order or document number.
A term matches whole words loosely: case is ignored, and spaces, underscores, hyphens, and CamelCase
joins are optional, so `AcmeCorp` also finds "Acme Corp" and "acme-corp", and "Dan" skips "Jordan".

Sessions: every top-level transcript under each non-backup ~/.claude*/projects (and
`CLAUDE_CONFIG_DIR`'s) modified in the last `DAYS`,
except this session's own (CLAUDE_CODE_SESSION_ID) and any session whose first prompt names
`--item`'s id (the drainer's own triage and worker runs for this same item).
Each hit prints the session's title, id, last-active time, the terms it matched, and one snippet per
matched term.

Trello: Trello's own card search over all of Russell's boards for each term (it matches names,
descriptions, and comments), printing the card, its link, and its last activity, plus up to 3
matching comments on each of the top `CARDS_WITH_COMMENTS` cards.
A failed Trello request prints as "(failed: ...)" and the other searches still run.

Sessions and cards rank by how many terms they matched, then by recency; the top `MAX_SHOWN` of each
print.

Provider records: every provider enabled in `--repo`'s drainer.local.md whose adapter keeps records of
its own (a tracking sheet; see `ProviderBase.search_records`) is searched for the same terms, and each
matching record prints in full, so an item that arrives on one source finds its record on another.

CLI:
    python search_around.py --term <name> [--term <name> ...] [--item <item.json>] [--repo <repo>]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from drainer_config import provider_search_dirs, read_config
from provider_base import ProviderBase, find_skill_script, load_providers

PROVIDERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "providers")
DAYS = 14
CARDS_WITH_COMMENTS = 5
MAX_SHOWN = 10
SNIPPET_RADIUS = 160
# Stripped before matching: a seed's duplicate-check list ("- <title> (background, blocked)",
# "- <title> (paused, resume id ...)", "You were handed off from `<title>`") and a ListAgents listing
# ("<title> [9147b8] · bg · idle · ...") name every live session's title, so the item's own subject
# would otherwise match every session launched while that item's worker was live.
SESSION_LIST_LINE = re.compile(
    r"(?m)^(?:\s*\d+\t)?(?:- [^\n]{1,120} \((?:background|interactive|paused|idle|busy|working|blocked|done)"
    r"(?:, [^)\n]{1,60})?\)|[^\n]{1,120} \[[0-9a-f]{6,8}\]\s+·[^\n]*|You were handed off from `[^\n]*)\s*$")


def transcripts():
    """Top-level transcripts under every Claude config dir's projects folder; a backup copy of a config
    dir holds old duplicates of the same sessions, so it is left out."""
    roots = {r for r in Path.home().glob(".claude*") if r.is_dir() and "backup" not in r.name}
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.add(Path(os.environ["CLAUDE_CONFIG_DIR"]))
    for root in roots:
        yield from (root / "projects").glob("*/*.jsonl")


def term_words(term):
    """A term's words, split on spaces, underscores, hyphens, and CamelCase joins:
    `AcmeCorp` -> ['Acme', 'Corp'], `acme-corp` -> ['acme', 'corp']."""
    spaced = re.sub(r"(?<=[a-z]{2})(?=[A-Z])|(?<=[0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", term)
    return [w for w in re.split(r"[\s_\-]+", spaced) if w]


def _strings(value):
    """Every string inside a tool call's input, so a term at a line start isn't glued to an escaped \\n."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


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
                parts.extend(_strings(block["input"]))
            elif "content" in block:
                parts.append(_text(block["content"]))
        return "\n".join(parts)
    return ""


def _window(text, m):
    return " ".join(text[max(0, m.start() - SNIPPET_RADIUS):m.end() + SNIPPET_RADIUS].split())


def scan_transcript(path, compiled, forms, exclude_id):
    """One transcript's hits, or None when it has none or is the item's own triage/worker run."""
    raw = path.read_bytes()
    low = raw.lower()

    def has_form(chunk):
        return forms is None or any(f in chunk for f in forms)

    if not has_form(low):
        return None
    titles, first_prompt, snippets = {}, None, {}
    for line, low_line in zip(raw.split(b"\n"), low.split(b"\n")):
        is_title = b'"custom-title"' in line or b'"ai-title"' in line
        if first_prompt is not None and not is_title and not has_form(low_line):
            continue
        try:
            rec = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if rec.get("type") in ("custom-title", "ai-title"):
            titles[rec["type"]] = rec.get("customTitle") or rec.get("aiTitle") or titles.get(rec["type"])
            continue
        if rec.get("type") not in ("user", "assistant"):
            continue
        text = SESSION_LIST_LINE.sub("", _text((rec.get("message") or {}).get("content")))
        if first_prompt is None and rec.get("type") == "user" and not rec.get("isMeta"):
            first_prompt = text
            if exclude_id and exclude_id in text:
                return None
        # A term's latest match wins its snippet: the work a session did comes after its opening prompt.
        for term, rx in compiled:
            m = rx.search(text)
            if m:
                snippets[term] = _window(text, m)
    if not snippets:
        return None
    return {"title": titles.get("custom-title") or titles.get("ai-title"), "matched": set(snippets),
            "snippets": list(dict.fromkeys(snippets.values()))}


def compile_terms(terms):
    """(term, pattern) for each term that has a word in it; a match starts and ends on a word edge."""
    return [(t, re.compile(r"(?<!\w)" + r"[\s_\-]*".join(map(re.escape, term_words(t))) + r"(?!\w)", re.I))
            for t in terms if term_words(t)]


def _local(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %I:%M %p %Z")


def search_sessions(compiled, exclude_id):
    # The prefilter, run on the lowercased file and then on each line after the first prompt (title
    # lines excepted) before it is parsed: a plain
    # substring test for each term's words joined by nothing, a space, a hyphen, or an underscore. A
    # case-insensitive regex over ~2 GB of transcripts takes minutes. bytes.lower() folds ASCII only,
    # so a non-ASCII term turns the prefilter off.
    forms = {sep.join(term_words(t)).lower().encode("utf-8") for t, _ in compiled for sep in ("", " ", "-", "_")}
    if not all(f.isascii() for f in forms):
        forms = None
    own = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    cutoff = time.time() - DAYS * 86400
    hits = []
    for path in transcripts():
        try:
            mtime = path.stat().st_mtime
            if mtime < cutoff or path.stem == own:
                continue
            hit = scan_transcript(path, compiled, forms, exclude_id)
        except OSError:
            continue
        if hit:
            hit.update(id=path.stem, mtime=mtime)
            hits.append(hit)
    hits.sort(key=lambda h: (len(h["matched"]), h["mtime"]), reverse=True)
    return hits


def search_trello(compiled):
    """(cards ranked like sessions, errors): one failed request is reported and the rest still run."""
    path = find_skill_script(__file__, "trello", os.path.join("scripts", "trello_utils.py"))
    if not path:
        raise RuntimeError("the trello skill's trello_utils.py was not found")
    sys.path.insert(0, os.path.dirname(path))
    import trello_utils
    session = trello_utils.get_trello_session()
    cards, errors = {}, []
    for term, _ in compiled:
        # Trello reads a leading #, @, or - as a search operator, and a colon as a field filter.
        words = [w for w in (re.sub(r"^[#@\-]+|:", "", w) for w in term_words(term)) if w]
        # Trello matches whole words, so a CamelCase name is searched both split and joined.
        for query in dict.fromkeys([" ".join(words), "".join(words)]):
            try:
                res = trello_utils.trello_request("GET", "/search", session, params={
                    "query": query, "partial": "true", "modelTypes": "cards",
                    "cards_limit": 20, "card_fields": "name,shortUrl,dateLastActivity,closed",
                })
            except Exception as e:
                errors.append(f"search {query!r}: {e}")
                continue
            for c in res.get("cards", []):
                cards.setdefault(c["id"], {"card": c, "matched": set(), "comments": []})["matched"].add(term)
    hits = sorted(cards.values(), key=lambda h: (len(h["matched"]), h["card"]["dateLastActivity"]),
                  reverse=True)
    # Trello's card search matches comment text but returns no comments, so the top cards'
    # comments are fetched and the matching ones kept.
    for h in hits[:CARDS_WITH_COMMENTS]:
        try:
            actions = trello_utils.trello_request("GET", f"/cards/{h['card']['id']}/actions", session,
                                                  params={"filter": "commentCard", "limit": 1000})
        except Exception as e:
            errors.append(f"comments of {h['card']['shortUrl']}: {e}")
            continue
        for a in actions:
            text = (a.get("data") or {}).get("text", "")
            m = next((m for _, rx in compiled for m in [rx.search(text)] if m), None)
            if m:
                h["comments"].append((a["date"], _window(text, m)))
    return hits, errors


def search_records(repo, terms):
    """(records, unloaded): `records` is [(provider name, hits, error)] for each provider enabled in
    `repo`'s drainer.local.md that keeps records (overrides `ProviderBase.search_records`), a failed
    search carrying its error; `unloaded` is [(provider name, error)] for each enabled provider whose
    adapter failed to load, so whether it keeps records is unknown."""
    cfg = read_config(repo)
    records, unloaded = [], []
    providers = load_providers(
        cfg["providers"], provider_search_dirs(PROVIDERS_DIR, cfg["local_dir"]), cfg=cfg,
        on_error=lambda name, message, kind: kind != "missing" and unloaded.append((name, message)))
    for prov in providers:
        if type(prov).search_records is ProviderBase.search_records:
            continue
        try:
            records.append((prov.name, prov.search_records(terms), None))
        except Exception as e:
            records.append((prov.name, [], str(e)))
    return records, unloaded


def main(argv=None):
    p = argparse.ArgumentParser(prog="search_around.py")
    p.add_argument("--term", action="append", required=True)
    p.add_argument("--item", help="the captured item json; sessions about this same item are skipped")
    p.add_argument("--repo", default=".",
                   help="the merged-main config repo, whose .claude/drainer.local.md names the providers")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    exclude_id = None
    if args.item:
        with open(args.item, encoding="utf-8") as f:
            exclude_id = json.load(f).get("id")

    compiled = compile_terms(args.term)
    if not compiled:
        p.error("every --term is empty")
    hits = search_sessions(compiled, exclude_id)
    shown = f", top {MAX_SHOWN} shown" if len(hits) > MAX_SHOWN else ""
    print(f"== Claude sessions, last {DAYS} days: {len(hits)} with a match{shown}")
    for h in hits[:MAX_SHOWN]:
        when = datetime.fromtimestamp(h["mtime"]).astimezone().strftime("%Y-%m-%d %I:%M %p %Z")
        print(f"- {h['title'] or '(untitled)'} [{h['id'][:8]}], last active {when}, "
              f"matched: {', '.join(sorted(h['matched']))}")
        for s in h["snippets"]:
            print(f"    ...{s}...")

    print()
    try:
        cards, errors = search_trello(compiled)
    except Exception as e:
        cards, errors = [], [str(e)]
    shown = f", top {MAX_SHOWN} shown" if len(cards) > MAX_SHOWN else ""
    print(f"== Trello cards: {len(cards)} with a match{shown}")
    for err in errors:
        print(f"   (failed: {err})")
    for h in cards[:MAX_SHOWN]:
        c = h["card"]
        state = " (archived)" if c.get("closed") else ""
        print(f"- {c['name']}{state} {c['shortUrl']}, last activity {_local(c['dateLastActivity'])}, "
              f"matched: {', '.join(sorted(h['matched']))}")
        for date, snippet in h["comments"][:3]:
            print(f"    {_local(date)}: ...{snippet}...")

    print()
    config = os.path.join(args.repo, ".claude", "drainer.local.md")
    try:
        records, unloaded = search_records(args.repo, [t for t, _ in compiled])
    except Exception as e:
        records, unloaded = [], []
        print(f"== Provider records: (failed: {e})")
    else:
        if not os.path.isfile(config):
            print(f"== Provider records: not searched, no drainer config at {config}")
        elif not records:
            print(f"== Provider records: no provider enabled in {config} keeps records")
    for name, hits, error in records:
        shown = f", top {MAX_SHOWN} shown" if len(hits) > MAX_SHOWN else ""
        print(f"== {name} records: {len(hits)} with a match{shown}")
        if error:
            print(f"   (failed: {error})")
        for h in hits[:MAX_SHOWN]:
            print(f"- {h.get('title', '(untitled)')} {h.get('url', '')}")
            for line in h.get("lines", []):
                print(f"    {line}")
    for name, error in unloaded:
        print(f"   (provider {name} failed to load, so its records, if any, were not searched: {error})")


if __name__ == "__main__":
    main()
