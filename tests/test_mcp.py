import json
import tempfile
import unittest
from pathlib import Path

from hq.control.audit import ControlLog
from hq.mcp import AgentKeys, McpServer

FLEET = {'agents': [
    {'host': 'ct300-worker', 'label': 'Worker', 'owner': 'Worker', 'group': 'Staff', 'status': 'idle', 'last_seen': 100, 'messages_in': 3, 'messages_out': 2, 'agent_messages': 0, 'tasks': 1, 'errors': 0, 'tokens': 5, 'value_usd': 1.5},
    {'host': 'ct203-manager', 'label': 'Manager Agent', 'owner': 'Morgan', 'group': 'Management', 'status': 'working', 'last_seen': 200, 'messages_in': 9, 'messages_out': 8, 'agent_messages': 4, 'tasks': 2, 'errors': 1, 'tokens': 9, 'value_usd': 3.0},
]}


class Setup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.now = [1000.0]
        self.keys = AgentKeys(self.dir / 'agent-keys.json', clock=lambda: self.now[0])
        self.log = ControlLog(self.dir / 'log.jsonl')
        self.sent, self.restarted = [], []
        providers = {
            'fleet': lambda name: FLEET,
            'infra': lambda: {'guests': [{'host': 'ct300-worker', 'vmid': 300, 'status': 'running', 'mem': 1, 'maxmem': 8}], 'nodes': [], 'storage': [], 'totals': {}, 'cluster': {}},
            'watch': lambda: {'checked_at': 5, 'warnings': [{'kind': 'disk', 'host': 'ct300-worker', 'text': 'Worker: disk 92% full', 'since': 1, 'label': 'Worker'}]},
            'updates': lambda: {'agents': {'ct300-worker': {'claude': '2.1', 'zylos': '1.0', 'claude_behind': True}}, 'totals': {}, 'latest': {}},
            'send': lambda host, text: self.sent.append((host, text)),
            'restart': lambda host: self.restarted.append(host),
        }
        self.mcp = McpServer(self.keys, providers, self.log)

    def call(self, key, method, params=None, mid=1):
        message = {'jsonrpc': '2.0', 'method': method, **({'id': mid} if mid is not None else {}), **({'params': params} if params is not None else {})}
        return self.mcp.handle(message, key)

    def tool(self, key, name, **args):
        status, answer = self.call(key, 'tools/call', {'name': name, 'arguments': args})
        return answer['result'] if 'result' in answer else answer


class KeyTests(Setup):
    def test_keys_are_stored_as_hashes_and_checked(self):
        token, key = self.keys.create('Manager Agent', [])
        self.assertTrue(token.startswith('hqa_'))
        self.assertNotIn(token, (self.dir / 'agent-keys.json').read_text())
        self.assertNotIn('hash', json.dumps(self.keys.public()))
        self.assertEqual(self.keys.check(f'Bearer {token}')['name'], 'Manager Agent')
        self.assertIsNone(self.keys.check('Bearer hqa_wrong'))
        self.assertIsNone(self.keys.check(token))  # needs the Bearer form
        self.keys.revoke(key['id'])
        self.assertIsNone(self.keys.check(f'Bearer {token}'))

    def test_bad_names_and_scopes_are_refused(self):
        for name, scopes in (('', []), ('x' * 80, []), ('ok', ['root'])):
            with self.assertRaises(ValueError):
                self.keys.create(name, scopes)

    def test_rate_limit_and_use_counts(self):
        token, _ = self.keys.create('busy', [])
        for _ in range(120):
            self.assertFalse(self.keys.check(f'Bearer {token}').get('limited'))
        self.assertTrue(self.keys.check(f'Bearer {token}')['limited'])
        self.now[0] += 61
        self.assertFalse(self.keys.check(f'Bearer {token}').get('limited'))
        self.assertEqual(self.keys.public()[0]['uses'], 121)


class ProtocolTests(Setup):
    def test_initialize_list_and_ping(self):
        _, key = self.keys.create('reader', [])
        status, answer = self.call(key, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'claude-code'}})
        self.assertEqual((status, answer['result']['protocolVersion'], answer['result']['serverInfo']['name']), (200, '2025-03-26', 'agent-hq'))
        self.assertEqual(self.call(key, 'notifications/initialized', mid=None), (202, None))
        names = [t['name'] for t in self.call(key, 'tools/list')[1]['result']['tools']]
        self.assertEqual(names, ['list_agents', 'get_agent', 'health_warnings', 'proxmox_capacity', 'update_status', 'router_status'])
        self.assertEqual(self.call(key, 'ping')[1]['result'], {})
        self.assertEqual(self.call(key, 'nope')[1]['error']['code'], -32601)
        self.assertEqual(self.mcp.handle({'method': 'x'}, key)[0], 400)

    def test_read_tools(self):
        _, key = self.keys.create('reader', [])
        agents = self.tool(key, 'list_agents')['structuredContent']['agents']
        self.assertEqual([a['label'] for a in agents], ['Worker', 'Manager Agent'])
        roy = self.tool(key, 'get_agent', host='ct300-worker')['structuredContent']
        self.assertEqual((roy['container']['vmid'], roy['versions']['claude_behind'], roy['warnings']), (300, True, ['Worker: disk 92% full']))
        self.assertEqual(self.tool(key, 'plan_limits')['error']['code'], -32602)  # retired with the login pool
        self.assertEqual(len(self.tool(key, 'health_warnings')['structuredContent']['warnings']), 1)
        missing = self.tool(key, 'get_agent', host='ct999-nobody')
        self.assertTrue(missing['isError'])

    def test_actions_need_their_permission(self):
        _, reader = self.keys.create('reader', [])
        refused = self.tool(reader, 'send_message', host='ct300-worker', text='hello')
        self.assertEqual(refused['error']['code'], -32602)
        self.assertEqual(self.sent, [])
        _, writer = self.keys.create('Manager Agent', ['message', 'control'])
        self.assertFalse(self.tool(writer, 'send_message', host='ct300-worker', text='Stock report is ready')['isError'])
        self.assertEqual(self.sent, [('ct300-worker', '[Message from Manager Agent via HQ, not from a person] Stock report is ready')])
        self.assertFalse(self.tool(writer, 'restart_claude', host='ct300-worker')['isError'])
        self.assertEqual(self.restarted, ['ct300-worker'])

    def test_a_key_cant_pretend_to_be_someone_else_and_message_text_isnt_logged(self):
        _, writer = self.keys.create('Manager Agent', ['message'])
        self.tool(writer, 'send_message', host='ct300-worker', text='secret plan', sender='Taylor (a person)')
        self.assertIn('Message from Manager Agent via HQ', self.sent[0][1])
        self.assertNotIn('secret plan', (self.dir / 'log.jsonl').read_text())
        entry = self.log.recent()[0]
        self.assertEqual((entry['action'], entry['tool'], entry['key'], entry['host']), ('mcp_call', 'send_message', 'Manager Agent', 'ct300-worker'))


class McpOverHttpTests(unittest.TestCase):
    """The /mcp route through HQ's real server: key check, HTTPS, origins, and a tool call."""

    def setUp(self):
        import threading
        from hq.collector import Collector
        from hq.config import load_config
        from hq.server import make_server
        from hq.store import Store
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        store = Store(self.root / 'hq.db')
        self.addCleanup(store.close)
        cfg = load_config(Path(__file__).resolve().parent.parent / 'config')
        self.server, self.master = make_server(store, Collector(store, cfg, []), cfg, self.root, port=0, public_url='https://hq.example.test')
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown(); self.server.server_close(); thread.join()
        self.addCleanup(stop)
        self.token, _ = AgentKeys(self.root / 'agent-keys.json').create('tester', [])

    def post(self, body, key=None, origin=None, proto='https', method='POST'):
        import http.client
        from contextlib import closing
        headers = {'Host': 'hq.example.test', 'Content-Type': 'application/json', 'X-Forwarded-Proto': proto}
        if key:
            headers['Authorization'] = f'Bearer {key}'
        if origin:
            headers['Origin'] = origin
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port)) as conn:
            conn.request(method, '/mcp', None if body is None else json.dumps(body), headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None

    def test_needs_a_key_https_and_no_foreign_origin(self):
        ping = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
        self.assertEqual(self.post(ping)[0], 401)
        self.assertEqual(self.post(ping, key='hqa_guess')[0], 401)
        self.assertEqual(self.post(ping, key=self.master)[0], 401)  # the browser's token is not an agent key
        self.assertEqual(self.post(ping, key=self.token, proto='http')[0], 403)
        self.assertEqual(self.post(ping, key=self.token, origin='https://evil.example')[0], 403)
        self.assertEqual(self.post(ping, key=self.token), (200, {'jsonrpc': '2.0', 'id': 1, 'result': {}}))
        self.assertEqual(self.post(None, key=self.token, method='GET')[0], 405)

    def test_a_tool_call_end_to_end(self):
        status, answer = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'list_agents', 'arguments': {}}}, key=self.token)
        self.assertEqual(status, 200)
        self.assertIn('agents', answer['result']['structuredContent'])
        self.assertEqual(self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, key=self.token), (202, None))


if __name__ == '__main__':
    unittest.main()
