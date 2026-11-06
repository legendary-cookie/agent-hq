import json
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from hq.config import load_config
from hq.fleet import Window, fleet_overview, load_fleet, rolling_window
from hq.records import Batch, EventRec, SessionRec
from hq.status import last_kinds
from hq.store import Store

ROOT = Path(__file__).resolve().parent.parent
MODEL = 'claude-sonnet-4-6'


def event(sid, ext, ts, kind, tool=None, tokens=None):
    return EventRec(sid, ext, ts, kind, tool, None, tokens, None, None, None, False, MODEL if tokens else None)


class FleetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.store = Store(self.root / 'hq.db')
        self.addCleanup(self.store.close)
        self.cfg = load_config(ROOT / 'config')
        self.now = time.time()
        self.win = Window(self.now - 3600, self.now + 1, self.now - 7200, self.now - 3600, 600, 6)

    def add(self, host, sid, events, ended=None):
        full = f'{host}/{sid}'
        stamps = [e[1] for e in events]
        recs = [event(full, f'e{i}', ts, kind, tool, tokens) for i, (kind, ts, tool, tokens) in enumerate(events)]
        session = SessionRec(full, 'claude_code', 'anthropic', MODEL, None, 'x', min(stamps), max(stamps), ended, None, 'r')
        self.store.apply(Batch([session], recs, [], []), lambda *a: 'a', (), host=host)

    def overview(self, health=None, meta=None):
        with closing(self.store.conn()) as conn:
            return fleet_overview(conn, self.cfg, self.win, self.now, health or {}, meta or {'brand': None, 'agents': {}})

    def agent(self, data, host):
        return next(a for a in data['agents'] if a['host'] == host)

    def test_usage_is_per_host_and_priced(self):
        self.add('one', 'claude_code:a', [('assistant', self.now - 10, None, 1000)])
        self.add('two', 'claude_code:b', [('assistant', self.now - 10, None, 3000)])
        data = self.overview()
        self.assertEqual((self.agent(data, 'one')['tokens'], self.agent(data, 'two')['tokens']), (1000, 3000))
        self.assertGreater(self.agent(data, 'two')['value_usd'], self.agent(data, 'one')['value_usd'])
        self.assertEqual(data['totals']['tokens'], 4000)

    def test_models_are_totalled_with_the_biggest_first(self):
        self.add('one', 'claude_code:a', [('assistant', self.now - 10, None, 1000)])
        self.add('two', 'claude_code:b', [('assistant', self.now - 10, None, 3000)])
        models = self.overview()['models']
        self.assertEqual([(m['model'], m['tokens']) for m in models], [(MODEL, 4000)])
        self.assertGreater(models[0]['value_usd'], 0)

    def test_previous_window_is_kept_out_of_current_totals(self):
        self.add('one', 'claude_code:a', [('assistant', self.now - 5000, None, 1000)])
        self.add('one', 'zylos:comm', [('user', self.now - 5000, 'telegram', None), ('user', self.now - 10, 'telegram', None)])
        totals = self.overview()['totals']
        self.assertEqual((totals['tokens'], totals['messages'], totals['previous_messages']), (0, 1, 1))
        self.assertGreater(totals['previous_value_usd'], 0)

    def test_messages_skip_system_channels(self):
        self.add('one', 'zylos:comm', [('user', self.now - 10, 'telegram', None), ('assistant', self.now - 9, 'telegram', None),
                                       ('user', self.now - 8, 'system', None), ('assistant', self.now - 7, 'scheduler', None),
                                       ('assistant', self.now - 6, 'void', None), ('user', self.now - 5, 'shell', None)])
        agent = self.agent(self.overview(), 'one')
        self.assertEqual((agent['messages_in'], agent['messages_out'], sum(agent['cells'])), (1, 1, 2))

    def test_agent_channel_is_reported_apart_but_still_counts_as_work(self):
        self.add('one', 'zylos:comm', [('user', self.now - 10, 'telegram', None), ('user', self.now - 9, 'hxa-connect', None),
                                       ('assistant', self.now - 8, 'hxa-connect', None), ('user', self.now - 5000, 'hxa-connect', None)])
        data = self.overview()
        agent = self.agent(data, 'one')
        self.assertEqual((agent['messages_in'], agent['messages_out'], agent['agent_messages'], sum(agent['cells'])), (1, 0, 2, 3))
        self.assertEqual((data['totals']['messages'], data['totals']['agent_messages'], data['totals']['previous_messages']), (1, 2, 0))

    def test_cells_land_in_the_right_place(self):
        self.add('one', 'zylos:comm', [('user', self.win.start + 5, 'telegram', None), ('user', self.win.start + 650, 'telegram', None),
                                       ('assistant', self.win.start + 660, 'telegram', None), ('user', self.now - 1, 'telegram', None)])
        self.assertEqual(self.agent(self.overview(), 'one')['cells'], [1, 2, 0, 0, 0, 1])

    def test_series_for_the_graphs_add_up_to_the_totals(self):
        self.add('one', 'zylos:comm', [('user', self.win.start + 5, 'telegram', None), ('assistant', self.now - 9, 'telegram', None),
                                       ('user', self.now - 8, 'hxa-connect', None), ('user', self.now - 7, 'system', None)])
        self.add('two', 'claude_code:a', [('assistant', self.win.start + 650, None, 2000), ('error', self.now - 4, None, None)])
        data = self.overview()
        series, totals = data['series'], data['totals']
        self.assertEqual({len(values) for values in series.values()}, {6})
        self.assertEqual(series['messages'], [1, 0, 0, 0, 0, 1])
        self.assertEqual((sum(series['agent_messages']), sum(series['errors']), sum(series['tokens'])), (1, 1, 2000))
        self.assertEqual(sum(series['actions']), totals['actions'])
        self.assertAlmostEqual(sum(series['value_usd']), totals['value_usd'], places=3)
        self.assertEqual(series['tokens'][1], 2000)

    def test_cells_count_agent_work_without_messages(self):
        self.add('busy', 'claude_code:a', [('user', self.now - 9, None, None), ('tool_call', self.now - 8, 'Read', None),
                                           ('tool_result', self.now - 7, None, None), ('assistant', self.now - 6, None, 100),
                                           ('turn_end', self.now - 5, None, None), ('error', self.now - 4, None, None)])
        agent = self.agent(self.overview(), 'busy')
        self.assertEqual((sum(agent['cells']), agent['messages_in'] + agent['messages_out']), (3, 0))

    def test_status_order(self):
        self.add('work', 'claude_code:a', [('tool_call', self.now - 5, 'Read', None)])
        self.add('talk', 'zylos:comm', [('user', self.now - 5, 'telegram', None)])
        self.add('idle', 'claude_code:c', [('turn_end', self.now - 86400, None, None)])
        self.add('old', 'claude_code:d', [('turn_end', self.now - 30 * 86400, None, None)])
        self.add('bad', 'claude_code:e', [('tool_call', self.now - 5, 'Read', None)])
        data = self.overview({'bad': {'state': 'partial', 'error': 'ssh exited 255'}, 'never': {'state': 'pending', 'error': None}})
        self.assertEqual({a['host']: a['status'] for a in data['agents']},
                         {'work': 'working', 'talk': 'recent', 'idle': 'idle', 'old': 'quiet', 'bad': 'attention', 'never': 'quiet'})
        self.assertEqual(data['totals']['attention'], 1)

    def test_an_ended_session_is_not_working(self):
        self.add('one', 'claude_code:a', [('tool_call', self.now - 5, 'Read', None)], ended=self.now - 4)
        self.assertEqual(self.agent(self.overview(), 'one')['status'], 'recent')

    def test_unreachable_agent_marks_cells_with_no_data(self):
        last_ok = self.win.start + 1250  # inside cell 2, so cells 3, 4 and 5 have no data
        data = self.overview({'bad': {'state': 'partial', 'error': 'x', 'last_ok': last_ok}, 'never': {'state': 'partial', 'error': 'x', 'last_ok': None}, 'fine': {'state': 'ok', 'error': None, 'last_ok': last_ok}})
        self.assertEqual((self.agent(data, 'bad')['gap'], self.agent(data, 'never')['gap'], self.agent(data, 'fine')['gap']), (3, 6, 0))

    def test_catching_up_is_counted(self):
        self.assertEqual(self.overview({'a': {'state': 'ok', 'catching_up': True}, 'b': {'state': 'ok'}})['totals']['catching_up'], 1)

    def test_names_come_from_fleet_json_and_unconfigured_agents_still_list(self):
        (self.root / 'fleet.json').write_text(json.dumps({'brand': 'Acme HQ', 'agents': {'one': {'label': 'Ada', 'owner': 'Ada L', 'group': 'Staff'}, 'bad': 'x'}}), encoding='utf-8')
        meta = load_fleet(self.root)
        self.assertEqual(meta['brand'], 'Acme HQ')
        self.assertNotIn('bad', meta['agents'])
        agent = self.agent(self.overview(meta=meta), 'one')
        self.assertEqual((agent['label'], agent['owner'], agent['group'], agent['tokens']), ('Ada', 'Ada L', 'Staff', 0))

    def test_broken_fleet_json_is_ignored(self):
        empty = {'brand': None, 'agents': {}}
        self.assertEqual(load_fleet(self.root), empty)
        (self.root / 'fleet.json').write_text('{', encoding='utf-8')
        self.assertEqual(load_fleet(self.root), empty)

    def test_kpis_add_up(self):
        def opus(sid, ext, ts, numbers):
            return EventRec(sid, ext, ts, 'assistant', None, None, *numbers, False, 'claude-opus-4-5')
        session = SessionRec('busy/claude_code:a', 'claude_code', 'anthropic', 'claude-opus-4-5', None, 'x', self.now - 9, self.now - 9, None, None, 'r')
        self.store.apply(Batch([session], [opus('busy/claude_code:a', 'a', self.now - 9, (100, 100, 800, 0))], [], []), lambda *a: 'a', (), host='busy')
        self.add('talks', 'claude_code:b', [('assistant', self.now - 10, None, 1000)])
        self.add('talks', 'zylos:comm', [('user', self.now - 10, 'telegram', None)])
        totals = self.overview()['totals']
        self.assertEqual((totals['tokens'], totals['cache_read_tokens'], totals['silent_agents'], totals['actions']), (2000, 800, 1, 3))
        self.assertGreater(totals['top_tier_usd'], 0)
        self.assertLess(totals['top_tier_usd'], totals['value_usd'])
        self.assertAlmostEqual(totals['silent_usd'], totals['top_tier_usd'])
        days = self.win.bucket_s * self.win.count / 86400
        self.assertAlmostEqual(totals['projected_30d_usd'], totals['value_usd'] / days * 30)

    def test_an_agent_with_no_spend_is_not_silent_spending(self):
        self.add('idle', 'claude_code:a', [('user', self.now - 9, None, None)])
        self.assertEqual(self.overview()['totals']['silent_agents'], 0)

    def test_last_kinds_can_skip_old_sessions(self):
        self.add('one', 'claude_code:new', [('user', self.now - 10, None, None)])
        self.add('one', 'claude_code:old', [('user', self.now - 90000, None, None)])
        with closing(self.store.conn()) as conn:
            self.assertEqual(set(last_kinds(conn)), {'one/claude_code:new', 'one/claude_code:old'})
            self.assertEqual(last_kinds(conn, since=self.now - 86400), {'one/claude_code:new': 'user'})


class WindowTests(unittest.TestCase):
    now = datetime(2026, 11, 6, 14, 25).timestamp()

    def check(self, name, bucket, count, end):
        win = rolling_window(name, self.now)
        self.assertEqual((win.bucket_s, win.count, win.end), (bucket, count, end.timestamp()))
        self.assertEqual(win.start, win.end - bucket * count)
        self.assertEqual((win.prev_end, win.prev_start), (win.start, win.start - bucket * count))
        self.assertGreater(win.end, self.now)

    def test_shapes(self):
        self.check('today', 3600, 24, datetime(2026, 11, 6, 15))
        self.check('week', 21600, 28, datetime(2026, 11, 6, 18))
        self.check('month', 86400, 30, datetime(2026, 11, 7))

    def test_end_of_day_rolls_over(self):
        late = datetime(2026, 11, 6, 23, 30).timestamp()
        self.assertEqual(rolling_window('today', late).end, datetime(2026, 11, 7).timestamp())
        self.assertEqual(rolling_window('week', late).end, datetime(2026, 11, 7).timestamp())

    def test_unknown_range_is_refused(self):
        with self.assertRaises(ValueError):
            rolling_window('year', self.now)
