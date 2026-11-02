import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hq.adapters.zylos import ZylosAdapter
from hq.store import Store
from tests.fixtures.zylos.make_fixture import build


class ZylosTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'c4.db'
        build(self.path)
        self.store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = SimpleNamespace(paths=[self.path], zylos_previews=False)

    def poll(self):
        return ZylosAdapter().poll(self.store, self.cfg)

    def test_messages_become_events(self):
        batch = self.poll()
        self.assertEqual([(e.ext_id, e.kind, e.tool, e.ts) for e in batch.events if e.kind != 'error'],
                         [('c4:1', 'user', 'telegram', 1767225660), ('c4:2', 'assistant', 'lark', 1767225720)])
        self.assertEqual(batch.sessions[0].title, 'Zylos messages')

    def test_previews_off_by_default(self):
        self.assertTrue(all(e.preview is None for e in self.poll().events))

    def test_previews_on_when_enabled(self):
        self.cfg.zylos_previews = True
        self.assertEqual(self.poll().events[0].preview, 'private message')

    def test_failed_delivery_is_error(self):
        self.assertEqual(len([e for e in self.poll().events if e.kind == 'error']), 1)

    def test_cursor_resumes(self):
        self.store.apply(self.poll(), lambda *args: 'unassigned')
        self.assertEqual(self.poll().events, [])
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("INSERT INTO conversations(direction,channel,content,timestamp) VALUES ('in','system','new','2026-01-01 00:03:00')")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([e.ext_id for e in self.poll().events], ['c4:3'])


class ZylosAppSettingTests(unittest.TestCase):
    """The collector hands each reader only its SourceCfg; the previews switch is an
    app setting, so the reader must read it from the app Config it was built with."""

    def test_previews_follow_app_setting(self):
        import tempfile
        from types import SimpleNamespace
        from hq.config import SourceCfg
        from hq.store import Store
        from tests.fixtures.zylos.make_fixture import build as make_c4_db
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / 'c4.db'
        make_c4_db(db)
        store = Store(Path(tmp.name) / 'hq.db')
        self.addCleanup(store.close)
        settings = SimpleNamespace(zylos_previews=True)
        batch = ZylosAdapter(settings).poll(store, SourceCfg(True, [db]))
        self.assertTrue(any(e.preview for e in batch.events))
