"""Unit tests for skills/ship-plugin/scripts/ship.py.

Every gh, git, and claude call goes through ship.run, which the tests replace with a scripted fake, and
the plugin cache is a throwaway temp directory, so nothing touches the network, the repo, or the real
cache.
Run directly: python plugins/ship-plugin/tests/test_ship.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SHIP = os.path.join(HERE, '..', 'skills', 'ship-plugin', 'scripts', 'ship.py')

_spec = importlib.util.spec_from_file_location('ship', SHIP)
ship = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ship)


def done(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRun:
    """Answers each call with the first scripted (prefix, result) whose prefix starts the command."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, cmd, cwd=None):
        self.calls.append((cmd, cwd))
        for prefix, result in self.script:
            if cmd[:len(prefix)] == prefix:
                return result(cmd) if callable(result) else result
        return done()


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(ship, k) for k in ('run', 'CACHE', 'INSTALLED', 'HOME', 'POLL_SECONDS')}
        self.tmp = Path(tempfile.mkdtemp())
        ship.CACHE = self.tmp / 'cache'
        ship.INSTALLED = self.tmp / 'installed_plugins.json'
        ship.HOME = self.tmp / 'home'
        ship.POLL_SECONDS = 0

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(ship, k, v)

    def fake(self, script):
        ship.run = FakeRun(script)
        return ship.run


class VersionOrdering(Base):
    def test_numeric_components_beat_lexical(self):
        for v in ('1.9.0', '1.10.1', '1.2.3'):
            (ship.CACHE / 'p' / v).mkdir(parents=True)
        self.assertEqual(ship.newest_version_dir(ship.CACHE / 'p').name, '1.10.1')

    def test_non_numeric_component_does_not_crash(self):
        max(['1.0.0-beta', '1.0.1', 'abc'], key=ship.version_key)

    def test_missing_plugin_has_no_version(self):
        self.assertIsNone(ship.newest_version_dir(ship.CACHE / 'nothing'))

    def test_newest_cached_file_skips_versions_without_it(self):
        rel = 'skills/x/setup.js'
        for v in ('1.2.0', '1.10.0'):
            (ship.CACHE / 'p' / v).mkdir(parents=True)
        f = ship.CACHE / 'p' / '1.2.0' / rel
        f.parent.mkdir(parents=True)
        f.write_text('')
        self.assertEqual(ship.newest_cached_file('p', rel), f)


class RepoResolution(Base):
    def test_origin_forms(self):
        for url in ('https://github.com/rrrutledge/rrrutledge-claude-code-plugins.git\n',
                    'git@github.com:rrrutledge/rrrutledge-claude-code-plugins.git',
                    'https://github.com/RRRutledge/rrrutledge-claude-code-plugins'):
            self.assertTrue(ship.origin_is_this_repo(url), url)
        self.assertFalse(ship.origin_is_this_repo('https://github.com/someone/rrrutledge-claude-code-plugins-fork'))

    def test_main_worktree_is_first_entry(self):
        porcelain = 'worktree C:/repo\nHEAD abc\nbranch refs/heads/main\n\nworktree C:/repo/.claude/worktrees/x\n'
        self.assertEqual(ship.main_worktree(porcelain), 'C:/repo')

    def test_resolves_main_checkout_from_a_worktree(self):
        self.fake([(['git', 'worktree'], done('worktree /r\n\nworktree /r/.claude/worktrees/w\n')),
                   (['git', '-C', '/r', 'remote'], done('https://github.com/rrrutledge/rrrutledge-claude-code-plugins.git'))])
        self.assertEqual(ship.resolve_repo(None), Path('/r'))

    def test_other_repo_is_refused(self):
        self.fake([(['git', 'worktree'], done('worktree /r\n')),
                   (['git', '-C', '/r', 'remote'], done('https://github.com/someone/else.git'))])
        self.assertIsNone(ship.resolve_repo(None))

    def test_explicit_repo_wins(self):
        self.assertEqual(ship.resolve_repo('/x'), Path('/x'))


class MergeAndBumpWaits(Base):
    def test_waits_through_open_then_reports_merge(self):
        states = iter([{'state': 'OPEN', 'mergeCommit': None, 'files': []},
                       {'state': 'MERGED', 'mergeCommit': {'oid': 'abc123'},
                        'files': [{'path': 'plugins/drainer/x.md'}]}])
        self.fake([(['gh', 'pr', 'view'], lambda cmd: done(json.dumps(next(states))))])
        got = ship.wait_for(lambda: ship.merged_pr(5), 60, 'merge')
        self.assertEqual(got, {'sha': 'abc123', 'files': ['plugins/drainer/x.md']})

    def test_closed_unmerged_raises(self):
        self.fake([(['gh', 'pr', 'view'], done(json.dumps({'state': 'CLOSED', 'mergeCommit': None})))])
        with self.assertRaises(RuntimeError):
            ship.merged_pr(5)

    def test_bump_run_waits_for_run_to_appear_and_complete(self):
        answers = iter(['[]', json.dumps([{'status': 'in_progress', 'conclusion': ''}]),
                        json.dumps([{'status': 'completed', 'conclusion': 'success'}])])
        self.fake([(['gh', 'run', 'list'], lambda cmd: done(next(answers)))])
        self.assertEqual(ship.wait_for(lambda: ship.bump_run('abc'), 60, 'bump'), 'success')

    def test_timeout(self):
        with self.assertRaises(TimeoutError):
            ship.wait_for(lambda: None, 0, 'nothing')

    def test_plugins_touched(self):
        files = ['plugins/a/x.md', 'plugins/b/c/d.py', 'plugins/a/y.md', 'README.md', 'plugins/top.md']
        self.assertEqual(ship.plugins_touched(files), ['a', 'b'])

    def test_pull_stops_at_first_failure(self):
        fake = self.fake([(['git', 'checkout'], done(returncode=1, stderr='error: local changes'))])
        err = ship.pull_main(Path('/r'))
        self.assertIn('local changes', err)
        self.assertEqual([c[0][1] for c in fake.calls], ['fetch', 'checkout'])
        self.assertTrue(all(c[1] == str(Path('/r')) for c in fake.calls))


class Sync(Base):
    def write_installed(self, plugins):
        ship.INSTALLED.write_text(json.dumps({'plugins': plugins}), encoding='utf-8')

    def test_installed_targets_filters_marketplace_and_dedupes_scopes(self):
        m = ship.MARKETPLACE
        data = {'plugins': {f'a@{m}': [{'scope': 'user'}, {'scope': 'local'}, {'scope': 'local'}],
                            f'b@{m}': [{}],
                            'c@other': [{'scope': 'user'}]}}
        self.assertEqual(ship.installed_targets(data),
                         [(f'a@{m}', 'a', 'local'), (f'a@{m}', 'a', 'user'), (f'b@{m}', 'b', 'user')])

    def test_sync_updates_each_scope_and_skips_removed_plugins(self):
        m = ship.MARKETPLACE
        self.write_installed({f'a@{m}': [{'scope': 'local'}], f'gone@{m}': [{'scope': 'user'}]})
        fake = self.fake([(['claude', 'plugin', 'update', f'gone@{m}'],
                           done(returncode=1, stderr='Plugin "gone" not found in marketplace'))])
        rows = ship.sync()
        self.assertEqual(rows, [('a', 'local', 'ok'), ('gone', 'user', 'skipped (not in marketplace)')])
        self.assertIn((['claude', 'plugin', 'update', f'a@{m}', '--scope', 'local'], None), fake.calls)

    def test_sync_runs_post_step_only_for_updated_plugin(self):
        m = ship.MARKETPLACE
        self.write_installed({f'drainer@{m}': [{'scope': 'user'}]})
        launcher = ship.CACHE / 'drainer' / '2.0.0' / 'skills' / 'drainer' / 'scripts' / 'launch-drainer.py'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('new')
        (ship.HOME / '.claude' / 'drainer').mkdir(parents=True)
        self.fake([])
        rows = ship.sync()
        self.assertIn(('drainer', 'launcher', 'ok'), rows)
        self.assertEqual((ship.HOME / '.claude' / 'drainer' / 'launch-drainer.py').read_text(), 'new')

    def test_missing_installed_file_is_an_error_row(self):
        self.assertTrue(ship.sync()[0][2].startswith('ERR'))

    def test_pkg_dirs_skip_node_modules_and_dep_free_manifests(self):
        root = self.tmp / 'plugin'
        for rel, deps in (('a', {'x': '1'}), ('b', {}), ('a/node_modules/x', {'y': '1'})):
            (root / rel).mkdir(parents=True)
            (root / rel / 'package.json').write_text(json.dumps({'dependencies': deps}))
        self.assertEqual(ship.pkg_dirs_with_deps(root), [root / 'a'])


if __name__ == '__main__':
    unittest.main()
