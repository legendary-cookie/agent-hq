import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hq.config import Config
from hq.store import Store
from hq.run.service import RunService, RunError


class FakeLauncher:
    name = 'fake'
    exe = 'fake'
    harness = 'fake'
    tested = True
    def levels(self): return {'edit', 'full_auto', 'read_only'}


class FakeRunner:
    def __init__(self, store, *args, **kwargs):
        self.store = store
        self.on_event = kwargs['on_event']
        self.executables = {'fake': Path('fake')}
        self.rings = {}
    def start(self, req, profile):
        rec = dict(id='run_1', tool=req.tool, project=req.project, workdir=str(ROOT), level=req.level,
                   guard_mode=req.guard_mode, status='starting', prompt_preview=req.prompt, started_at=1, time_limit_s=1800)
        self.store.insert_run(rec); self.rings['run_1'] = ['hello']
        self.on_event({'type':'run', 'id':'run_1', 'status':'starting'})
        return self.store.get_run('run_1')
    def output(self, rid): return self.rings.get(rid)
    def stop(self, rid): self.store.update_run(rid, status='stopped')


class RunServiceTests(unittest.TestCase):
    def setUp(self):
        global ROOT
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); ROOT = Path(tmp.name)
        self.store = Store(ROOT/'db'); self.addCleanup(self.store.close)
        (ROOT/'run.json').write_text(json.dumps({'run_mode': True}))
        (ROOT/'projects.json').write_text(json.dumps({'projects':[{'name':'p','path':str(ROOT)}]}))
        self.cfg = Config({}, [], {}, {'thresholds':[80,95], 'toast':False}, 'isometric', 'coding')
        self.service = RunService(self.store,self.cfg,ROOT,ROOT,lambda _: [], launchers={'fake':FakeLauncher()}, runner_factory=FakeRunner)
        self.body = {'tool':'fake','project':'p','prompt':'hello'}
    def test_options_when_off(self):
        self.service.run_cfg.run_mode = False
        self.assertFalse(self.service.options()['run_mode'])
    def test_prepare_403_when_off(self):
        self.service.run_cfg.run_mode = False
        with self.assertRaises(RunError) as e: self.service.prepare(self.body)
        self.assertEqual(e.exception.status, 403)
    def test_start_without_nonce_403(self):
        with self.assertRaises(RunError) as e: self.service.start(self.body)
        self.assertEqual(e.exception.status,403)
    def test_start_replayed_nonce_403(self):
        data = self.body | {'nonce':self.service.prepare(self.body)['nonce']}
        self.service.start(data)
        with self.assertRaises(RunError) as e: self.service.start(data)
        self.assertEqual(e.exception.status,403)
    def test_start_tampered_prompt_403(self):
        nonce = self.service.prepare(self.body)['nonce']
        with self.assertRaises(RunError) as e: self.service.start(self.body | {'prompt':'different','nonce':nonce})
        self.assertEqual(e.exception.status,403)
    def test_start_ok_201_then_list_and_get(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.assertEqual(self.service.get(rec['id'])['id'], rec['id'])
        self.assertEqual(len(self.service.list()),1)
    def test_stop_ok(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.service.stop(rec['id']); self.assertEqual(self.store.get_run(rec['id'])['status'],'stopped')
    def test_output_ring(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.assertEqual(self.service.output(rec['id']), ['hello'])
    def test_output_410_for_run_from_before_restart(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.service.runner.rings.clear()
        with self.assertRaises(RunError) as e: self.service.output(rec['id'])
        self.assertEqual(e.exception.status,410)
    def test_full_auto_start_recorded_with_level(self):
        body = self.body | {'level':'full_auto'}
        self.assertEqual(self.service.start(body | {'nonce':self.service.prepare(body)['nonce']})['level'],'full_auto')
    def test_untested_tool_hidden_unless_show_untested(self):
        self.service.launchers['fake'].tested = False
        self.assertNotIn('fake',self.service.options()['tools'])
        self.service.run_cfg.show_untested = True
        self.assertIn('fake',self.service.options()['tools'])
    def test_merge_before_diff_409(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.store.update_run(rec['id'],status='done',branch='branch',base_commit='base')
        with self.assertRaises(RunError) as e: self.service.merge(rec['id'])
        self.assertEqual(e.exception.status,409)
    def test_merge_while_running_409(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        with self.assertRaises(RunError) as e: self.service.merge(rec['id'])
        self.assertEqual(e.exception.status,409)
    def test_diff_then_merge_ok(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.store.update_run(rec['id'],status='done',branch='branch',base_commit='base')
        with patch('hq.run.service.workspace.diff',return_value=('patch',False)), patch('hq.run.service.workspace.merge',return_value='merged'):
            self.assertEqual(self.service.diff(rec['id'])['diff'],'patch')
            self.service.merge(rec['id'])
        self.assertEqual(self.store.get_run(rec['id'])['merge_state'],'merged')


class RunHttpTests(unittest.TestCase):
    def setUp(self):
        RunServiceTests.setUp(self)
        import threading
        from hq.collector import Collector
        from hq.server import make_server
        self.collector = Collector(self.store, self.cfg, [])
        self.server, self.token = make_server(self.store, self.collector, self.cfg, ROOT, port=0, run_service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close_server)
    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
    def req(self, path, method='GET', data=None, origin=None):
        import http.client
        port = self.server.server_port
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3); self.addCleanup(conn.close)
        headers = {'X-HQ-Token':self.token}
        if method != 'GET': headers['Origin'] = origin or f'http://127.0.0.1:{port}'
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    def test_wrong_origin_403(self):
        self.assertEqual(self.req('/api/runs/prepare', 'POST', self.body, 'http://evil.com')[0],403)
    def test_start_ok_201_then_list_and_get(self):
        status, prepared = self.req('/api/runs/prepare', 'POST', self.body)
        self.assertEqual(status,200)
        status, run = self.req('/api/runs', 'POST', self.body | {'nonce':prepared['nonce']})
        self.assertEqual(status,201)
        self.assertEqual(self.req('/api/runs')[1][0]['id'], run['id'])
        self.assertEqual(self.req('/api/runs/'+run['id'])[1]['id'],run['id'])
    def test_sessions_carry_launched_run_id(self):
        run = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        with self.store._lock, self.store._writer as conn:
            conn.execute("insert into sessions(id,harness,provider,started_at,last_event_at) values ('s','h','p',1,1)")
        self.store.update_run(run['id'], session_id='s')
        self.assertEqual(self.req('/api/sessions')[1][0]['launched_run_id'],run['id'])
        self.assertEqual(self.req('/api/sessions/s')[1]['session']['launched_run_id'],run['id'])
    def test_settings_put_cannot_enable_run_mode(self):
        self.service.run_cfg.run_mode = False
        settings = self.req('/api/settings')[1]; settings['run_mode'] = True
        self.assertEqual(self.req('/api/settings','PUT',settings)[0],200)
        self.assertEqual(self.req('/api/runs/prepare','POST',self.body), (403, {'error':'run mode is off'}))
    def test_live_feed_gets_run_event(self):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3); self.addCleanup(conn.close)
        conn.request('GET','/api/stream?token='+self.token); response=conn.getresponse()
        self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.assertEqual(response.readline(), b'event: changed\n')
        self.assertEqual(json.loads(response.readline().removeprefix(b'data: '))['type'],'run')


class RunExtraTests(RunServiceTests):
    def test_provider_usage_failure_counted_and_mapped(self):
        import subprocess
        self.service.pu_dir = ROOT
        with patch('hq.run.service.subprocess.run', side_effect=subprocess.TimeoutExpired('pu', 20)) as call:
            self.service.pu_log('agy', 'model', 'hq-run', 'ok', 1.5, None)
        self.assertEqual(self.service.pu_failures, 1)
        args, kwargs = call.call_args
        self.assertEqual(args[0][2:6], ['log', 'antigravity', 'model', 'hq-run'])
        self.assertEqual(kwargs['timeout'], 20)
    def test_guard_is_rechecked_after_prepare(self):
        from hq.run.guard import Verdict
        nonce = self.service.prepare(self.body)['nonce']
        with patch('hq.run.service.guard.check', return_value=Verdict(False,['blocked'],[],None,None,None)):
            with self.assertRaises(RunError) as exc: self.service.start(self.body | {'nonce':nonce})
        self.assertEqual(exc.exception.status,409)
    def test_no_branch_diff_and_discard_while_running(self):
        rec = self.service.start(self.body | {'nonce':self.service.prepare(self.body)['nonce']})
        self.assertEqual(self.service.diff(rec['id']), {'diff':'', 'truncated':False, 'branch':None})
        with self.assertRaises(RunError) as exc: self.service.discard(rec['id'])
        self.assertEqual(exc.exception.status,409)
