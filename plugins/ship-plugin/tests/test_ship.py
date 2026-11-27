"""Unit tests for skills/ship-plugin/scripts/ship.py.

Every gh, git, and claude call goes through ship.run, which the tests replace with a scripted fake, and
the plugin cache is a throwaway temp directory, so nothing touches the network, the repo, or the real
cache.
Run directly: python plugins/ship-plugin/tests/test_ship.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SHIP = os.path.join(HERE, '..', 'skills', 'ship-plugin', 'scripts', 'ship.py')

_spec = importlib.util.spec_from_file_location('ship', SHIP)
ship = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ship)

ORIGIN = 'https://github.com/rrrutledge/rrrutledge-claude-code-plugins.git'


def done(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def pr_data(state='OPEN', draft=False, merge_sha=None):
    return {'number': 7, 'title': 'fix a thing', 'state': state, 'isDraft': draft,
            'headRefName': 'feature', 'headRefOid': 'headsha', 'baseRefName': 'main',
            'mergeCommit': {'oid': merge_sha} if merge_sha else None,
            'files': [{'path': 'plugins/drainer/x.md'}]}


class FakeRun:
    """Answers each call with the first scripted (prefix, result) whose prefix starts the command. A
    result may be a callable taking the command, or a list consumed one answer per call."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, cmd, cwd=None):
        self.calls.append((cmd, cwd))
        for prefix, result in self.script:
            if cmd[:len(prefix)] == prefix:
                if isinstance(result, list):
                    return result.pop(0) if len(result) > 1 else result[0]
                return result(cmd) if callable(result) else result
        return done()

    def ran(self, prefix):
        return [c for c, _ in self.calls if c[:len(prefix)] == prefix]


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(ship, k) for k in ('run', 'CACHE', 'INSTALLED', 'HOME', 'POLL_SECONDS', 'GH_REPO')}
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

    def test_newest_cached_file_skips_versions_without_it(self):
        rel = 'skills/x/setup.js'
        for v in ('1.2.0', '1.10.0'):
            (ship.CACHE / 'p' / v).mkdir(parents=True)
        f = ship.CACHE / 'p' / '1.2.0' / rel
        f.parent.mkdir(parents=True)
        f.write_text('')
        self.assertEqual(ship.newest_cached_file('p', rel), f)


class WhereTheSessionIs(Base):
    def test_origin_forms(self):
        for url in (ORIGIN + '\n', 'git@github.com:rrrutledge/rrrutledge-claude-code-plugins.git',
                    'https://github.com/RRRutledge/rrrutledge-claude-code-plugins'):
            self.assertEqual(ship.github_slug(url), ship.PLUGINS_REPO, url)
        for url in ('https://github.com/someone/fork.git', 'git@github-alias:someone/fork.git',
                    'https://user@github.com/someone/fork/', 'ssh://git@github.com:22/someone/fork'):
            self.assertEqual(ship.github_slug(url), 'someone/fork', url)
        for url in ('C:\\remotes\\app', ''):
            self.assertIsNone(ship.github_slug(url), url)

    def test_worktree_entries(self):
        porcelain = ('worktree C:/repo\nHEAD abc\nbranch refs/heads/main\n\n'
                     'worktree C:/repo/.claude/worktrees/x\nHEAD def\nbranch refs/heads/feature\n\n'
                     'worktree C:/detached\nHEAD 123\ndetached\n')
        self.assertEqual(ship.worktree_entries(porcelain),
                         [{'path': 'C:/repo', 'branch': 'main'},
                          {'path': 'C:/repo/.claude/worktrees/x', 'branch': 'feature'},
                          {'path': 'C:/detached', 'branch': None}])

    def test_resolves_main_checkout_from_a_worktree(self):
        self.fake([(['git', 'worktree'], done('worktree /r\n\nworktree /r/.claude/worktrees/w\n')),
                   (['git', '-C', '/r', 'remote'], done(ORIGIN))])
        self.assertEqual(ship.resolve_repo(None), (Path('/r'), ship.PLUGINS_REPO))

    def test_any_github_repo_resolves(self):
        fake = self.fake([(['git', '-C', '/x', 'worktree'], done('worktree /r\n')),
                          (['git', '-C', '/r', 'remote'], done('https://github.com/someone/else.git'))])
        self.assertEqual(ship.resolve_repo('/x'), (Path('/r'), 'someone/else'))
        self.assertTrue(fake.ran(['git', '-C', '/x', 'worktree', 'list']))

    def test_origin_without_a_slug_is_refused(self):
        self.fake([(['git', 'worktree'], done('worktree /r\n')),
                   (['git', '-C', '/r', 'remote'], done('C:\\remotes\\app'))])
        self.assertEqual(ship.resolve_repo(None), (None, None))

    def worktree_of(self, common):
        self.fake([(['git', 'rev-parse', '--path-format=absolute', '--git-dir'], done(common + '/worktrees/w')),
                   (['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], done(common)),
                   (['git', 'rev-parse', '--abbrev-ref'], done('feature'))])

    def test_linked_worktree_branch(self):
        self.worktree_of('/r/.git')
        self.assertEqual(ship.linked_worktree_branch('/r'), 'feature')

    def test_worktree_of_another_repo_does_not_count(self):
        self.worktree_of('/other/.git')
        self.assertIsNone(ship.linked_worktree_branch('/r'))

    def test_main_checkout_is_not_a_linked_worktree(self):
        self.fake([(['git', 'rev-parse', '--path-format=absolute'], done('/r/.git'))])
        self.assertIsNone(ship.linked_worktree_branch('/r'))

    def test_require_pushed(self):
        self.fake([(['git', 'status'], done(' M x.py'))])
        with self.assertRaises(ship.ShipError):
            ship.require_pushed(pr_data())
        self.fake([(['git', 'rev-parse', 'HEAD'], done('localsha'))])
        with self.assertRaises(ship.ShipError):
            ship.require_pushed(pr_data())
        self.fake([(['git', 'rev-parse', 'HEAD'], done('headsha'))])
        ship.require_pushed(pr_data())


class BeforeTheMerge(Base):
    def test_no_checks_reported_counts_as_green(self):
        self.fake([(['gh', 'pr', 'checks'], done(returncode=1, stderr="no checks reported on the 'feature' branch"))])
        self.assertEqual(ship.checks_settled(pr_data()), [])

    def test_pending_then_failed_checks(self):
        self.fake([(['gh', 'pr', 'checks'], [done(json.dumps([{'name': 'a', 'bucket': 'pending'}])),
                                              done(json.dumps([{'name': 'a', 'bucket': 'fail'},
                                                               {'name': 'b', 'bucket': 'pass'}]))])])
        self.assertEqual(ship.wait_for(lambda: ship.checks_settled(pr_data()), 60, 'checks'), ['a'])

    def test_draft_and_behind_branch_are_fixed_up(self):
        fake = self.fake([(['gh', 'api'], done(json.dumps({'behind_by': 3}))),
                          (['gh', 'pr', 'checks'], done('[]'))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.make_mergeable(pr_data(draft=True))
        self.assertTrue(fake.ran(['gh', 'pr', 'ready', '7']))
        self.assertTrue(fake.ran(['gh', 'pr', 'update-branch', '7']))

    def test_up_to_date_branch_is_left_alone(self):
        fake = self.fake([(['gh', 'api'], done(json.dumps({'behind_by': 0}))),
                          (['gh', 'pr', 'checks'], done('[]'))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.make_mergeable(pr_data())
        self.assertFalse(fake.ran(['gh', 'pr', 'update-branch']))
        self.assertFalse(fake.ran(['gh', 'pr', 'ready']))

    def test_conflicting_update_stops(self):
        self.fake([(['gh', 'api'], done(json.dumps({'behind_by': 1}))),
                   (['gh', 'pr', 'update-branch'], done(returncode=1, stderr='merge conflict'))])
        with self.assertRaises(ship.ShipError) as e:
            ship.make_mergeable(pr_data())
        self.assertIn('conflicts', str(e.exception))

    def test_merge_falls_back_to_auto(self):
        fake = self.fake([(['gh', 'pr', 'merge'], lambda cmd: done() if '--auto' in cmd
                           else done(returncode=1, stderr='base branch policy prohibits the merge'))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.merge(pr_data(), admin=False)
        self.assertEqual(fake.ran(['gh', 'pr', 'merge'])[-1][-1], '--auto')

    def test_merge_uses_a_method_the_repo_allows(self):
        fake = self.fake([(['gh', 'repo', 'view'], done(json.dumps({'squashMergeAllowed': False,
                                                                     'mergeCommitAllowed': True})))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.merge(pr_data(), admin=False)
        self.assertEqual(fake.ran(['gh', 'pr', 'merge'])[0][-1], '--merge')

    def test_merge_refused_entirely(self):
        self.fake([(['gh', 'pr', 'merge'], done(returncode=1, stderr='not mergeable'))])
        with self.assertRaises(ship.ShipError):
            ship.merge(pr_data(), admin=False)

    def test_closed_unmerged_raises(self):
        self.fake([(['gh', 'pr', 'view'], done(json.dumps(pr_data(state='CLOSED'))))])
        with self.assertRaises(ship.ShipError):
            ship.merged_pr(7)


class AfterTheMerge(Base):
    def test_bump_run_waits_for_run_to_appear_and_complete(self):
        self.fake([(['gh', 'run', 'list'], [done('[]'),
                                             done(json.dumps([{'status': 'in_progress', 'conclusion': ''}])),
                                             done(json.dumps([{'status': 'completed', 'conclusion': 'success'}]))])])
        self.assertEqual(ship.wait_for(lambda: ship.bump_run('abc'), 60, 'bump'), 'success')

    def test_timeout(self):
        with self.assertRaises(ship.ShipError):
            ship.wait_for(lambda: None, 0, 'nothing')

    def test_plugins_touched(self):
        files = ['plugins/a/x.md', 'plugins/b/c/d.py', 'plugins/a/y.md', 'README.md', 'plugins/top.md']
        self.assertEqual(ship.plugins_touched(files), ['a', 'b'])

    def test_sync_pulls_in_the_checkout_that_holds_the_base(self):
        porcelain = 'worktree /r\nbranch refs/heads/wip\n\nworktree /r/m\nbranch refs/heads/main\n'
        fake = self.fake([(['git', 'worktree', 'list'], done(porcelain))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.sync_base(Path('/r'), 'main')
        self.assertEqual([c for c in fake.calls if c[0][1] == 'pull'],
                         [(['git', 'pull', '--ff-only', 'origin', 'main'], '/r/m')])
        self.assertFalse(fake.ran(['git', 'checkout']))

    def test_sync_with_no_checkout_on_base_moves_the_ref_alone(self):
        fake = self.fake([(['git', 'worktree', 'list'], done('worktree /r\nbranch refs/heads/wip\n'))])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ship.sync_base(Path('/r'), 'main')
        self.assertTrue(fake.ran(['git', 'fetch', 'origin', 'main:main']))
        self.assertFalse(fake.ran(['git', 'pull']))
        self.assertIn('only the branch moved', out.getvalue())

    def test_sync_refused_by_local_changes_stops(self):
        self.fake([(['git', 'worktree', 'list'], done('worktree /r\nbranch refs/heads/main\n')),
                   (['git', 'pull'], done(returncode=1, stderr='error: Your local changes would be overwritten'))])
        with self.assertRaises(ship.ShipError) as e:
            ship.sync_base(Path('/r'), 'main')
        self.assertIn('local changes', str(e.exception))

    def test_clean_up_keeps_a_dirty_worktree_and_removes_a_clean_one(self):
        porcelain = ('worktree /r\nbranch refs/heads/main\n\nworktree /r/w1\nbranch refs/heads/feature\n\n'
                     'worktree /r/w2\nbranch refs/heads/feature\n\nworktree /r/w3\nbranch refs/heads/other\n')
        fake = self.fake([(['git', 'worktree', 'list'], done(porcelain)),
                          (['git', '-C', '/r/w1', 'status'], done(' M dirty')),
                          (['git', 'rev-parse', '--verify'], done('sha'))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.clean_up_local(Path('/r'), 'feature')
        self.assertEqual(fake.ran(['git', 'worktree', 'remove']), [['git', 'worktree', 'remove', '/r/w2']])
        self.assertFalse(fake.ran(['git', 'branch', '-D', 'feature']))

    def test_clean_up_deletes_the_branch_once_its_worktrees_are_gone(self):
        porcelain = 'worktree /r\nbranch refs/heads/main\n\nworktree /r/w1\nbranch refs/heads/feature\n'
        fake = self.fake([(['git', 'worktree', 'list'], done(porcelain)),
                          (['git', '-C', '/r/w1', 'status'], done('')),
                          (['git', 'rev-parse', '--verify'], done('sha'))])
        with contextlib.redirect_stdout(io.StringIO()):
            ship.clean_up_local(Path('/r'), 'feature')
        self.assertTrue(fake.ran(['git', 'branch', '-D', 'feature']))


class WholeChain(Base):
    def chain(self, view_answers, in_worktree, origin=ORIGIN):
        rev_parse = (['git', 'rev-parse', '--path-format=absolute'],
                     (lambda cmd: done('/r/.git/worktrees/w' if '--git-dir' in cmd else '/r/.git'))
                     if in_worktree else done('/r/.git'))
        slug = ship.github_slug(origin)
        return self.fake([
            (['git', 'worktree', 'list'], done('worktree /r\nbranch refs/heads/main\n')),
            (['git', '-C', '/r', 'remote'], done(origin)),
            rev_parse,
            (['git', 'rev-parse', '--abbrev-ref'], done('feature')),
            (['git', 'rev-parse', 'HEAD'], done('headsha')),
            (['gh', 'pr', 'view'], view_answers),
            (['gh', 'api', f'repos/{slug}/compare/main...feature'], done('{"behind_by": 0}')),
            (['gh', 'pr', 'checks'], done('[]')),
            (['gh', 'run', 'list'], done(json.dumps([{'status': 'completed', 'conclusion': 'success'}]))),
        ])

    def run_ship(self, argv=('--pr', '7')):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ship.main(list(argv))
        return code, out.getvalue()

    def test_repo_without_pr_stops_before_any_lookup(self):
        fake = self.chain([done(json.dumps(pr_data()))], in_worktree=False)
        code, out = self.run_ship(['--repo', '/r'])
        self.assertEqual(code, 1, out)
        self.assertIn('--repo needs --pr', out)
        self.assertFalse(fake.ran(['gh']))

    def test_fork_pr_leaves_same_named_branches_alone(self):
        merged = dict(pr_data('MERGED', merge_sha='m1'), isCrossRepository=True)
        fake = self.chain([done(json.dumps(merged))], in_worktree=False, origin='https://github.com/someone/app')
        code, out = self.run_ship()
        self.assertEqual(code, 0, out)
        self.assertFalse(fake.ran(['gh', 'api', '-X', 'DELETE']))
        self.assertFalse(fake.ran(['git', 'branch', '-D']))

    def test_plugins_pr_into_another_base_skips_the_bump_wait(self):
        ship.INSTALLED.write_text(json.dumps({'plugins': {}}), encoding='utf-8')
        merged = dict(pr_data('MERGED', merge_sha='m1'), baseRefName='stack')
        fake = self.chain([done(json.dumps(merged))], in_worktree=False)
        code, out = self.run_ship()
        self.assertEqual(code, 0, out)
        self.assertFalse(fake.ran(['gh', 'run', 'list']))
        self.assertTrue(fake.ran(['git', 'fetch', 'origin', 'stack:stack']))

    def test_from_a_worktree_it_merges_then_stops_for_exit_worktree(self):
        fake = self.chain([done(json.dumps(pr_data())), done(json.dumps(pr_data('MERGED', merge_sha='m1')))],
                          in_worktree=True)
        code, out = self.run_ship()
        self.assertEqual(code, ship.EXIT_WORKTREE, out)
        self.assertIn('EXIT_WORKTREE keep', out)
        self.assertTrue(fake.ran(['gh', 'pr', 'merge', '7']))
        self.assertFalse(fake.ran(['git', 'pull']))

    def test_rerun_from_main_checkout_skips_the_merge_and_rolls_out(self):
        ship.INSTALLED.write_text(json.dumps({'plugins': {}}), encoding='utf-8')
        fake = self.chain([done(json.dumps(pr_data('MERGED', merge_sha='m1')))], in_worktree=False)
        code, out = self.run_ship()
        self.assertEqual(code, 0, out)
        self.assertFalse(fake.ran(['gh', 'pr', 'merge']))
        self.assertTrue(fake.ran(['git', 'pull', '--ff-only', 'origin', 'main']))
        self.assertIn('live: after a restart', out)

    def test_other_repo_merges_and_syncs_main_without_the_plugin_rollout(self):
        fake = self.chain([done(json.dumps(pr_data())), done(json.dumps(pr_data('MERGED', merge_sha='m1')))],
                          in_worktree=False, origin='git@github.com:someone/app.git')
        code, out = self.run_ship()
        self.assertEqual(code, 0, out)
        self.assertTrue(fake.ran(['gh', 'pr', 'merge', '7', '--repo', 'someone/app']))
        self.assertTrue(fake.ran(['git', 'pull', '--ff-only', 'origin', 'main']))
        self.assertFalse(fake.ran(['gh', 'run', 'list']))
        self.assertFalse(fake.ran(['claude']))
        self.assertNotIn('plugins:', out)

    def test_worktree_rerun_line_names_the_repo(self):
        self.chain([done(json.dumps(pr_data('MERGED', merge_sha='m1')))], in_worktree=True,
                   origin='https://github.com/someone/app')
        code, out = self.run_ship()
        self.assertEqual(code, ship.EXIT_WORKTREE, out)
        self.assertIn('--pr 7 --repo', out)


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
        self.assertTrue(fake.ran(['claude', 'plugin', 'update', f'a@{m}', '--scope', 'local']))

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

    def test_checkout_deps_install_only_where_no_setup_js(self):
        repo = self.tmp / 'clone'
        for name, setup in (('rooted', False), ('stored', True)):
            plugin = repo / 'plugins' / name
            plugin.mkdir(parents=True)
            (plugin / 'package.json').write_text(json.dumps({'dependencies': {'x': '1'}}))
            if setup:
                (plugin / 'setup.js').write_text('')
        installed = []
        ship.npm_install = lambda cwd: (installed.append(cwd) or (True, ''))
        rows = ship.ensure_checkout_node_deps(repo)
        self.assertEqual(rows, [('rooted', 'checkout-deps', 'ok')])
        self.assertEqual(installed, [repo / 'plugins' / 'rooted'])


class NonAsciiOutput(Base):
    def test_emoji_title_prints_under_a_cp1252_stdout(self):
        title = 'drainer: \U0001f4b0 Money label'
        data = dict(pr_data(state='CLOSED'), title=title)
        self.fake([(['git', '-C', 'unused', 'worktree'], done('worktree /r\n')),
                   (['git', '-C', '/r', 'remote'], done(ORIGIN)),
                   (['gh', 'pr', 'view'], done(json.dumps(data)))])
        raw = io.BytesIO()
        saved = ship.sys.stdout, ship.sys.stderr
        ship.sys.stdout = io.TextIOWrapper(raw, encoding='cp1252', errors='strict')
        ship.sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding='cp1252', errors='strict')
        try:
            code = ship.main(['--pr', '7', '--repo', 'unused'])
            ship.sys.stdout.flush()
            printed = raw.getvalue().decode('utf-8')
        finally:
            ship.sys.stdout, ship.sys.stderr = saved
        self.assertEqual(code, 1)
        self.assertIn(title, printed)


if __name__ == '__main__':
    unittest.main()
