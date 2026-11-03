import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hq.run import workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'a b'
        self.project.mkdir()
        self.worktrees = self.root / 'worktrees'
        self.env = patch.dict(os.environ, {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                                           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git(self.project, 'init')
        (self.project / 'file.txt').write_text('base\n', encoding='utf-8')
        self.git(self.project, 'add', 'file.txt')
        self.git(self.project, 'commit', '-m', 'base')

    def git(self, path, *args):
        return subprocess.run(['git', '-C', str(path), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                              check=True, capture_output=True, text=True, timeout=60).stdout.strip()

    def create(self, run_id='run_123'):
        return workspace.create(self.project, run_id, self.worktrees)[:3]

    def change(self, path, text, filename='file.txt'):
        (path / filename).write_text(text, encoding='utf-8')
        self.git(path, 'add', filename)
        self.git(path, 'commit', '-m', 'change')

    def test_create_makes_branch_and_worktree(self):
        workdir, branch, base = self.create()
        self.assertEqual(workdir, self.worktrees / 'run_123')
        self.assertEqual(branch, 'hq/run-run_123')
        self.assertEqual(base, self.git(self.project, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(workdir, 'branch', '--show-current'), branch)

    def test_non_git_returns_project(self):
        folder = self.root / 'plain'
        folder.mkdir()
        self.assertEqual(workspace.create(folder, 'run_1', self.worktrees), (folder, None, None, None))

    def test_diff_shows_change(self):
        workdir, branch, base = self.create()
        self.change(workdir, 'changed\n')
        text, truncated = workspace.diff(self.project, branch, base)
        self.assertIn('+changed', text)
        self.assertFalse(truncated)

    def test_diff_snapshots_edits_and_untracked_files_but_not_ignored_files(self):
        (self.project / '.gitignore').write_text('ignored.txt\n', encoding='utf-8')
        self.git(self.project, 'add', '.gitignore')
        self.git(self.project, 'commit', '-m', 'ignore files')
        workdir, branch, base = self.create()
        (workdir / 'file.txt').write_text('uncommitted edit\n', encoding='utf-8')
        (workdir / 'new.txt').write_text('new file\n', encoding='utf-8')
        (workdir / 'ignored.txt').write_text('ignored content\n', encoding='utf-8')
        text, _ = workspace.diff(self.project, branch, base, workdir=workdir)
        self.assertIn('+uncommitted edit', text)
        self.assertIn('+new file', text)
        self.assertNotIn('ignored.txt', text)
        self.assertNotIn('ignored content', text)

    def test_snapshot_without_changes_does_not_commit(self):
        workdir, _, _ = self.create()
        head = self.git(workdir, 'rev-parse', 'HEAD')
        self.assertFalse(workspace.snapshot(workdir, 'run_123', project=self.project))
        self.assertEqual(self.git(workdir, 'rev-parse', 'HEAD'), head)

    def test_diff_includes_agent_commit_and_later_edits(self):
        workdir, branch, base = self.create()
        self.change(workdir, 'agent commit\n', 'committed.txt')
        (workdir / 'file.txt').write_text('later edit\n', encoding='utf-8')
        text, _ = workspace.diff(self.project, branch, base, workdir=workdir)
        self.assertIn('+agent commit', text)
        self.assertIn('+later edit', text)

    def test_merge_snapshots_uncommitted_edit(self):
        workdir, branch, _ = self.create()
        (workdir / 'file.txt').write_text('merged edit\n', encoding='utf-8')
        self.assertEqual(workspace.merge(self.project, branch, workdir=workdir), 'merged')
        self.assertEqual((self.project / 'file.txt').read_text(encoding='utf-8'), 'merged edit\n')

    def test_diff_truncated_over_cap(self):
        workdir, branch, base = self.create()
        self.change(workdir, 'changed' * 100)
        text, truncated = workspace.diff(self.project, branch, base, cap=20)
        self.assertLessEqual(len(text.encode('utf-8')), 20)
        self.assertTrue(truncated)

    def test_fast_forward_merge(self):
        workdir, branch, _ = self.create()
        self.change(workdir, 'changed\n')
        self.assertEqual(workspace.merge(self.project, branch), 'merged')
        self.assertEqual(self.git(self.project, 'rev-parse', 'HEAD'), self.git(workdir, 'rev-parse', 'HEAD'))

    def test_merge_commit_when_diverged(self):
        workdir, branch, _ = self.create()
        self.change(workdir, 'run\n', 'run.txt')
        self.change(self.project, 'project\n', 'project.txt')
        self.assertEqual(workspace.merge(self.project, branch), 'merged')
        self.assertEqual(len(self.git(self.project, 'rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)

    def test_conflict_aborts_and_keeps_branch(self):
        workdir, branch, _ = self.create()
        self.change(workdir, 'run\n')
        self.change(self.project, 'project\n')
        head = self.git(self.project, 'rev-parse', 'HEAD')
        self.assertEqual(workspace.merge(self.project, branch), 'conflict')
        self.assertEqual(self.git(self.project, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git(self.project, 'rev-parse', branch), self.git(workdir, 'rev-parse', 'HEAD'))
        self.assertEqual(self.git(self.project, 'status', '--porcelain'), '')

    def test_dirty_checkout_refused(self):
        _, branch, _ = self.create()
        (self.project / 'untracked').write_text('dirty', encoding='utf-8')
        self.assertEqual(workspace.merge(self.project, branch), 'dirty')

    def test_discard_removes_worktree_and_branch(self):
        workdir, branch, _ = self.create()
        workspace.discard(self.project, workdir, branch)
        self.assertFalse(workdir.exists())
        self.assertNotIn(branch, self.git(self.project, 'branch', '--list'))

    def test_two_worktrees_same_repo(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.create, ['run_1', 'run_2']))
        self.assertNotEqual(results[0][0], results[1][0])
        self.assertNotEqual(results[0][1], results[1][1])
        self.assertTrue(all(item[0].is_dir() for item in results))

    def test_tampered_worktree_git_is_refused(self):
        workdir, branch, base = self.create()
        evil = workdir / 'evil'
        evil.mkdir()
        marker = self.root / 'marker'
        (evil / 'hook.py').write_text(f"open({str(marker)!r}, 'w').write('executed')")
        (evil / 'config').write_text('[core]\n fsmonitor = python evil/hook.py\n')
        (workdir / '.git').write_text('gitdir: evil\n')
        for operation in (lambda: workspace.diff(self.project, branch, base, workdir=workdir),
                          lambda: workspace.merge(self.project, branch, workdir=workdir)):
            with self.assertRaisesRegex(ValueError, 'worktree .git was changed'):
                operation()
        self.assertFalse(marker.exists())

    def test_worktree_config_is_refused(self):
        workdir, branch, base = self.create()
        admin = self.project / '.git' / 'worktrees' / workdir.name
        (admin / 'config.worktree').write_text('[core]\n hooksPath = evil\n')
        with self.assertRaisesRegex(ValueError, 'worktree .git was changed'):
            workspace.diff(self.project, branch, base, workdir=workdir)

    def test_merge_rejects_changes_since_viewed_commit(self):
        workdir, branch, base = self.create()
        workspace.diff(self.project, branch, base, workdir=workdir)
        commit = workspace.revision(self.project, branch)
        (workdir / 'file.txt').write_text('later edit\n')
        with self.assertRaisesRegex(ValueError, 'the run changed after you viewed'):
            workspace.merge(self.project, branch, workdir=workdir, commit=commit)
        workspace.diff(self.project, branch, base, workdir=workdir)
        commit = workspace.revision(self.project, branch)
        self.assertEqual(workspace.merge(self.project, branch, workdir=workdir, commit=commit), 'merged')

    def test_large_diff_is_truncated(self):
        workdir, branch, base = self.create()
        (workdir / 'large').write_text('x' * 10_000_000)
        from unittest.mock import Mock
        real_popen = subprocess.Popen
        processes = []
        def traced(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            if kwargs.get('stderr') == subprocess.DEVNULL:
                proc.stdout = Mock(wraps=proc.stdout)
                processes.append(proc)
            return proc
        with patch('hq.run.workspace.subprocess.Popen', side_effect=traced):
            text, truncated = workspace.diff(self.project, branch, base, cap=1024, workdir=workdir)
        processes[-1].stdout.read.assert_called_once_with(1025)
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), 1024)

    def test_path_with_space(self):
        workdir, branch, base = self.create()
        self.change(workdir, 'space\n')
        self.assertIn('+space', workspace.diff(self.project, branch, base)[0])
        self.assertEqual(workspace.merge(self.project, branch), 'merged')
