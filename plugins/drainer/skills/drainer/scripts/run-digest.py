"""Drainer EOD digest launcher — opens ONE interactive digest tab, once a day.

The fast-loop poller (`run-poller.py`) is headless and silent; the digest is the OPPOSITE — it must
be a visible, interactive session, because it empties the fyi/junk queue only AFTER Russell reviews and
approves. So this launcher is deliberately thin: it opens a single Windows Terminal tab running a fresh
Claude session seeded to follow `engine/digest-core.md`. All the judgment (summarize fyi, group junk
with source-stop proposals, and clearing on Russell's OK) happens inside that interactive session.

The daily Scheduled Task (see `install-digest-schedule.ps1`) runs this once a day.

Usage:
    python run-digest.py --repo C:/Users/russe/Dev/personal-ai-pod              # open the digest tab
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
from provider_base import run_node, spawn_tab, load_providers, ProviderError  # noqa: E402  (shared subprocess + tab-spawn + adapter loader)

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


def compute_backlog(runtime_dir, cfg, queue):
    """The read-only backlog-depth barometer: for each enabled provider, how many items are still
    pending (its live source listing minus the ids already parked in the digest queue) and the oldest
    pending item's arrival time, plus a grand total across sources. Zero AI and no body capture - each
    provider's `pending_summary` reads its envelope-only listing. A provider that can't list this run is
    reported as unavailable rather than aborting the whole report.

    Returns `{"sources": [{name, count, capped, oldest_received, is_email, error}], "total": int,
    "capped": bool}` (a source's own listing is skipped entirely when it opts out via count_in_backlog)."""
    parked = {}
    for e in queue:  # the ids already awaiting the digest, per source, so they don't inflate the count
        parked.setdefault(e.get("source"), set()).add(e.get("id"))
    load_errors = {}
    providers = load_providers(
        cfg["providers"], provider_search_dirs(PROVIDERS_DIR, cfg["local_dir"]), cfg=cfg,
        on_error=lambda name, msg, kind: load_errors.__setitem__(name, (msg, kind)))
    sources, total, capped_total = [], 0, False
    for prov in providers:
        if not getattr(prov, "count_in_backlog", True):
            continue  # a standing-noise scanner (e.g. the Junk-folder net), not a work queue to drain
        is_email = getattr(prov, "email_backlog", False)
        try:
            s = prov.pending_summary(exclude_ids=parked.get(prov.name, frozenset()),
                                     limit=ENUMERATE_PAGE_SIZE)
            sources.append({"name": prov.name, "count": s["count"], "capped": s.get("capped", False),
                            "oldest_received": s.get("oldest_received"), "is_email": is_email,
                            "error": None})
            total += s["count"]
            capped_total = capped_total or s.get("capped", False)
        except Exception as e:  # ProviderError or anything a listing raises - isolate this one source
            sources.append({"name": prov.name, "count": None, "capped": False, "oldest_received": None,
                            "is_email": is_email, "error": str(e)})
    # A provider that failed to even load (a broken deploy or an expired credential at construction) still
    # belongs in the barometer as unavailable; a plain missing adapter is just not enabled here, so skip it.
    for name, (msg, kind) in load_errors.items():
        if kind != "missing":
            sources.append({"name": name, "count": None, "capped": False, "oldest_received": None,
                            "is_email": False, "error": msg})
    return {"sources": sources, "total": total, "capped": capped_total}


def format_backlog(backlog):
    """Render the backlog barometer as a terse block: one line per source, a grand-total line, and the
    email barometer (how far back the oldest unhandled email goes - the queue-size proxy)."""
    lines = ["Backlog depth (pending needs-you items, read-only barometer):"]
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
    emails = [s for s in counted if s.get("is_email")]
    if emails:
        email_count = sum(s["count"] for s in emails)
        email_oldest = sorted(s["oldest_received"] for s in emails if s["oldest_received"])
        if email_oldest:
            age = _age_days(email_oldest[0])
            back = f", {age} days back" if age is not None else ""
            lines.append(f"    Email barometer: {email_count} pending, oldest "
                         f"{_fmt_local(email_oldest[0])}{back}.")
        else:
            lines.append(f"    Email barometer: {email_count} pending (none carries a date).")
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
    """Deterministic preview (no AI, no tab): provider health + queue counts + backlog depth. For the
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
    print(format_backlog(compute_backlog(runtime_dir, cfg, q)))
    print("Nothing cleared (dry-run).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # Config is read from the drainer-owned worktree pinned to origin/main (so a feature branch left
    # checked out at the real repo root can't stale the digest's view); the digest tab itself runs with
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
        backlog_block = format_backlog(compute_backlog(runtime_dir, cfg, _load_queue(runtime_dir)))
    except Exception as e:  # a backlog failure must never keep the digest tab from opening
        backlog_block = f"Backlog depth: unavailable this run ({e})."
    prompt_file = write_seed(runtime_dir, repo, cfg, backlog_block)
    # Name this session "Drainer EOD digest" so it reads recognizably in the tab title, the /resume
    # picker, and the Remote Control session list on the phone - the same one-line-summary path the
    # workers use: spawn-tab.cmd's 5th arg -> launch-session.ps1 -SummaryFile -> --name. Remote Control
    # auto-connects from the remoteControlAtStartup setting on its own; the summary only decides the
    # label the session carries, giving the digest a descriptive name in place of a random placeholder.
    summary_file = os.path.join(os.path.dirname(prompt_file), "digest.summary.txt")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("Drainer EOD digest")
    spawn_cmd = os.path.join(SCRIPT_DIR, "spawn-tab.cmd")
    spawn_tab([spawn_cmd, "drain:digest", repo, prompt_file, cfg["digest_model"], summary_file], cwd=repo)
    print(f"Opened digest tab (model {cfg['digest_model']}) for {runtime_dir}.")


if __name__ == "__main__":
    main()
