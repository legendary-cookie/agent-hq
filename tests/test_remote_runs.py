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

@unittest.skipIf(os.name == 'nt', 'POSIX helper process tests')
class RemoteIntegrationTests(unittest.TestCase):
    start = RemoteRunTests.start
    def setUp(self):
        RemoteRunTests.setUp(self)
        import json
        from unittest.mock import patch
        from hq.config import Config
        from hq.store import Store
        from hq.run.service import RunService
        from hq.run.launchers import REGISTRY
        tool = self.bin / 'claude'
        tool.write_text('#!/usr/bin/env python3\nimport json,sys,time\nprint(json.dumps({"type":"system","subtype":"init","session_id":"abc-123"}),flush=True)\nprint("streamed",flush=True)\n' +
                        'time.sleep(30) if "wait" in sys.stdin.readline() else None\n')
        tool.chmod(0o755)
        (self.root / 'hosts.json').write_text(json.dumps({'hosts': [dict(name='test', ssh='test', os='linux', sources=[], run=True, projects=[dict(name='p', path=str(self.project))])]}))
        (self.root / 'run.json').write_text(json.dumps({'run_mode': True}))
        self.store = Store(self.root/'db'); self.addCleanup(self.store.close)
        cfg = Config({}, [], {}, {'thresholds':[80,95], 'toast':False}, 'isometric', 'coding')
        self.patch = patch('hq.remote.remote_runs.RemoteRuns', side_effect=lambda host: self.remote)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.service = RunService(self.store, cfg, self.root, self.root, lambda _: [], launchers=REGISTRY)
        self.addCleanup(self.service.runner.shutdown)
        self.body = dict(host='test', project='p', tool='claude', prompt='hello', level='edit', guard_mode='warn')

    def run_remote(self, body=None):
        data = body or self.body
        rec = self.service.start(data | {'nonce': self.service.prepare(data)['nonce']})
        import time
        end = time.monotonic() + 12
        while time.monotonic() < end:
            row = self.store.get_run(rec['id'])
            if row['ended_at'] is not None: return row
            time.sleep(.02)
        self.fail('run did not finish')

    def test_remote_run_end_to_end_with_local_transport(self):
        row = self.run_remote()
        self.assertEqual(row['status'], 'done')
        self.assertEqual(row['workdir'], str(self.root / '.agent-hq/worktrees' / row['id']))
        self.assertIn('streamed', self.service.output(row['id']))

    def test_remote_finished_run_can_resume(self):
        import time
        row = self.run_remote()
        rid = row['id']
        nonce = self.service.message_prepare(rid, {'text': 'again'})['nonce']
        self.service.message(rid, {'text': 'again', 'nonce': nonce})
        end = time.monotonic() + 10
        while time.monotonic() < end:
            row = self.store.get_run(rid)
            if row['status'] == 'done' and row['turns'] == 2 and row['ended_at'] is not None:
                break
            time.sleep(.02)
        self.assertEqual((row['status'], row['turns']), ('done', 2))
        self.assertEqual(row['session_id'], 'test/claude_code:abc-123')

    def test_remote_codex_reads_prompt_to_eof(self):
        tool = self.bin / 'codex'
        tool.write_text('#!/usr/bin/env python3\nimport json,sys\ntext=sys.stdin.read()\nprint(json.dumps({"type":"thread.started","thread_id":"codex-123"}),flush=True)\nprint(text,flush=True)\n')
        tool.chmod(0o755)
        row = self.run_remote(self.body | {'tool': 'codex', 'level': 'read_only'})
        self.assertEqual(row['status'], 'done')
        self.assertIn('hello', self.service.output(row['id']))
        self.assertEqual(row['session_id'], 'test/codex:codex-123')

    def test_remote_session_id_prefixed_with_host(self):
        self.assertEqual(self.run_remote()['session_id'], 'test/claude_code:abc-123')

    def test_remote_workdir_token_only_exact(self):
        with self.assertRaises(ValueError): self.start(argv=['claude', 'prefix{workdir}'])
        process = self.remote._open(dict(action='start', id='run_bad', project='p', projects=list(self.host.projects), argv=['claude', 'prefix{workdir}'], env={}, level='edit'))
        process.communicate(timeout=8)
        self.assertNotEqual(process.returncode, 0)
        tool = self.bin / 'claude'
        tool.write_text('#!/usr/bin/env python3\nimport os,sys\nprint(sys.argv[1] == os.getcwd(), flush=True)\n'); tool.chmod(0o755)
        child = self.start(rid='run_token', argv=['claude', '{workdir}'])
        self.assertEqual(child.stdout.readline().strip(), 'True')
        self.assertEqual(child.wait(timeout=8), 0); child.close()

    def test_remote_full_auto_refused(self):
        from hq.run.service import RunError
        with self.assertRaises(RunError): self.service.prepare(self.body | {'level': 'full_auto'})
        with self.assertRaises(ValueError): self.remote.start('run_bad', 'p', ['claude'], {}, None, 'full_auto')

    def test_remote_project_must_be_in_host_list(self):
        from hq.run.service import RunError
        with self.assertRaisesRegex(RunError, 'Project is not allowlisted'): self.service.prepare(self.body | {'project': 'missing'})

    def test_remote_stop_calls_helper_stop(self):
        from unittest.mock import patch
        import time
        data = self.body | {'prompt': 'wait'}
        with patch.object(self.remote, 'action', wraps=self.remote.action) as action:
            rec = self.service.start(data | {'nonce': self.service.prepare(data)['nonce']})
            end = time.monotonic() + 8
            while time.monotonic() < end and 'streamed' not in self.service.output(rec['id']): time.sleep(.02)
            self.service.stop(rec['id'])
            self.assertTrue(any(c.args[0] == 'stop' for c in action.call_args_list))

    def test_remote_diff_merge_discard_via_helper(self):
        row = self.run_remote(); rid = row['id']
        (Path(row['workdir']) / 'file').write_text('edit\n')
        diff = self.service.diff(rid)
        self.assertIn('+edit', diff['diff'])
        self.assertEqual(self.service.merge(rid, {'commit': diff['commit']})['merge_state'], 'merged')
        self.assertEqual(self.service.discard(rid)['merge_state'], 'discarded')

    def test_remote_host_is_bound_to_nonce(self):
        from hq.run.service import RunError
        import json
        path = self.root / 'hosts.json'
        config = json.loads(path.read_text())
        config['hosts'].append(config['hosts'][0] | {'name': 'other'})
        path.write_text(json.dumps(config))
        nonce = self.service.prepare(self.body)['nonce']
        with self.assertRaisesRegex(RunError, 'confirm again'):
            self.service.start(self.body | {'host': 'other', 'nonce': nonce})

    def test_remote_tools_require_tested_launcher(self):
        self.service.launchers['claude'].tested = False
        try:
            self.assertNotIn('claude', self.service.options()['hosts'][0]['tools'])
        finally:
            self.service.launchers['claude'].tested = True

    def test_options_list_run_hosts(self):
        hosts = self.service.options()['hosts']
        self.assertEqual(hosts[0]['name'], 'test')
        self.assertEqual(hosts[0]['projects'], ['p'])
        self.assertIn('claude', hosts[0]['tools'])
        self.assertNotIn('full_auto', hosts[0]['tools']['claude']['levels'])
