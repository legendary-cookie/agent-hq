import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hq.config import Account, Config, Profile, load_config, save_config
from hq.run.config import load_projects, load_run_config
from hq.store import Store


class RunConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def store(self):
        store = Store(self.path / 'test.db')
        self.addCleanup(store.close)
        return store

    def test_missing_run_json_is_off(self):
        cfg = load_run_config(self.path)
        self.assertFalse(cfg.run_mode)
        self.assertEqual(cfg.max_concurrent, 3)

    def test_run_json_values_clamped(self):
        (self.path / 'run.json').write_text(json.dumps({'max_time_limit_min': 999, 'max_concurrent': 0}))
        cfg = load_run_config(self.path)
        self.assertEqual((cfg.max_time_limit_min, cfg.max_concurrent), (240, 1))

    def test_projects_resolve_and_drop_forbidden(self):
        good = self.path / 'good'
        bad = self.path / 'secrets'
        good.mkdir(); bad.mkdir()
        (self.path / 'projects.json').write_text(json.dumps({'projects': [{'name': 'good', 'path': str(good)}, {'name': 'bad', 'path': str(bad)}]}))
        with self.assertWarns(Warning):
            projects = load_projects(self.path)
        self.assertEqual([(p.name, p.path) for p in projects], [('good', good.resolve())])

    def test_profiles_parsed_and_saved_unchanged(self):
        profile = Profile('p', 'codex', str(self.path / 'profile'), 'a')
        cfg = Config({}, [Account('a', 'x', 'x', 'x', None, 1, [], {}, [profile])], {}, {}, 'x', 'coding')
        raw = [{'id': 'a', 'provider': 'x', 'label': 'x', 'plan': 'x', 'monthly_price_usd': None, 'billing_day': 1, 'match': [], 'cap_tokens': {}, 'profiles': [{'id': 'p', 'tool': 'codex', 'config_dir': str(self.path / 'profile')}]}]
        (self.path / 'accounts.json').write_text(json.dumps(raw))
        save_config(self.path, cfg)
        self.assertEqual(json.loads((self.path / 'accounts.json').read_text())[0]['profiles'], raw[0]['profiles'])
        self.assertEqual(load_config(self.path).accounts[0].profiles, [profile])
        save_config(self.path, load_config(self.path))
        self.assertEqual(json.loads((self.path / 'accounts.json').read_text())[0]['profiles'][0]['id'], 'p')

    def test_runs_table_roundtrip(self):
        store = self.store()
        for i in range(2):
            store.insert_run(dict(id=str(i), tool='x', project='p', workdir='p', level='edit', guard_mode='block', status='starting', started_at=i, time_limit_s=60, prompt_preview='x' * 300))
        store.update_run('0', status='done')
        self.assertEqual(store.get_run('0')['status'], 'done')
        self.assertEqual(len(store.get_run('0')['prompt_preview']), 200)
        self.assertEqual([r['id'] for r in store.list_runs()], ['1', '0'])
        with self.assertRaises(ValueError): store.update_run('0', unknown='x')

    def test_mark_interrupted(self):
        store = self.store()
        for status in ('starting', 'running', 'done'):
            store.insert_run(dict(id=status, tool='x', project='p', workdir='p', level='edit', guard_mode='block', status=status, time_limit_s=60))
        self.assertEqual(store.mark_interrupted(), 2)
        self.assertEqual(store.get_run('running')['status'], 'interrupted')
        self.assertEqual(store.get_run('done')['status'], 'done')

    def test_mark_interrupted_includes_live_steer_states(self):
        # Found in the real check: an idle live run kept 'idle' after a restart, so it looked
        # live but had no process, and Send stayed disabled.
        store = self.store()
        for status in ('queued', 'idle', 'needs_you', 'closing', 'done', 'failed'):
            store.insert_run(dict(id=status, tool='x', project='p', workdir='p', level='edit', guard_mode='block', status=status, time_limit_s=60))
        self.assertEqual(store.mark_interrupted(), 4)
        for status in ('queued', 'idle', 'needs_you', 'closing'):
            self.assertEqual(store.get_run(status)['status'], 'interrupted')
        self.assertEqual(store.get_run('failed')['status'], 'failed')

    def test_runs_migration_on_existing_db(self):
        with closing(sqlite3.connect(self.path / 'test.db')) as conn:
            conn.execute('CREATE TABLE old (id INTEGER)')
            conn.commit()
        store = self.store()
        with closing(store.conn()) as conn:
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchone())
