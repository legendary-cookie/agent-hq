import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hq.remote.config import _host
from hq.remote.remote_runs import RemoteRuns


@unittest.skipIf(os.name == 'nt', 'POSIX helper process tests')
class RemoteRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        for args in (('init',), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid')):
            subprocess.run(['git', '-C', str(self.project), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.project / 'file').write_text('base\n')
        subprocess.run(['git', '-C', str(self.project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.project), 'commit', '-qm', 'base'], check=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        tool = self.bin / 'claude'
        tool.write_text('#!/usr/bin/env python3\nimport sys, time\nprint("ready", flush=True)\n' +
                        'time.sleep(30) if "wait" in sys.argv else None\n')
        tool.chmod(0o755)
        self.host = _host(dict(name='test', ssh='test', os='linux', sources=[], run=True,
                               projects=[dict(name='p', path=str(self.project))]))
        def local(argv, **kwargs):
            kwargs['env'] = dict(os.environ, HOME=str(self.root), PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
            return subprocess.Popen([sys.executable, '-c', shlex.split(argv[-1])[2]], **kwargs)
        self.remote = RemoteRuns(self.host, popen=local)

    def start(self, rid='run_test', argv=None):
        return self.remote.start(rid, 'p', argv or ['claude'], {}, None, 'edit')

    def test_helper_start_streams_and_exits(self):
        child = self.start()
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        self.assertEqual(child.wait(), 0)
        child.close()

    def test_helper_refuses_unlisted_project_and_tool(self):
        for project, argv in [('missing', ['claude']), ('p', ['sh'])]:
            request = dict(action='start', id='run_bad', project=project,
                           projects=list(self.host.projects), argv=argv, env={}, level='edit')
            process = self.remote._open(request)
            _, error = process.communicate(timeout=8)
            self.assertNotEqual(process.returncode, 0)
            self.assertTrue(error)

    def test_helper_kills_group_when_stdin_closes(self):
        child = self.start(argv=['claude', 'wait'])
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        child.stdin.close()
        self.assertNotEqual(child.wait(timeout=8), 0)
        child.close()

    def test_remote_run_stop(self):
        child = self.start(argv=['claude', 'wait'])
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        child.kill_tree()
        self.assertNotEqual(child.wait(timeout=8), 0)
        child.close()

    def test_remote_diff_merge_discard(self):
        child = self.start()
        child.wait(timeout=8)
        child.close()
        workdir = self.root / '.agent-hq' / 'worktrees' / 'run_test'
        (workdir / 'file').write_text('edit\n')
        result = self.remote.action('diff', 'run_test', 'p')
        self.assertIn('+edit', result['diff'])
        self.assertEqual(self.remote.action('merge', 'run_test', 'p', commit=result['commit'])['state'], 'merged')
        self.remote.action('discard', 'run_test', 'p')
        self.assertFalse(workdir.exists())


class RemoteHostTests(unittest.TestCase):
    def test_windows_host_not_offered(self):
        host = _host(dict(name='win', ssh='win', os='windows', sources=[], run=True, projects=[]))
        with self.assertRaises(ValueError):
            RemoteRuns(host)
