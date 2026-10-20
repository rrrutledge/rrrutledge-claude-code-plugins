#!/usr/bin/env python
"""Roll a merged rrrutledge-claude-code-plugins PR out to this machine's Claude Code plugin cache.

This is the deterministic tail of the ship-plugin skill; its SKILL.md explains why each step is needed
and when to run this.

  1. wait until the PR is merged (covers `gh pr merge --auto`),
  2. wait for the version-bump workflow run on the merge commit to finish,
  3. check out main in the repo's main checkout and fast-forward it from origin,
  4. refresh the marketplace and `claude plugin update` every installed plugin at its recorded scope,
     then run the plugin-specific post-steps (Node deps, browser-chauffeur shim, drainer launcher),
  5. print the new version of each plugin the PR touched.

Usage:
  python ship.py --pr <n> [--repo <path>]   wait, pull, sync, report
  python ship.py --sync-only                 refresh the plugin cache, with no wait or pull

The repo is resolved at runtime: `--repo` if given, else the main checkout of the git repo holding the
current directory, provided its origin is this repo.

Every line that needs attention starts with `ERR`.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MARKETPLACE = 'rrrutledge-claude-code-plugins'
GH_REPO = 'rrrutledge/rrrutledge-claude-code-plugins'
BUMP_WORKFLOW = 'version-bump.yml'
HOME = Path.home()
PLUGINS_HOME = HOME / '.claude' / 'plugins'
INSTALLED = PLUGINS_HOME / 'installed_plugins.json'
CACHE = PLUGINS_HOME / 'cache' / MARKETPLACE
POLL_SECONDS = 10
MERGE_TIMEOUT = 30 * 60
BUMP_TIMEOUT = 10 * 60


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def last_line(r):
    lines = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
    return lines[-1] if lines else 'error'


def version_key(name):
    """Sort key for a dotted version string, numeric where it can be, so 1.10.1 beats 1.9.0."""
    return [(0, int(x), '') if x.isdigit() else (1, 0, x) for x in name.split('.')]


def newest_version_dir(base):
    versions = [p for p in base.glob('*') if p.is_dir()] if base.exists() else []
    return max(versions, key=lambda p: version_key(p.name)) if versions else None


def newest_cached_file(plugin, relative):
    """The copy of `relative` inside the newest cached version of `plugin` that has one, or None."""
    base = CACHE / plugin
    hits = [p for p in base.glob('*/' + relative)] if base.exists() else []
    return max(hits, key=lambda p: version_key(p.relative_to(base).parts[0])) if hits else None


# --- repo resolution and the wait-pull steps -------------------------------------------------------

def origin_is_this_repo(url):
    url = url.strip().lower()
    if url.endswith('.git'):
        url = url[:-4]
    return url.endswith('/' + GH_REPO) or url.endswith(':' + GH_REPO)


def main_worktree(porcelain):
    """The main checkout's path from `git worktree list --porcelain` output - always the first entry."""
    for line in porcelain.splitlines():
        if line.startswith('worktree '):
            return line[len('worktree '):].strip()
    return None


def resolve_repo(explicit):
    if explicit:
        return Path(explicit)
    r = run(['git', 'worktree', 'list', '--porcelain'])
    path = main_worktree(r.stdout) if r.returncode == 0 else None
    if not path:
        return None
    origin = run(['git', '-C', path, 'remote', 'get-url', 'origin'])
    if origin.returncode != 0 or not origin_is_this_repo(origin.stdout):
        return None
    return Path(path)


def wait_for(probe, timeout, what):
    """Poll `probe()` until it returns a non-None value or `timeout` seconds pass. Returns the value."""
    deadline = time.time() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if time.time() >= deadline:
            raise TimeoutError(f'timed out after {timeout // 60} min waiting for {what}')
        time.sleep(POLL_SECONDS)


def merged_pr(pr):
    """Probe: the PR's {sha, files} once it's merged; raises if it was closed unmerged."""
    r = run(['gh', 'pr', 'view', str(pr), '--repo', GH_REPO, '--json', 'state,mergeCommit,files'])
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout)
    if data.get('state') == 'CLOSED':
        raise RuntimeError(f'PR #{pr} was closed without merging')
    if data.get('state') != 'MERGED' or not data.get('mergeCommit'):
        return None
    return {'sha': data['mergeCommit']['oid'], 'files': [f['path'] for f in data.get('files', [])]}


def bump_run(sha):
    """Probe: the version-bump run's conclusion for push `sha` once it has completed."""
    r = run(['gh', 'run', 'list', '--repo', GH_REPO, '--workflow', BUMP_WORKFLOW, '--commit', sha,
             '--json', 'status,conclusion'])
    if r.returncode != 0:
        return None
    runs = json.loads(r.stdout or '[]')
    if not runs or any(x.get('status') != 'completed' for x in runs):
        return None
    return runs[0].get('conclusion') or 'unknown'


def plugins_touched(files):
    return sorted({f.split('/')[1] for f in files if f.startswith('plugins/') and f.count('/') >= 2})


def plugin_version(repo, name):
    try:
        return json.loads((repo / 'plugins' / name / '.claude-plugin' / 'plugin.json')
                          .read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
        return None


def pull_main(repo):
    """Check out main in the main checkout and fast-forward it. Returns an error string or None."""
    for cmd in (['git', 'fetch', 'origin'], ['git', 'checkout', 'main'],
                ['git', 'pull', '--ff-only', 'origin', 'main']):
        r = run(cmd, cwd=str(repo))
        if r.returncode != 0:
            return f'{" ".join(cmd)}: {last_line(r)}'
    return None


# --- the cache sync ---------------------------------------------------------------------------------

def installed_targets(data):
    """(qualified key, plugin name, scope) for every install record from this marketplace."""
    out = []
    for key, records in sorted(data.get('plugins', {}).items()):
        if not key.endswith('@' + MARKETPLACE):
            continue
        for scope in sorted({rec.get('scope', 'user') for rec in records}):
            out.append((key, key.split('@', 1)[0], scope))
    return out


def pkg_dirs_with_deps(root):
    """Every directory under `root` holding a package.json that declares dependencies, skipping any
    node_modules tree (a dependency's own manifest is never ours to install)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != 'node_modules']
        if 'package.json' not in filenames:
            continue
        try:
            data = json.loads((Path(dirpath) / 'package.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if data.get('dependencies'):
            out.append(Path(dirpath))
    return out


def npm_install(cwd):
    """`npm install` (production deps only) in `cwd`. On Windows npm is a .cmd shim, so it goes through the
    shell to resolve; cwd (which may contain spaces) is handed to the process, never spliced into the
    command string."""
    args = 'npm install --omit=dev --no-audit --no-fund'
    if os.name == 'nt':
        r = subprocess.run(args, cwd=str(cwd), shell=True, capture_output=True, text=True)
    else:
        r = subprocess.run(args.split(), cwd=str(cwd), capture_output=True, text=True)
    return r.returncode == 0, last_line(r)


def ensure_node_deps(name):
    """Install the Node dependencies of a freshly updated plugin, since the cache update lays down new
    source but never runs the skill's install step - without this, a bump that touched a dependency
    leaves the tool dying with `Cannot find module`.

    A package.json with a setup.js beside it bootstraps its own per-user store, so setup.js runs; one
    without resolves deps by Node's upward node_modules walk, so npm install runs there.
    Both are idempotent. Returns a short status, or None when the plugin declares no Node deps."""
    cache_dir = newest_version_dir(CACHE / name)
    if not cache_dir:
        return None
    statuses = []
    for pkg_dir in pkg_dirs_with_deps(cache_dir):
        setup = pkg_dir / 'setup.js'
        if setup.exists():
            r = run(['node', str(setup)])
            statuses.append('ok' if r.returncode == 0 else f'ERR (setup.js: {last_line(r)})')
        else:
            ok, tail = npm_install(pkg_dir)
            statuses.append('ok' if ok else f'ERR (npm install: {tail})')
    if not statuses:
        return None
    return 'ok' if all(s == 'ok' for s in statuses) else '; '.join(statuses)


def browser_chauffeur_reshim():
    """browser-chauffeur's scripts resolve helpers through a shim that hardcodes a version path, so re-run
    the newest cached setup.js to re-point it at the new version."""
    setup = newest_cached_file('browser-chauffeur', 'skills/browser-chauffeur/templates/setup.js')
    if not setup:
        return None
    r = run(['node', str(setup)])
    return 'ok' if r.returncode == 0 else 'ERR (re-run browser-chauffeur setup.js manually)'


def drainer_redeploy_launcher():
    """The drainer's scheduled task runs a version-independent copy of launch-drainer.py in
    ~/.claude/drainer/, so re-copy the newest cached launcher there. Skipped when the drainer isn't set
    up on this machine."""
    stable_dir = HOME / '.claude' / 'drainer'
    launcher = newest_cached_file('drainer', 'skills/drainer/scripts/launch-drainer.py')
    if not stable_dir.exists() or not launcher:
        return None
    try:
        shutil.copy2(launcher, stable_dir / 'launch-drainer.py')
        return 'ok'
    except OSError as e:
        return f'ERR ({e})'


POST_STEPS = {
    'browser-chauffeur': ('shim', browser_chauffeur_reshim),
    'drainer': ('launcher', drainer_redeploy_launcher),
}


def sync():
    """Refresh the marketplace and update every installed plugin from it at its recorded scope. Returns
    (plugin, scope-or-step, status) rows."""
    if not INSTALLED.exists():
        return [('-', '-', f'ERR: no installed_plugins.json at {INSTALLED}')]
    r = run(['claude', 'plugin', 'marketplace', 'update', MARKETPLACE])
    rows = [] if r.returncode == 0 else [('marketplace', '-', f'ERR: {last_line(r)}')]
    updated = set()
    for key, name, scope in installed_targets(json.loads(INSTALLED.read_text(encoding='utf-8'))):
        r = run(['claude', 'plugin', 'update', key, '--scope', scope])
        note = last_line(r)
        if r.returncode != 0 and 'not found' in note.lower():
            rows.append((name, scope, 'skipped (not in marketplace)'))
            continue
        if r.returncode == 0:
            updated.add(name)
        rows.append((name, scope, 'ok' if r.returncode == 0 else f'ERR: {note}'))
    for name in sorted(updated):
        deps = ensure_node_deps(name)
        if deps:
            rows.append((name, 'node-deps', deps))
        if name in POST_STEPS:
            label, step = POST_STEPS[name]
            status = step()
            if status:
                rows.append((name, label, status))
    return rows


# --- entry point ------------------------------------------------------------------------------------

def ship(pr, repo_arg):
    repo = resolve_repo(repo_arg)
    if not repo:
        print(f'ERR: run from a checkout of {GH_REPO}, or pass --repo <path>')
        return 1
    try:
        merged = wait_for(lambda: merged_pr(pr), MERGE_TIMEOUT, f'PR #{pr} to merge')
        print(f'merged: PR #{pr} as {merged["sha"][:7]}')
        conclusion = wait_for(lambda: bump_run(merged['sha']), BUMP_TIMEOUT, 'the version-bump run')
    except (TimeoutError, RuntimeError) as e:
        print(f'ERR: {e}')
        return 1
    if conclusion == 'success':
        print('version-bump: success')
    else:
        print(f'ERR: version-bump run concluded {conclusion} - the version may not have moved')
    err = pull_main(repo)
    if err:
        print(f'ERR: pulling main in {repo}: {err}')
        return 1
    print(f'pulled: main in {repo}')
    for name, scope, status in sync():
        print(f'{status}: {name} [{scope}]')
    for name in plugins_touched(merged['files']):
        print(f'version: {name} {plugin_version(repo, name) or "unknown"}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pr', type=int, help='merged (or auto-merging) PR number to roll out')
    mode.add_argument('--sync-only', action='store_true', help='only refresh the plugin cache')
    p.add_argument('--repo', help='path to a checkout of the plugins repo (default: from cwd)')
    args = p.parse_args(argv)
    if args.sync_only:
        rows = sync()
        for name, scope, status in rows:
            print(f'{status}: {name} [{scope}]')
        if not rows:
            print(f'no plugins installed from {MARKETPLACE}')
        return 0
    return ship(args.pr, args.repo)


if __name__ == '__main__':
    sys.exit(main())
