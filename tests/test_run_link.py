import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hq.collector import Collector
from hq.config import Account, Config, Profile, SourceCfg
from hq.run.link import add_profile_paths, find_session
from hq.run.config import RunConfig
from hq.run.runner import Runner
from hq.store import Store


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = Store(self.root / 'db')
        self.addCleanup(self.store.close)

    def session(self, sid, harness='codex', project=None, started=100):
        with self.store._lock, self.store._writer:
            self.store._writer.execute('INSERT INTO sessions (id,harness,project,started_at) VALUES (?,?,?,?)',
                                       (sid, harness, str(project or self.root), started))

    def test_link_matches_project_and_window(self):
        self.session('old', started=100)
        self.session('new', project=self.root / 'sub' / '..', started=160)
        self.session('other', project=self.root / 'other', started=159)
        self.assertEqual(find_session(self.store, 'codex', self.root, 100), 'new')

    def test_link_ignores_other_harness(self):
        self.session('wrong', harness='claude_code')
        self.assertIsNone(find_session(self.store, 'codex', self.root, 100))

    def test_link_ignores_outside_window(self):
        self.session('early', started=99.9)
        self.session('late', started=160.1)
        self.assertIsNone(find_session(self.store, 'codex', self.root, 100))

    def test_link_none_when_absent(self):
        self.assertIsNone(find_session(self.store, 'codex', self.root, 100))

    def test_link_wsl_project(self):
        self.session('wsl', project='/mnt/c/Users/A Project', started=100)
        self.assertEqual(find_session(self.store, 'codex', Path(r'C:\Users\A Project'), 100), 'wsl')

    def test_profile_paths_added_in_memory_not_saved(self):
        claude = self.root / 'claude'
        codex = self.root / 'codex'
        account = Account('a', 'p', 'label', 'plan', None, 1, [], {}, [
            Profile('c', 'claude', str(claude), 'a'), Profile('x', 'codex', str(codex), 'a')])
        cfg = Config({'claude_code': SourceCfg(True, []), 'codex': SourceCfg(True, [])},
                     [account], {}, {}, 'skin', 'coding')
        marker = self.root / 'accounts.json'
        marker.write_text('unchanged', encoding='utf-8')
        Collector(self.store, cfg, [])
        add_profile_paths(cfg)
        self.assertEqual(cfg.sources['claude_code'].paths, [claude / 'projects'])
        expected = (Path('\\\\wsl.localhost\\Debian' + str(codex).replace('/', '\\')) / 'sessions'
                    if os.name == 'nt' and str(codex).startswith('/') else codex / 'sessions')
        self.assertEqual(cfg.sources['codex'].paths, [expected])
        self.assertEqual(marker.read_text(encoding='utf-8'), 'unchanged')

    def test_forbidden_profile_path_skipped(self):
        cfg = SimpleNamespace(accounts=[SimpleNamespace(profiles=[SimpleNamespace(tool='claude', config_dir=str(self.root / 'secrets'))])],
                              sources={'claude_code': SourceCfg(True, [])})
        add_profile_paths(cfg)
        self.assertEqual(cfg.sources['claude_code'].paths, [])

    def test_runner_fallback_after_grace_and_stop(self):
        runner = Runner(self.store, RunConfig(), {}, self.root, lambda *a: None, lambda e: None,
                        link_interval_s=.01, link_window_s=.3, link_grace_s=.03)
        self.addCleanup(runner.shutdown)
        runner._runs['r'] = {'session_id': None, 'stop': None, 'finished': False}
        self.store.insert_run(dict(id='r', tool='fake', project='p', workdir=str(self.root), level='read_only', guard_mode='warn', status='running', time_limit_s=1))
        self.session('found', started=100)
        runner._start_linker('r', SimpleNamespace(harness='codex', links_by_stream=True), self.root, 100)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not self.store.get_run('r')['session_id']:
            time.sleep(.01)
        self.assertEqual(self.store.get_run('r')['session_id'], 'found')
        runner._runs['r']['finished'] = True

    def test_stream_session_wins_pending_fallback(self):
        runner = Runner(self.store, RunConfig(), {}, self.root, lambda *a: None, lambda e: None,
                        link_interval_s=.01, link_window_s=.1, link_grace_s=0)
        self.addCleanup(runner.shutdown)
        runner._runs['r'] = {'session_id': None, 'stop': None, 'finished': False}
        self.store.insert_run(dict(id='r', tool='fake', project='p', workdir=str(self.root), level='read_only', guard_mode='warn', status='running', time_limit_s=1))
        def find(*args):
            with runner._lock:
                runner._runs['r']['session_id'] = 'stream'
                self.store.update_run('r', session_id='stream')
            return 'fallback'
        with patch('hq.run.runner.find_session', side_effect=find):
            runner._link_session('r', SimpleNamespace(harness='codex', links_by_stream=False), self.root, 100)
        self.assertEqual(self.store.get_run('r')['session_id'], 'stream')
        runner._runs['r']['finished'] = True
