#!/usr/bin/env python
"""Post-merge automation: patch-bump every plugin whose code changed in this push.

The failure this prevents: a behavior change lands under plugins/<name>/ while
plugins/<name>/.claude-plugin/plugin.json keeps the same version, so
`claude plugin update` (which keys off the version number) never re-pulls the
new source and the fix silently never ships. Previously a CI guardrail (see git
history for check_version_bump.py) required every PR to bump the version by
hand - in practice that step was easy to forget and needed follow-up "bump to
X" PRs more than once. This script removes the manual step: it runs after a
push lands on main and bumps the patch version for any plugin that changed but
didn't already get a version bump in the PR.

For skill plugins the behavior IS markdown - skills/**/SKILL.md and
commands/*.md are the payload, not documentation. So a blanket "*.md is docs"
exemption would skip bumping exactly the changes that most need to ship.
The exemption is therefore narrow: only genuine meta-docs (README, CHANGELOG,
CONTRIBUTING, LICENSE) and anything under a docs/ directory are bump-exempt.

A plugin that already got a manual version change in the push (e.g. a
deliberate minor/major bump made in the PR) is left alone - only plugins whose
version is unchanged between before/after get an automatic patch bump.

Run after merge, as a push-to-main workflow step. Compares the pushed commits
against the pre-push state ($BEFORE_SHA, from the push event's `before` field).
Writes updated plugin.json files in place; the workflow commits and pushes them.
"""
import json
import os
import re
import subprocess
import sys

ZERO_SHA = '0' * 40


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout


def file_at(ref, path):
    """Return file contents at a git ref, or None if it doesn't exist there."""
    try:
        return subprocess.run(['git', 'show', f'{ref}:{path}'],
                              capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        return None


# Genuine meta-docs that don't change plugin behavior - safe to edit without a
# version bump. Everything else under a plugin (SKILL.md, commands/*.md, scripts,
# hooks, plugin.json) is treated as behavior and requires a bump.
DOC_BASENAMES = {'readme.md', 'changelog.md', 'contributing.md', 'license', 'license.md'}


def is_doc_only(path):
    parts = path.lower().split('/')
    if 'docs' in parts:
        return True
    return parts[-1] in DOC_BASENAMES


def version_of(ref, plugin):
    raw = file_at(ref, f'plugins/{plugin}/.claude-plugin/plugin.json')
    if raw is None:
        return None
    try:
        return json.loads(raw).get('version')
    except json.JSONDecodeError:
        return None


def bump_patch(version):
    parts = version.split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f'Not a plain X.Y.Z version, cannot auto-bump: {version!r}')
    major, minor, patch = parts
    return f'{major}.{minor}.{int(patch) + 1}'


def resolve_before(before):
    if before and before != ZERO_SHA:
        try:
            git('cat-file', '-e', before)
            return before
        except subprocess.CalledProcessError:
            pass
    # New branch, force-push, or an unreachable `before` - fall back to the
    # immediate parent of HEAD so a single bad event doesn't skip bumps entirely.
    return git('rev-parse', 'HEAD~1').strip()


def main():
    before = resolve_before(os.environ.get('BEFORE_SHA', ''))
    changed = [f for f in git('diff', '--name-only', f'{before}..HEAD').splitlines() if f]

    touched = {}
    for f in changed:
        parts = f.split('/')
        if len(parts) < 2 or parts[0] != 'plugins':
            continue
        plugin = parts[1]
        touched[plugin] = touched.get(plugin, False) or not is_doc_only(f)

    bumped = []
    for plugin, has_code_change in sorted(touched.items()):
        if not has_code_change:
            continue
        before_version = version_of(before, plugin)
        if before_version is None:
            continue  # new plugin - nothing to bump against
        head_version = version_of('HEAD', plugin)
        if head_version != before_version:
            print(f'{plugin}: already bumped {before_version} -> {head_version} in this push, leaving as-is')
            continue

        new_version = bump_patch(head_version)
        manifest_path = f'plugins/{plugin}/.claude-plugin/plugin.json'
        raw = open(manifest_path, encoding='utf-8').read()
        updated, count = re.subn(
            r'("version"\s*:\s*")' + re.escape(head_version) + r'(")',
            r'\g<1>' + new_version + r'\g<2>',
            raw, count=1,
        )
        if count != 1:
            raise RuntimeError(f'Could not find version string {head_version!r} to replace in {manifest_path}')
        with open(manifest_path, 'w', encoding='utf-8') as fh:
            fh.write(updated)
        print(f'{plugin}: auto-bumped {head_version} -> {new_version}')
        bumped.append(plugin)

    if bumped:
        with open(os.environ.get('GITHUB_OUTPUT', os.devnull), 'a', encoding='utf-8') as out:
            out.write(f'bumped={",".join(bumped)}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
