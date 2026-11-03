import json
import io
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hq.remote.config import Host, load_hosts
from hq.remote.ssh import RemoteError, run_scan, scan_argv


class RemoteConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name).resolve()
        self.host = Host('box', 'user@box', 'linux', None, 60, ('codex',))

    def write(self, hosts):
        (self.path / 'hosts.json').write_text(json.dumps({'hosts': hosts}), encoding='utf-8')

    def test_load_valid_hosts(self):
        self.write([{'name': 'box', 'ssh': 'user@box', 'os': 'linux', 'sources': ['codex']}])
        self.assertEqual(load_hosts(self.path), ([self.host], []))

    def test_invalid_entries_skipped_with_warning(self):
        base = {'name': 'box', 'ssh': 'box', 'os': 'linux', 'sources': ['codex']}
        entries = [{**base, **change} for change in ({'ssh': '-box'}, {'ssh': 'a b'}, {'ssh': 'a;b'}, {'name': 'Box'}, {'sources': ['unknown']})]
        self.write(entries)
        hosts, warnings = load_hosts(self.path)
        self.assertEqual(hosts, [])
        self.assertEqual(len(warnings), len(entries))

    def test_scan_argv_linux_exact(self):
        self.assertEqual(scan_argv(self.host), ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=10', '--', 'user@box', 'python3', '-'])

    def test_scan_argv_windows(self):
        self.assertEqual(scan_argv(Host('box', 'box', 'windows', None, 60, ()))[-2:], ['python', '-'])

    def test_scan_argv_container(self):
        self.assertEqual(scan_argv(Host('box', 'box', 'windows', 'web', 60, ()))[-6:], ['docker', 'exec', '-i', 'web', 'python3', '-'])

    def test_run_scan_errors(self):
        def process(stdout=b'{}', stderr=b'', returncode=0):
            class Input(io.BytesIO):
                def close(self):
                    pass
            return lambda *a, **k: SimpleNamespace(stdin=Input(), stdout=io.BytesIO(stdout), stderr=io.BytesIO(stderr), returncode=returncode, wait=lambda: returncode, kill=lambda: None)
        for popen in (process(stderr=b'bad', returncode=255), process(stdout=b'{')):
            with self.subTest(popen=popen), self.assertRaises(RemoteError):
                run_scan(self.host, {}, 'scanner', popen)

    def test_real_payload_through_python_dash(self):
        path = self.path / '.codex/sessions/a'
        path.parent.mkdir(parents=True)
        path.write_text('fixture')
        env = dict(os.environ, HOME=str(self.path), USERPROFILE=str(self.path))
        def local(argv, **kwargs):
            return subprocess.Popen([sys.executable, '-'], env=env, **kwargs)
        scanner = (Path(__file__).resolve().parents[1] / 'hq/remote/scanner.py').read_text()
        answer = run_scan(self.host, {'sources': ['codex']}, scanner, local)
        self.assertEqual(answer['files'][0]['path'], '.codex/sessions/a')

    def test_request_cannot_inject_code(self):
        env = dict(os.environ, HOME=str(self.path), USERPROFILE=str(self.path))
        def local(argv, **kwargs):
            return subprocess.Popen([sys.executable, '-'], env=env, **kwargs)
        scanner = (Path(__file__).resolve().parents[1] / 'hq/remote/scanner.py').read_text()
        answer = run_scan(self.host, {'sources': ["'); import os; os._exit(9) #\n"]}, scanner, local)
        self.assertEqual(answer['files'], [])

    def test_streaming_output_is_bounded(self):
        class Stream:
            count = 0
            def read(self, size):
                size = min(size, 200 * 1024 * 1024 - self.count)
                self.count += size
                return b'x' * size
            def close(self):
                pass
        stream = Stream()
        class Input(io.BytesIO):
            def close(self):
                pass
        proc = SimpleNamespace(stdin=Input(), stdout=stream, stderr=io.BytesIO(), returncode=0, wait=lambda: 0, kill=lambda: None)
        with self.assertRaisesRegex(RemoteError, 'scanner answer too large'):
            run_scan(self.host, {}, '', lambda *a, **k: proc)
        self.assertLessEqual(stream.count, 128 * 1024 * 1024 + 1)

    def test_watchdog_kills_timed_out_process(self):
        class Input(io.BytesIO):
            def close(self):
                pass
        killed = []
        proc = SimpleNamespace(stdin=Input(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                               returncode=-9, wait=lambda: -9, kill=lambda: killed.append(True))
        class Timer:
            def __init__(self, seconds, callback):
                self.callback = callback
                self.seconds = seconds
            def start(self):
                self.callback()
            def cancel(self):
                pass
        with patch('hq.remote.ssh.threading.Timer', Timer):
            with self.assertRaisesRegex(RemoteError, 'timed out'):
                run_scan(self.host, {}, '', lambda *a, **k: proc)
        self.assertTrue(killed)
