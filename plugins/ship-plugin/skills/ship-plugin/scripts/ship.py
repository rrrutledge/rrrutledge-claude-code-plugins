#!/usr/bin/env python
"""Merge a rrrutledge-claude-code-plugins PR and roll it out to this machine's Claude Code plugin cache.

This is the whole ship-plugin chain; its SKILL.md covers the two calls a script can't make for itself.
Every step checks the PR's current state first, so rerunning after any stop resumes where it left off.

  1. Resolve the PR (`--pr`, else the open PR for the current branch) and print its title and the
     plugins it touches.
  2. If the session sits in a worktree on the PR's branch, require it clean and pushed, since the
     worktree is discarded after the merge.
  3. Mark a draft ready, merge `main` into the branch on GitHub if it's behind, and wait for checks.
  4. Squash-merge (falling back to `--auto` when branch protection holds it), wait for the merge, and
     delete the remote branch.
  5. From inside a worktree, stop with exit code 3 and an `EXIT_WORKTREE` line: the pull writes to the
     main checkout, which a worktree-isolated session leaves to be rerun from there. The line says
     `remove` when the worktree is on the merged branch and `keep` when it's on any other branch.
  6. Remove any other clean worktree on the merged branch and the local branch itself.
  7. Wait for the version-bump run on the merge commit, then fast-forward `main` in the main checkout.
  8. Refresh the marketplace and `claude plugin update` every installed plugin at its recorded scope,
     then run the plugin-specific post-steps (Node deps, browser-chauffeur shim, drainer launcher).
  9. Print each touched plugin's new version and how the change goes live.

Usage:
  python ship.py [--pr <n>] [--repo <path>] [--admin]   the whole chain
  python ship.py --sync-only                             step 8 alone

`--admin` passes through to `gh pr merge` to override branch protection.
The repo is `--repo` if given, else the main checkout of the git repo holding the current directory,
provided its origin is this repo.

Exit codes: 0 shipped, 1 a line starting `ERR` needs attention, 3 `EXIT_WORKTREE` (see step 5).
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
CHECKS_TIMEOUT = 30 * 60
MERGE_TIMEOUT = 30 * 60
BUMP_TIMEOUT = 10 * 60
EXIT_WORKTREE = 3
PR_FIELDS = 'number,title,state,isDraft,headRefName,headRefOid,baseRefName,mergeCommit,files'


class ShipError(Exception):
    """A stop that needs attention; its message is printed after `ERR: `."""


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def last_line(r):
    lines = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
    return lines[-1] if lines else 'error'


def git(*args, cwd=None):
    """stdout of a git command, or None when it fails."""
    r = run(['git', *args], cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def gh_json(*args):
    r = run(['gh', *args, '--repo', GH_REPO])
    if r.returncode != 0:
        raise ShipError(f'gh {" ".join(args[:2])}: {last_line(r)}')
    return json.loads(r.stdout or 'null')


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


def wait_for(probe, timeout, what):
    """Poll `probe()` until it returns a non-None value or `timeout` seconds pass. Returns the value."""
    deadline = time.time() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if time.time() >= deadline:
            raise ShipError(f'timed out after {timeout // 60} min waiting for {what}')
        time.sleep(POLL_SECONDS)


# --- where the session is ---------------------------------------------------------------------------

def origin_is_this_repo(url):
    url = url.strip().lower()
    if url.endswith('.git'):
        url = url[:-4]
    return url.endswith('/' + GH_REPO) or url.endswith(':' + GH_REPO)


def worktree_entries(porcelain):
    """[{'path', 'branch'}] from `git worktree list --porcelain`; the main checkout is always first."""
    entries = []
    for line in porcelain.splitlines():
        if line.startswith('worktree '):
            entries.append({'path': line[len('worktree '):].strip(), 'branch': None})
        elif line.startswith('branch refs/heads/') and entries:
            entries[-1]['branch'] = line[len('branch refs/heads/'):].strip()
    return entries


def resolve_repo(explicit):
    if explicit:
        return Path(explicit)
    entries = worktree_entries(git('worktree', 'list', '--porcelain') or '')
    if not entries:
        return None
    path = entries[0]['path']
    origin = git('-C', path, 'remote', 'get-url', 'origin')
    return Path(path) if origin and origin_is_this_repo(origin) else None


def linked_worktree_branch():
    """The current branch when the working directory is a linked worktree (not the main checkout), else
    None."""
    git_dir = git('rev-parse', '--path-format=absolute', '--git-dir')
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir')
    if not git_dir or not common or Path(git_dir) == Path(common):
        return None
    return git('rev-parse', '--abbrev-ref', 'HEAD')


def require_pushed(pr):
    """The worktree on the PR's branch is discarded after the merge, so everything in it must already be
    on the PR."""
    if git('status', '--porcelain'):
        raise ShipError('this worktree has uncommitted changes - commit and push them, then rerun')
    if git('rev-parse', 'HEAD') != pr['headRefOid']:
        raise ShipError("this worktree's HEAD differs from the PR's head - push (or pull) it, then rerun")


# --- the PR ------------------------------------------------------------------------------------------

def resolve_pr(number):
    if number is None:
        r = run(['gh', 'pr', 'view', '--json', 'number'])
        if r.returncode != 0:
            raise ShipError('no --pr given and no open PR for the current branch')
        number = json.loads(r.stdout)['number']
    return gh_json('pr', 'view', str(number), '--json', PR_FIELDS)


def plugins_touched(files):
    return sorted({f.split('/')[1] for f in files if f.startswith('plugins/') and f.count('/') >= 2})


def behind_by(pr):
    r = run(['gh', 'api', f'repos/{GH_REPO}/compare/{pr["baseRefName"]}...{pr["headRefName"]}'])
    if r.returncode != 0:
        raise ShipError(f'comparing the branch with {pr["baseRefName"]}: {last_line(r)}')
    return json.loads(r.stdout).get('behind_by', 0)


def checks_settled(pr):
    """Probe: None while any check is pending; [] when all passed or there are none; else the names of
    failed checks."""
    r = run(['gh', 'pr', 'checks', str(pr['number']), '--repo', GH_REPO, '--json', 'name,bucket'])
    if 'no checks reported' in (r.stderr or '') + (r.stdout or ''):
        return []
    try:
        checks = json.loads(r.stdout or '[]')
    except ValueError:
        return None
    if any(c.get('bucket') == 'pending' for c in checks):
        return None
    return [c['name'] for c in checks if c.get('bucket') in ('fail', 'cancel')]


def make_mergeable(pr):
    n = str(pr['number'])
    if pr.get('isDraft'):
        r = run(['gh', 'pr', 'ready', n, '--repo', GH_REPO])
        if r.returncode != 0:
            raise ShipError(f'marking ready: {last_line(r)}')
        print('ready: marked the draft ready for review')
    behind = behind_by(pr)
    if behind:
        r = run(['gh', 'pr', 'update-branch', n, '--repo', GH_REPO])
        if r.returncode != 0:
            raise ShipError(f'merging {pr["baseRefName"]} into the branch failed ({last_line(r)}) - '
                            'resolve the conflicts in the branch, push, and rerun')
        print(f'updated: merged {behind} new {pr["baseRefName"]} commit(s) into the branch')
        time.sleep(POLL_SECONDS)
    failed = wait_for(lambda: checks_settled(pr), CHECKS_TIMEOUT, 'checks')
    if failed:
        raise ShipError(f'checks failed: {", ".join(failed)}')
    print('checks: green')


def merge(pr, admin):
    base = ['gh', 'pr', 'merge', str(pr['number']), '--repo', GH_REPO, '--squash']
    r = run(base + (['--admin'] if admin else []))
    if r.returncode == 0:
        print('merge: squash-merged')
        return
    first = last_line(r)
    r = run(base + ['--auto'])
    if r.returncode != 0:
        raise ShipError(f'merge refused: {first}')
    print('merge: queued with --auto until branch protection allows it')


def merged_pr(number):
    """Probe: the PR once merged; raises if it was closed unmerged."""
    data = gh_json('pr', 'view', str(number), '--json', PR_FIELDS)
    if data.get('state') == 'CLOSED':
        raise ShipError(f'PR #{number} was closed without merging')
    return data if data.get('state') == 'MERGED' and data.get('mergeCommit') else None


def delete_remote_branch(branch):
    run(['gh', 'api', '-X', 'DELETE', f'repos/{GH_REPO}/git/refs/heads/{branch}'])


def clean_up_local(repo, branch):
    """Remove each clean worktree still on the merged branch, then the local branch. A worktree with
    changes in it stays, and is reported."""
    for entry in worktree_entries(git('worktree', 'list', '--porcelain', cwd=str(repo)) or '')[1:]:
        if entry['branch'] != branch:
            continue
        if git('-C', entry['path'], 'status', '--porcelain'):
            print(f'kept: worktree {entry["path"]} has uncommitted changes')
        elif git('worktree', 'remove', entry['path'], cwd=str(repo)) is not None:
            print(f'removed: worktree {entry["path"]}')
    if git('rev-parse', '--verify', '--quiet', f'refs/heads/{branch}', cwd=str(repo)):
        git('branch', '-D', branch, cwd=str(repo))


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


def pull_main(repo):
    for cmd in (['fetch', 'origin'], ['checkout', 'main'], ['pull', '--ff-only', 'origin', 'main']):
        r = run(['git', *cmd], cwd=str(repo))
        if r.returncode != 0:
            raise ShipError(f'pulling main in {repo}: git {" ".join(cmd)}: {last_line(r)}')
    print(f'pulled: main in {repo}')


def plugin_version(repo, name):
    try:
        return json.loads((repo / 'plugins' / name / '.claude-plugin' / 'plugin.json')
                          .read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
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


def print_sync(rows):
    for name, scope, status in rows:
        print(f'{status}: {name} [{scope}]')
    if not rows:
        print(f'no plugins installed from {MARKETPLACE}')


# --- entry point ------------------------------------------------------------------------------------

def ship(number, repo_arg, admin):
    repo = resolve_repo(repo_arg)
    if not repo:
        raise ShipError(f'run from a checkout of {GH_REPO}, or pass --repo <path>')
    pr = resolve_pr(number)
    touched = plugins_touched([f['path'] for f in pr.get('files', [])])
    print(f'PR #{pr["number"]}: {pr["title"]}')
    print(f'plugins: {", ".join(touched) or "none"}')
    in_worktree = linked_worktree_branch()

    if pr['state'] == 'OPEN':
        if in_worktree == pr['headRefName']:
            require_pushed(pr)
        make_mergeable(pr)
        merge(pr, admin)
    elif pr['state'] == 'CLOSED':
        raise ShipError(f'PR #{pr["number"]} was closed without merging')
    pr = wait_for(lambda: merged_pr(pr['number']), MERGE_TIMEOUT, 'the merge')
    print(f'merged: as {pr["mergeCommit"]["oid"][:7]}')
    delete_remote_branch(pr['headRefName'])

    if in_worktree:
        action = 'remove' if in_worktree == pr['headRefName'] else 'keep'
        print(f'EXIT_WORKTREE {action}')
        print(f'then rerun: python "{os.path.abspath(__file__)}" --pr {pr["number"]}')
        return EXIT_WORKTREE

    clean_up_local(repo, pr['headRefName'])
    conclusion = wait_for(lambda: bump_run(pr['mergeCommit']['oid']), BUMP_TIMEOUT, 'the version-bump run')
    if conclusion != 'success':
        print(f'ERR: version-bump run concluded {conclusion} - the version may not have moved')
    pull_main(repo)
    rows = sync()
    print_sync(rows)
    for name in touched:
        print(f'version: {name} {plugin_version(repo, name) or "unknown"}')
    print('live: after a restart of Claude Code or /reload-plugins')
    for name, scope, status in rows:
        if name in touched and scope in ('local', 'project') and status == 'ok':
            print(f'note: {name} is {scope}-scoped, so it takes effect only in that project')
    return 1 if conclusion != 'success' or any(s.startswith('ERR') for _, _, s in rows) else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--pr', type=int, help='PR number (default: the open PR for the current branch)')
    p.add_argument('--repo', help='path to a checkout of the plugins repo (default: from cwd)')
    p.add_argument('--admin', action='store_true', help='override branch protection when merging')
    p.add_argument('--sync-only', action='store_true', help='only refresh the plugin cache')
    args = p.parse_args(argv)
    if args.sync_only:
        print_sync(sync())
        return 0
    try:
        return ship(args.pr, args.repo, args.admin)
    except ShipError as e:
        print(f'ERR: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
