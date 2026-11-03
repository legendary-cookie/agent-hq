import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hq.config import Config
from hq.run.runner import NotIdle
from hq.run.service import RunError, RunService
from hq.store import Store
from tests.test_run_api import FakeLauncher, FakeRunner


class SteerApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.store = Store(self.root / 'db'); self.addCleanup(self.store.close)
        (self.root / 'run.json').write_text(json.dumps({'run_mode': True, 'approval_timeout_min': 2}))
        self.cfg = Config({}, [], {}, {'thresholds': [], 'toast': False}, 'isometric', 'coding')
        self.service = RunService(self.store, self.cfg, self.root, self.root, lambda _: [],
                                  launchers={'fake': FakeLauncher()}, runner_factory=FakeRunner)
        for rid in ('run_1', 'run_2'):
            self.store.insert_run(dict(id=rid, tool='fake', project='p', workdir=str(self.root), level='edit',
                                       guard_mode='warn', status='idle', prompt_preview='', started_at=1, time_limit_s=60))

    def approval(self):
        self.addCleanup(self.service.approvals.close_run, 'run_1')
        return self.service.approvals.open('run_1', 'req', 'Bash', {'command': 'echo hi'}, lambda *a: None)

    def test_answer_other_runs_request_404(self):
        self.approval()
        with self.assertRaises(RunError) as exc: self.service.answer('run_2:req', {'decision': 'allow_once'})
        self.assertEqual(exc.exception.status, 404)

    def test_answer_twice_409(self):
        aid = self.approval().id
        self.service.answer(aid, {'decision': 'allow_once'})
        with self.assertRaises(RunError) as exc: self.service.answer(aid, {'decision': 'deny'})
        self.assertEqual(exc.exception.status, 409)

    def test_message_requires_nonce_bound_to_text(self):
        nonce = self.service.message_prepare('run_1', {'text': 'hello'})['nonce']
        with self.assertRaises(RunError) as exc: self.service.message('run_1', {'text': 'changed', 'nonce': nonce})
        self.assertEqual(exc.exception.status, 403)
        nonce = self.service.message_prepare('run_1', {'text': 'hello'})['nonce']
        with patch.object(self.service.runner, 'send', create=True) as send:
            self.service.message('run_1', {'text': 'hello', 'nonce': nonce})
            send.assert_called_once_with('run_1', 'hello')

    def test_message_while_working_409(self):
        nonce = self.service.message_prepare('run_1', {'text': 'hello'})['nonce']
        with patch.object(self.service.runner, 'send', side_effect=NotIdle('working'), create=True):
            with self.assertRaises(RunError) as exc: self.service.message('run_1', {'text': 'hello', 'nonce': nonce})
        self.assertEqual(exc.exception.status, 409)

    def test_diff_allowed_when_idle(self):
        self.store.update_run('run_1', branch='branch', base_commit='base')
        with patch.object(self.service, '_project', return_value=self.root), patch('hq.run.service.workspace.diff', return_value=('patch', False)), patch('hq.run.service.workspace.revision', return_value='abc'):
            self.assertEqual(self.service.diff('run_1')['diff'], 'patch')
        self.store.update_run('run_1', status='needs_you')
        with self.assertRaises(RunError): self.service.diff('run_1')

    def test_feed_gets_approval_event(self):
        events = []; self.service.on_event = events.append
        approval = self.approval()
        self.assertEqual(events[-1], {'type': 'approval', 'id': approval.id, 'run_id': 'run_1', 'state': 'waiting'})
        self.assertEqual(self.service.approvals.timeout_s, 120)
        self.assertEqual(self.service.approvals_list('waiting')[0]['id'], approval.id)


class SteerHttpTests(unittest.TestCase):
    def setUp(self):
        SteerApiTests.setUp(self)
        import threading
        from hq.collector import Collector
        from hq.server import make_server
        self.server, self.token = make_server(self.store, Collector(self.store, self.cfg, []), self.cfg, self.root, port=0, run_service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_answer_requires_run_key(self):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3); self.addCleanup(conn.close)
        conn.request('POST', '/api/approvals/run_1%3Areq', json.dumps({'decision': 'deny'}),
                     {'X-HQ-Token': self.token, 'Origin': f'http://127.0.0.1:{self.server.server_port}'})
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 403)

    def test_http_message_and_approval_routes(self):
        import http.client
        port = self.server.server_port
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3); self.addCleanup(conn.close)
        origin = f'http://127.0.0.1:{port}'
        def call(method, path, data=None, headers=None):
            conn.request(method, path, json.dumps(data) if data is not None else None, headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        _, exchange = call('POST', '/api/exchange', {'code': self.server.bootstrap_code}, {'Origin': origin})
        headers = {'Origin': origin, 'X-HQ-Token': self.token, 'X-HQ-Run-Key': exchange['run_key']}
        approval = self.service.approvals.open('run_1', 'req', 'Bash', {'command': 'echo hi'}, lambda *a: None)
        self.addCleanup(self.service.approvals.close_run, 'run_1')
        self.assertEqual(call('GET', '/api/approvals?state=waiting', headers=headers)[1][0]['id'], approval.id)
        self.assertEqual(call('POST', '/api/approvals/run_2%3Areq', {'decision': 'deny'}, headers)[0], 404)
        self.assertEqual(call('POST', '/api/approvals/run_1%3Areq', {'decision': 'deny'}, headers)[0], 200)
        self.assertEqual(call('POST', '/api/approvals/run_1%3Areq', {'decision': 'deny'}, headers)[0], 409)
        status, prepared = call('POST', '/api/runs/run_1/message/prepare', {'text': 'hi'}, headers)
        self.assertEqual(status, 200)
        with patch.object(self.service.runner, 'send', create=True):
            self.assertEqual(call('POST', '/api/runs/run_1/message', {'text': 'hi', 'nonce': prepared['nonce']}, headers)[0], 202)
