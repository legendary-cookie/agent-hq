import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        def result(stdout=b'{}', stderr=b'', returncode=0):
            return lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired('ssh', 120)
        for runner in (result(stderr=b'bad', returncode=255), timeout, result(stdout=b'x' * (64 * 1024 * 1024 + 1)), result(stdout=b'{')):
            with self.subTest(runner=runner), self.assertRaises(RemoteError):
                run_scan(self.host, {}, 'scanner', runner)
