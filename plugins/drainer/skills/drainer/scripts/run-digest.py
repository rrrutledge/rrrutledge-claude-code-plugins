"""Drainer EOD digest launcher — launches ONE interactive digest session, once a day.

The fast-loop poller (`run-poller.py`) is headless and silent; the digest is the OPPOSITE — it must
be an interactive session Russell works in, because it empties the fyi/junk queue only AFTER he reviews
and approves. So this launcher is deliberately thin: it launches a single background Claude session
(reachable from claude.ai/code and the phone) seeded to follow `engine/digest-core.md`. All the judgment (summarize fyi, group junk
with source-stop proposals, and clearing on Russell's OK) happens inside that interactive session.

The daily Scheduled Task (see `install-digest-schedule.ps1`) runs this once a day.

Usage:
    python run-digest.py --repo C:/Users/russe/Dev/personal-ai-pod              # launch the digest session
    python run-digest.py --repo C:/Users/russe/Dev/personal-ai-pod --dry-run    # print the brief only
"""
import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
PROVIDERS_DIR = os.path.join(SKILL_DIR, "providers")
sys.path.insert(0, SCRIPT_DIR)
from drainer_config import read_config, provider_search_dirs, ensure_main_worktree  # noqa: E402  (shared reader + provider resolution + main-pinned config worktree)
from provider_base import (run_node, spawn_bg, prompt_seed, write_receipt, load_providers,  # noqa: E402
                           ProviderError, load_seen)  # shared subprocess + background-session launch + adapter loader + seen-state reader

SEEN_STATE = os.path.join(SCRIPT_DIR, "seen-state.js")
# Page-size ceiling for each provider's own list call when the backlog barometer counts pending items -
# the same generous bound the poller enumerates with, so the count reflects the whole current listing.
ENUMERATE_PAGE_SIZE = 500


def write_seed(runtime_dir, repo, cfg, backlog_block):
    """Write the digest session's prompt file: a pointer to digest-core.md plus the few runtime
    facts it can't infer (where the queue/state live, the providers dir), and the pre-computed
    backlog-depth barometer the launcher measured for this run (so the session presents real numbers
    rather than recomputing them)."""
    seeds = os.path.join(runtime_dir, "seeds")
    os.makedirs(seeds, exist_ok=True)
    prompt_file = os.path.join(seeds, "digest.prompt.txt")
    digest_core = os.path.join(SKILL_DIR, "engine", "digest-core.md")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(
            "You are the drainer EOD digest session. Read `~/.claude/CLAUDE.md`, then follow the "
            f"drainer digest procedure at\n`{digest_core}`.\n\n"
            "Runtime facts for this run:\n"
            f"- runtime_dir: `{runtime_dir}` (holds digest-queue.json, seen.json, and items/).\n"
            f"- repo: `{repo}`.\n"
            f"- seen-state helper: `{SEEN_STATE}` (run with node).\n"
            f"- providers dirs: `{PROVIDERS_DIR}` and `{os.path.join(cfg['local_dir'], 'providers')}` "
            "(machine-local providers) — each item's `source` names its `<source>-provider.md` in one of "
            "these (read it for CLEAR and JUNK-LEARNING).\n"
            f"- provider-health file: `{os.path.join(runtime_dir, 'provider-health.json')}` — read it FIRST "
            "(digest-core step 0) and surface any stuck provider; missing/empty means all healthy.\n"
            "- Backlog depth measured by the launcher for this run (digest-core step 1b - present it "
            f"verbatim as the read-only barometer, do not recompute):\n{backlog_block}\n\n"
            "Present the digest to Russell and clear NOTHING until he approves. Draft-only: never send "
            "or post. When the queue is emptied (or Russell defers) and you are done, stop.\n"
        )
    return prompt_file


def _fmt_local(ts):
    """An ISO-8601 UTC timestamp rendered in the machine's local zone, or the raw string on any error."""
    if not ts:
        return "never"
    try:
        return datetime.datetime.fromisoformat(ts).astimezone().strftime("%Y-%m-%d %I:%M %p %Z")
    except ValueError:
        return ts


def _load_queue(runtime_dir):
    """The digest queue as a list of `{id, source, item}` entries (empty on any read/parse failure)."""
    out = run_node([SEEN_STATE, "queue-list", runtime_dir]).stdout
    try:
        return json.loads(out or "[]")
    except ValueError:
        return []


def _age_days(iso):
    """Whole days from an ISO-8601 timestamp until now, or None when it can't be parsed."""
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - dt.astimezone(datetime.timezone.utc)).days


def compute_backlog(runtime_dir, cfg):
    """The read-only backlog-depth barometer: for each enabled provider, how many items are waiting and
    NOT yet started, the oldest such item's arrival time, and a grand total across sources.

    "Not yet started" is the source's live listing minus the ids the poller has already recorded in
    seen.json - the items it dispatched a worker for (needs-you / auto-handle) or queued for the digest
    (fyi / junk). The poller leaves an item it merely held (a correspondent still being worked, or the
    open-worker budget full) UNrecorded, so exactly the items with no worker yet launched are what remains.
    This is the poller's own `collect_new` measure read from persisted state, so it needs no live-process
    scan: an item Russell already has a worker on is recorded and drops out. Zero AI and no body capture -
    each provider's `pending_summary` reads its envelope-only listing. A provider that can't list this run
    is reported as unavailable rather than aborting the whole report.

    Returns `{"sources": [{name, count, capped, oldest_received, error}], "total": int, "capped": bool}`
    (a source's own listing is skipped entirely when it opts out via count_in_backlog)."""
    load_errors = {}
    providers = load_providers(
        cfg["providers"], provider_search_dirs(PROVIDERS_DIR, cfg["local_dir"]), cfg=cfg,
        on_error=lambda name, msg, kind: load_errors.__setitem__(name, (msg, kind)))
    sources, total, capped_total = [], 0, False
    for prov in providers:
        if not getattr(prov, "count_in_backlog", True):
            continue  # a standing-noise scanner (e.g. the Junk-folder net), not a work queue to drain
        seen_ids = set(load_seen(runtime_dir, prov.name))  # the ids the poller has already started/queued
        try:
            s = prov.pending_summary(exclude_ids=seen_ids, limit=ENUMERATE_PAGE_SIZE)
            sources.append({"name": prov.name, "count": s["count"], "capped": s.get("capped", False),
                            "oldest_received": s.get("oldest_received"), "error": None})
            total += s["count"]
            capped_total = capped_total or s.get("capped", False)
        except Exception as e:  # ProviderError or anything a listing raises - isolate this one source
            sources.append({"name": prov.name, "count": None, "capped": False, "oldest_received": None,
                            "error": str(e)})
    # A provider that failed to even load (a broken deploy or an expired credential at construction) still
    # belongs in the barometer as unavailable; a plain missing adapter is just not enabled here, so skip it.
    for name, (msg, kind) in load_errors.items():
        if kind != "missing":
            sources.append({"name": name, "count": None, "capped": False, "oldest_received": None,
                            "error": msg})
    return {"sources": sources, "total": total, "capped": capped_total}


def format_backlog(backlog):
    """Render the backlog barometer as a terse block: one line per source, a grand-total line, and the
    how-far-back barometer (the oldest still-waiting item across every source - the queue-depth proxy)."""
    lines = ["Backlog depth (items not yet started, read-only barometer):"]
    for s in backlog["sources"]:
        if s["error"]:
            lines.append(f"    {s['name']}: unavailable this run ({s['error'][:80]})")
            continue
        oldest = ""
        if s["oldest_received"]:
            oldest = f", oldest {_fmt_local(s['oldest_received'])}"
            age = _age_days(s["oldest_received"])
            if age is not None:
                oldest += f" ({age} days back)"
        count = f"{s['count']}+" if s.get("capped") else str(s["count"])
        lines.append(f"    {s['name']}: {count} pending{oldest}")
    counted = [s for s in backlog["sources"] if s["count"] is not None]
    total = f"{backlog['total']}+" if backlog.get("capped") else str(backlog["total"])
    lines.append(f"    Grand total: {total} pending across {len(counted)} source(s).")
    # The how-far-back barometer: the single oldest still-waiting item across every source, the proxy for
    # how deep the whole queue runs. Sources with no dated pending item (an undated Trello card, an empty
    # source) simply don't contribute a candidate.
    dated = [s for s in counted if s["oldest_received"]]
    if dated:
        oldest_src = min(dated, key=lambda s: s["oldest_received"])
        age = _age_days(oldest_src["oldest_received"])
        back = f", {age} days back" if age is not None else ""
        lines.append(f"    Oldest still waiting: {_fmt_local(oldest_src['oldest_received'])}{back} "
                     f"(on {oldest_src['name']}) - how far back the backlog reaches.")
    return "\n".join(lines)


def _print_heartbeat(hb):
    """Show the poller's own liveness (`_poller` heartbeat) so a run of empty cycles is legible: the
    poller stamps this every live cycle, so a stale `last_drained_ts` reads as 'not running' instead of
    a silent death."""
    if not hb:
        print("  Poller heartbeat: none recorded yet (no live cycle has run since this was added).")
        return
    print(f"  Poller heartbeat: last drained {_fmt_local(hb.get('last_drained_ts'))}.")


def print_brief(runtime_dir, cfg):
    """Deterministic preview (no AI, no session): provider health + queue counts + backlog depth. For the
    dry-run ramp."""
    q = _load_queue(runtime_dir)
    counts = {"fyi": 0, "junk": 0, "other": 0}
    for e in q:
        t = (e.get("item") or {}).get("triage")
        counts[t if t in ("fyi", "junk") else "other"] += 1
    print(f"DRY-RUN digest brief for {runtime_dir}")
    try:
        with open(os.path.join(runtime_dir, "provider-health.json"), encoding="utf-8") as f:
            health = json.load(f) or {}
    except (OSError, ValueError):
        health = {}
    stuck = {n: h for n, h in health.items()
             if not n.startswith("_") and (h or {}).get("consecutive_failures", 0) >= 2}
    if stuck:
        print(f"  Provider health: {len(stuck)} stuck (>=2 consecutive failures):")
        for n, h in stuck.items():
            print(f"    [{h.get('last_error_kind', '?'):6}] {n}: {h.get('consecutive_failures')} cycles "
                  f"failing since last OK {h.get('last_ok_ts')}\n        {h.get('last_error')}")
    else:
        print("  Provider health: all healthy (no provider with >=2 consecutive failures).")
    _print_heartbeat(health.get("_poller") or {})
    print(f"  Digest queue: {len(q)} item(s) -> {counts['fyi']} fyi, {counts['junk']} junk, "
          f"{counts['other']} other")
    for e in q:
        it = e.get("item") or {}
        print(f"    [{(it.get('triage') or '?'):4}] {e.get('id')}\n"
              f"        {it.get('from')} | {it.get('subject')}")
    print(format_backlog(compute_backlog(runtime_dir, cfg)))
    print("Nothing cleared (dry-run).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # Config is read from the drainer-owned worktree pinned to origin/main (so a feature branch left
    # checked out at the real repo root can't stale the digest's view); the digest session itself runs with
    # cwd = the real repo, keeping its machine-local settings. Runtime state stays at the real repo too.
    source_repo = os.path.abspath(args.repo)
    config_repo = ensure_main_worktree(source_repo)
    repo = source_repo
    cfg = read_config(config_repo, runtime_root=source_repo)
    runtime_dir = cfg["runtime_dir"]

    if args.dry_run:
        print_brief(runtime_dir, cfg)
        return

    try:
        backlog_block = format_backlog(compute_backlog(runtime_dir, cfg))
    except Exception as e:  # a backlog failure must never keep the digest session from launching
        backlog_block = f"Backlog depth: unavailable this run ({e})."
    prompt_file = write_seed(runtime_dir, repo, cfg, backlog_block)
    # The digest is a background session named "Drainer EOD digest", so it reads recognizably in the
    # /resume picker and the Claude app's session list on the phone. The same text leads the seed and is
    # kept in a sibling summary file, the way a worker's is.
    summary = "Drainer EOD digest"
    summary_file = os.path.join(os.path.dirname(prompt_file), "digest.summary.txt")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(summary)
    bg_id = spawn_bg(prompt_seed(prompt_file, summary), cfg["digest_model"], repo, summary)
    if not bg_id:
        print("run-digest: headless `claude --bg` launch returned no id; the digest did not start.")
        sys.exit(1)
    receipt = write_receipt(prompt_file, bg_id)
    print(f"Launched the digest as background session {receipt} (model {cfg['digest_model']}) "
          f"for {runtime_dir}.")


if __name__ == "__main__":
    main()
