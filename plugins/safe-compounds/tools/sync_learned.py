"""Promote machine-local learned approvals into the plugin source via a PR.

Runs automatically at session end (wired in hooks/hooks.json) and on demand
(/safe-compounds:sync-learned). When the hook has learned new safe commands, it
records them in ~/.claude/safe-compounds-learned.json; this turns those into a
pull request so the learnings are shared instead of staying on one machine.

Design notes:
- It opens/updates the PR through the GitHub API (`gh api`), not local git, so it
  works from the installed plugin cache and never touches your working tree.
- It accumulates into one rolling branch/PR (BRANCH) - not one PR per session.
- It is best-effort: any failure (no `gh`, offline, not authed) is swallowed so
  it can never disrupt session end; the learnings stay local for next time.
- It is idempotent: each run rebuilds the branch as main + still-pending entries,
  and prunes entries that have already landed in main from the local store.
"""
import base64
import json
import os
import re
import subprocess

REPO = "rrrutledge/rrrutledge-claude-code-plugins"
BRANCH = "safe-compounds-learned"

# learned-store key -> (path in repo, set name in that file)
TARGETS = {
    "commands": ("plugins/safe-compounds/safe_compounds/trust.py", "TRUSTED_COMMANDS"),
    "NPM_TRUSTED_SUBCOMMANDS": ("plugins/safe-compounds/safe_compounds/commands.py", "NPM_TRUSTED_SUBCOMMANDS"),
    "YARN_TRUSTED_SUBCOMMANDS": ("plugins/safe-compounds/safe_compounds/commands.py", "YARN_TRUSTED_SUBCOMMANDS"),
    "PIP_TRUSTED_SUBCOMMANDS": ("plugins/safe-compounds/safe_compounds/commands.py", "PIP_TRUSTED_SUBCOMMANDS"),
    "PNPM_TRUSTED_SUBCOMMANDS": ("plugins/safe-compounds/safe_compounds/commands.py", "PNPM_TRUSTED_SUBCOMMANDS"),
    "BUN_TRUSTED_SUBCOMMANDS": ("plugins/safe-compounds/safe_compounds/commands.py", "BUN_TRUSTED_SUBCOMMANDS"),
    "GH_AI_TRUSTED_PAIRS": ("plugins/safe-compounds/safe_compounds/commands.py", "GH_AI_TRUSTED_PAIRS_BASE"),
}


def learned_path():
    return os.environ.get(
        "SAFE_COMPOUNDS_LEARNED_JSON",
        os.path.expanduser("~/.claude/safe-compounds-learned.json"),
    )


def load_learned():
    try:
        with open(learned_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def share_exclusions():
    """Names that must never be promoted to the public PR: the consumer's
    private/company `trusted_commands` plus an explicit `learned_sync_exclude`.
    Company-specific trust belongs in config, which is never shared."""
    path = os.environ.get(
        "SAFE_COMPOUNDS_CONFIG_JSON",
        os.path.expanduser("~/.claude/safe-compounds-config.json"),
    )
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return set(cfg.get("trusted_commands", [])) | set(cfg.get("learned_sync_exclude", []))


# --------------------------------------------------------------- formatting ---
def _format_grouped(items):
    groups = {}
    for c in sorted(items):
        groups.setdefault(c[0].lower(), []).append(c)
    lines = ["    " + ", ".join(f"'{c}'" for c in groups[k]) + "," for k in sorted(groups)]
    return "\n" + "\n".join(lines) + "\n"


def _format_wrapped(items, width=92):
    lines, cur, length = [], [], 4
    for item in (f"'{c}'" for c in sorted(items)):
        if length + len(item) + 2 > width and cur:
            lines.append("    " + ", ".join(cur) + ",")
            cur, length = [item], 4 + len(item)
        else:
            cur.append(item)
            length += len(item) + 2
    if cur:
        lines.append("    " + ", ".join(cur) + ",")
    return "\n" + "\n".join(lines) + "\n"


def insert_into_text(text, set_name, items):
    """Insert items into a `set_name = {...}` literal in `text`.

    Returns (new_text, added, already_present). `added` are items that were not
    already in the set; `already_present` are items that were.
    """
    m = re.search(rf"({re.escape(set_name)}\s*=\s*\{{)(.*?)(\}})", text, re.DOTALL)
    if not m:
        raise ValueError(f"set {set_name} not found")
    existing = set(re.findall(r"'([^']+)'", m.group(2)))
    added = [i for i in items if i not in existing]
    already = [i for i in items if i in existing]
    if not added:
        return text, added, already
    formatter = _format_grouped if set_name == "TRUSTED_COMMANDS" else _format_wrapped
    block = m.group(1) + formatter(existing | set(added)) + m.group(3)
    return text[:m.start()] + block + text[m.end():], added, already


# ------------------------------------------------------------------- gh api ---
def gh_json(*args):
    proc = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def gh_ok(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True)


# ---------------------------------------------------------------------- main --
def main():
    learned = load_learned()
    exclude = share_exclusions()
    plan = {}  # learned-key -> [values]
    if learned.get("commands"):
        vals = [v for v in learned["commands"] if v not in exclude]
        if vals:
            plan["commands"] = vals
    for key, vals in (learned.get("subcommands") or {}).items():
        if key in TARGETS:
            vals = [v for v in vals if v not in exclude]
            if vals:
                plan[key] = vals
    if not plan:
        return

    if subprocess.run(["gh", "--version"], capture_output=True).returncode != 0:
        return  # gh not available; leave learnings local

    main_sha = gh_json(f"repos/{REPO}/git/ref/heads/main")["object"]["sha"]

    # Reset the rolling branch to main so it always reads as main + pending.
    try:
        gh_json("-X", "PATCH", f"repos/{REPO}/git/refs/heads/{BRANCH}",
                "-f", f"sha={main_sha}", "-F", "force=true")
    except RuntimeError:
        gh_json("-X", "POST", f"repos/{REPO}/git/refs",
                "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={main_sha}")

    # Group plan entries by repo file path.
    by_path = {}
    for key, vals in plan.items():
        path, set_name = TARGETS[key]
        by_path.setdefault(path, []).append((key, set_name, vals))

    added_summary = {}
    pruned = set()  # learned-keys whose values are now fully in main (prunable)

    for path, edits in by_path.items():
        meta = gh_json(f"repos/{REPO}/contents/{path}?ref=main")
        text = base64.b64decode(meta["content"]).decode("utf-8")
        file_sha = meta["sha"]
        changed = False
        for key, set_name, vals in edits:
            text, added, already = insert_into_text(text, set_name, vals)
            if added:
                added_summary[set_name] = added
                changed = True
            # An entry already in main never needs promoting again.
            if already and not added:
                pruned.add(key)
        if changed:
            new_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
            gh_json("-X", "PUT", f"repos/{REPO}/contents/{path}",
                    "-f", "message=safe-compounds: promote learned commands",
                    "-f", f"content={new_b64}", "-f", f"branch={BRANCH}", "-f", f"sha={file_sha}")

    if added_summary:
        existing_pr = gh_json(f"repos/{REPO}/pulls?head=rrrutledge:{BRANCH}&state=open")
        if not existing_pr:
            body = "Auto-generated from learned safe commands.\n\n" + "\n".join(
                f"- {name}: {', '.join(vals)}" for name, vals in added_summary.items())
            gh_ok("pr", "create", "--repo", REPO, "--head", BRANCH, "--base", "main",
                  "--title", "safe-compounds: promote learned commands", "--body", body)
        summary = "; ".join(f"{n}: {', '.join(v)}" for n, v in added_summary.items())
        print(f"safe-compounds: opened/updated PR with -> {summary}")

    _prune_store(learned, pruned)


def _prune_store(learned, pruned_keys):
    """Drop entries that are already in main from the local store."""
    if not pruned_keys:
        return
    if "commands" in pruned_keys:
        learned["commands"] = []
    subs = learned.get("subcommands", {})
    for key in pruned_keys:
        if key in subs:
            subs[key] = []
    try:
        with open(learned_path(), "w", encoding="utf-8") as f:
            json.dump(learned, f, indent=2, sort_keys=True)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # best-effort: never disrupt session end
