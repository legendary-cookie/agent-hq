import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from hq.office import kind_of_tool, office_snapshot
from hq.records import Batch, EventRec, SessionRec
from hq.store import Store

META = {'brand': None, 'agents': {'one': {'label': 'One', 'owner': 'Ann', 'group': 'Staff'}, 'two': {'label': 'Two', 'group': 'Staff'}}, 'accounts': []}
COMM = 'zylos:comm'


class OfficeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name).resolve() / 'hq.db')
        self.addCleanup(self.store.close)
        self.now = time.time()

    def add(self, host, sid, events, title='Weekly report', ended=None):
        full = f'{host}/{sid}'
        stamps = [e[1] for e in events]
        recs = [EventRec(full, f'e{i}', ts, kind, tool, None, None, None, None, None, False, None) for i, (kind, ts, tool) in enumerate(events)]
        session = SessionRec(full, 'zylos' if sid == COMM else 'claude_code', None, None, None, title, min(stamps), max(stamps), ended, None, 'r')
        self.store.apply(Batch([session], recs, [], []), lambda *a: 'a', (), host=host)

    def snapshot(self, health=None):
        with closing(self.store.conn()) as conn:
            return office_snapshot(conn, self.now, health or {}, META)

    def agent(self, data, host):
        return next(a for a in data['agents'] if a['host'] == host)

    def test_tools_map_to_animations(self):
        for tool, kind in (('WebSearch', 'search'), ('WebFetch', 'search'), ('Read', 'read'), ('Grep', 'read'), ('Write', 'draft'), ('Edit', 'draft'),
                           ('Bash', 'command'), ('mcp__shopify__list-orders', 'orders'), ('mcp__google-drive__read_file', 'drive'), ('Unknown', 'think'), (None, 'think')):
            self.assertEqual(kind_of_tool(tool), kind, tool)

    def test_newest_event_is_the_activity(self):
        self.add('one', 'c:a', [('tool_call', self.now - 50, 'Read'), ('tool_call', self.now - 5, 'WebSearch')])
        activity = self.agent(self.snapshot(), 'one')['activity']
        self.assertEqual((activity['kind'], activity['title']), ('search', 'Weekly report'))

    def test_earlier_lists_each_other_piece_of_work_once(self):
        self.add('one', 'c:a', [('tool_call', self.now - 90, 'Read'), ('tool_call', self.now - 50, 'Write')], title='Stock report')
        self.add('one', 'c:b', [('tool_call', self.now - 40, 'Read'), ('tool_call', self.now - 30, 'Edit')], title='Board notes')
        self.add('one', 'c:c', [('tool_call', self.now - 5, 'WebSearch')], title='Prices')
        self.assertEqual([item['title'] for item in self.agent(self.snapshot(), 'one')['earlier']], ['Board notes', 'Stock report'])

    def test_a_reply_shows_the_tool_used_just_before_it(self):
        self.add('one', 'c:a', [('tool_call', self.now - 30, 'WebSearch'), ('assistant', self.now - 5, None)])
        activity = self.agent(self.snapshot(), 'one')['activity']
        self.assertEqual((activity['kind'], activity['at']), ('search', self.now - 5))

    def test_an_old_tool_or_another_session_is_not_borrowed(self):
        self.add('one', 'c:a', [('tool_call', self.now - 900, 'WebSearch'), ('assistant', self.now - 5, None)])
        self.add('two', 'c:b', [('tool_call', self.now - 30, 'WebSearch')])
        self.add('two', 'c:c', [('assistant', self.now - 5, None)])
        data = self.snapshot()
        self.assertEqual(self.agent(data, 'one')['activity']['kind'], 'think')
        self.assertEqual(self.agent(data, 'two')['activity']['kind'], 'think')

    def test_housekeeping_channels_are_not_activity(self):
        self.add('one', COMM, [('user', self.now - 5, 'system')])
        self.assertIsNone(self.agent(self.snapshot(), 'one')['activity'])

    def test_scheduler_and_people_channels(self):
        self.add('one', COMM, [('user', self.now - 5, 'scheduler')])
        self.add('two', COMM, [('user', self.now - 5, 'telegram')])
        data = self.snapshot()
        self.assertEqual(self.agent(data, 'one')['activity']['kind'], 'schedule')
        self.assertEqual(self.agent(data, 'two')['activity']['kind'], 'message')

    def test_a_send_and_nearby_receive_are_an_exchange(self):
        self.add('one', COMM, [('assistant', self.now - 30, 'hxa-connect')])
        self.add('two', COMM, [('user', self.now - 28, 'hxa-connect')])
        data = self.snapshot()
        self.assertEqual([(e['from'], e['to']) for e in data['exchanges']], [('one', 'two')])
        self.assertEqual(data['meetings'], [])

    def test_a_distant_receive_is_not_an_exchange(self):
        self.add('one', COMM, [('assistant', self.now - 200, 'hxa-connect')])
        self.add('two', COMM, [('user', self.now - 100, 'hxa-connect')])
        self.assertEqual(self.snapshot()['exchanges'], [])

    def test_other_channels_never_form_exchanges(self):
        self.add('one', COMM, [('assistant', self.now - 30, 'telegram')])
        self.add('two', COMM, [('user', self.now - 28, 'telegram')])
        self.assertEqual(self.snapshot()['exchanges'], [])

    def test_a_receive_is_claimed_only_once(self):
        self.add('one', COMM, [('assistant', self.now - 30, 'hxa-connect'), ('assistant', self.now - 29, 'hxa-connect')])
        self.add('two', COMM, [('user', self.now - 28, 'hxa-connect')])
        self.assertEqual(len(self.snapshot()['exchanges']), 1)

    def test_repeated_exchanges_are_a_meeting(self):
        self.add('one', COMM, [('assistant', self.now - t, 'hxa-connect') for t in (300, 200, 100)])
        self.add('two', COMM, [('user', self.now - t + 1, 'hxa-connect') for t in (300, 200, 100)])
        meetings = self.snapshot()['meetings']
        self.assertEqual([m['hosts'] for m in meetings], [['one', 'two']])
        self.assertEqual(meetings[0]['count'], 3)

    def test_three_agents_talking_are_one_meeting(self):
        self.add('one', COMM, [('assistant', self.now - t, 'hxa-connect') for t in (500, 400, 300, 200, 100, 50)])
        self.add('two', COMM, [('user', self.now - t + 1, 'hxa-connect') for t in (500, 400, 300)])
        self.add('three', COMM, [('user', self.now - t + 1, 'hxa-connect') for t in (200, 100, 50)])
        self.assertEqual([m['hosts'] for m in self.snapshot()['meetings']], [['one', 'three', 'two']])

    def test_unreachable_agent_needs_attention_and_keeps_its_labels(self):
        agent = self.agent(self.snapshot({'one': {'state': 'partial', 'error': 'ssh failed'}}), 'one')
        self.assertEqual((agent['status'], agent['label'], agent['owner'], agent['error']), ('attention', 'One', 'Ann', 'ssh failed'))

    def test_long_titles_are_clipped(self):
        self.add('one', 'c:a', [('tool_call', self.now - 5, 'Read')], title='x ' * 100)
        self.assertLessEqual(len(self.agent(self.snapshot(), 'one')['activity']['title']), 90)

    def test_last_message_from_a_person_ignores_agents_and_housekeeping(self):
        self.add('one', COMM, [('user', self.now - 100, 'telegram'), ('user', self.now - 20, 'hxa-connect'), ('user', self.now - 10, 'system')])
        self.assertEqual(self.agent(self.snapshot(), 'one')['last_from_person'], self.now - 100)

    def test_recent_limit_and_api_errors_are_flagged(self):
        full = 'one/c:a'
        recs = [EventRec(full, 'l', self.now - 600, 'assistant', None, 'Claude AI usage limit reached|1790000000', None, None, None, None, False, None),
                EventRec(full, 'o', self.now - 20 * 60, 'assistant', None, 'API Error: 529 overloaded_error', None, None, None, None, False, None)]
        session = SessionRec(full, 'claude_code', None, None, None, 't', self.now - 1200, self.now - 600, None, None, 'r')
        self.store.apply(Batch([session], recs, [], []), lambda *a: 'a', (), host='one')
        agent = self.agent(self.snapshot(), 'one')
        self.assertEqual(agent['limit_at'], self.now - 600)
        self.assertIsNone(agent['api_error_at'])  # 20 minutes ago is older than the 15 minutes shown

    def test_feed_is_newest_first(self):
        self.add('one', 'c:a', [('tool_call', self.now - 50, 'Read')])
        self.add('two', 'c:b', [('tool_call', self.now - 5, 'Read')])
        self.assertEqual([item['host'] for item in self.snapshot()['feed']], ['two', 'one'])


if __name__ == '__main__':
    unittest.main()
