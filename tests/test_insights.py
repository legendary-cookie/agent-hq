import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from hq.fleet import Window
from hq.insights import insights, reply_times
from hq.records import Batch, EventRec, SessionRec
from hq.store import Store

COMM = 'zylos:comm'


class InsightTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name).resolve() / 'hq.db')
        self.addCleanup(self.store.close)
        self.now = time.time()
        self.win = Window(self.now - 86400, self.now + 1, self.now - 2 * 86400, self.now - 86400, 3600, 24)
        self.n = 0

    def add(self, host, sid, events):
        full = f'{host}/{sid}'
        recs = []
        for kind, ts, tool, *text in events:
            self.n += 1
            recs.append(EventRec(full, f'e{self.n}', ts, kind, tool, text[0] if text else None, None, None, None, None, False, None))
        stamps = [e.ts for e in recs]
        session = SessionRec(full, 'zylos' if sid == COMM else 'claude_code', None, None, None, 't', min(stamps), max(stamps), None, None, 'r')
        self.store.apply(Batch([session], recs, [], []), lambda *a: 'a', (), host=host)
        if sid != COMM:
            self.store.derive_tasks({full})

    def data(self):
        with closing(self.store.conn()) as conn:
            return insights(conn, self.win, self.now)

    def test_reply_pairs_the_oldest_waiting_message(self):
        rows = [('a', 'telegram', 'user', 0), ('a', 'telegram', 'user', 10), ('a', 'telegram', 'assistant', 30), ('a', 'telegram', 'assistant', 40)]
        self.assertEqual([(h, c, w) for h, c, w, _ in reply_times(rows)], [('a', 'telegram', 30)])

    def test_reply_is_per_agent_and_channel(self):
        rows = [('a', 'telegram', 'user', 0), ('b', 'telegram', 'assistant', 5), ('a', 'whatsapp', 'assistant', 6), ('a', 'telegram', 'assistant', 9)]
        self.assertEqual([(h, c, w) for h, c, w, _ in reply_times(rows)], [('a', 'telegram', 9)])

    def test_a_very_late_reply_is_not_an_answer(self):
        self.assertEqual(reply_times([('a', 't', 'user', 0), ('a', 't', 'assistant', 7 * 3600)]), [])

    def test_reply_speed_by_channel_ignores_agent_and_system_channels(self):
        self.add('a', COMM, [('user', self.now - 100, 'telegram'), ('assistant', self.now - 40, 'telegram'),
                             ('user', self.now - 90, 'hxa-connect'), ('assistant', self.now - 89, 'hxa-connect'),
                             ('user', self.now - 80, 'system'), ('assistant', self.now - 79, 'void')])
        replies = self.data()['replies']
        self.assertEqual(list(replies['by_channel']), ['telegram'])
        self.assertEqual((replies['all']['replies'], replies['all']['median_s']), (1, 60))

    def test_limit_and_api_error_messages_are_counted(self):
        self.add('a', 'claude_code:x', [('user', self.now - 50, None), ('assistant', self.now - 40, None, 'Claude AI usage limit reached|1790000000'),
                                        ('assistant', self.now - 30, None, 'API Error: 529 overloaded_error'), ('error', self.now - 20, None)])
        data = self.data()
        self.assertEqual((data['reliability']['limit_messages'], data['reliability']['api_errors'], data['reliability']['tool_errors']), (1, 1, 1))
        self.assertEqual(data['agents'][0]['host'], 'a')

    def test_tools_and_busy_hours(self):
        self.add('a', 'claude_code:x', [('user', self.now - 50, None), ('tool_call', self.now - 40, 'Read'), ('tool_call', self.now - 30, 'Read'), ('tool_call', self.now - 20, 'Bash')])
        data = self.data()
        self.assertEqual(data['tools']['top'][0], {'tool': 'Read', 'count': 2})
        self.assertEqual(data['tools']['total'], 3)
        self.assertEqual(sum(map(sum, data['week'])), 4)

    def test_tasks_have_durations(self):
        self.add('a', 'claude_code:x', [('user', self.now - 100, None), ('assistant', self.now - 40, None), ('turn_end', self.now - 39, None)])
        tasks = self.data()['tasks']
        self.assertEqual(tasks['count'], 1)
        self.assertIsNotNone(tasks['median_s'])

    def test_trend_matches_the_totals_and_lands_in_the_newest_cell(self):
        self.add('a', COMM, [('user', self.now - 100, 'telegram'), ('assistant', self.now - 40, 'telegram')])
        self.add('a', 'claude_code:x', [('user', self.now - 50, None), ('assistant', self.now - 40, None, 'Claude AI usage limit reached|1790000000'),
                                        ('assistant', self.now - 30, None, 'API Error: 529 overloaded_error'), ('error', self.now - 20, None)])
        data = self.data()
        trend = data['trend']
        self.assertEqual({len(values) for values in trend.values()}, {24})
        self.assertEqual((sum(trend['limit_messages']), sum(trend['api_errors']), sum(trend['tool_errors']), sum(trend['replies'])), (1, 1, 1, 1))
        self.assertEqual(sum(trend['tasks']), data['tasks']['count'])
        self.assertEqual((trend['replies'][23], trend['reply_median_s'][23], trend['reply_median_s'][0]), (1, 60, None))

    def test_nothing_recorded_is_empty_not_an_error(self):
        data = self.data()
        self.assertEqual(data['replies']['all'], {'replies': 0, 'median_s': None, 'p90_s': None})
        self.assertEqual(data['agents'], [])


if __name__ == '__main__':
    unittest.main()
