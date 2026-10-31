from contextlib import closing
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from hq.adapters.base import iter_files
from hq.adapters.claude_code import ClaudeCodeAdapter
from hq.collector import Collector
from hq.config import Account, Config, SourceCfg, _path, ForbiddenPathError, save_config
from hq.kpis import resolve_range, _billing
from hq.limits import recommend_bridge
from hq.records import Batch, EventRec, SessionRec
from hq.server import make_server, validate_settings
from hq.store import Store

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = Store(self.root/'hq.db'); self.addCleanup(self.store.close)
        self.cfg = Config({}, [Account('a','p','A',None,None,1,[],{})], {}, {'thresholds':[80], 'toast':False}, 'isometric','coding')

    def test_api_token_and_immutable_sources(self):
        save_config(self.root,self.cfg); col=Collector(self.store,self.cfg,[])
        server,token=make_server(self.store,col,self.cfg,self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(),server.server_close(),thread.join()))
        def request(path, method='GET', body=None, headers=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            c.request(method,path,json.dumps(body) if body is not None else None,headers or {}); r=c.getresponse(); data=r.read(); c.close(); return r.status,data
        status,body=request('/'); self.assertNotIn(token.encode(),body)
        self.assertEqual(request('/api/settings')[0],403)
        h={'X-HQ-Token':token,'Origin':f'http://127.0.0.1:{server.server_port}'}
        data=json.loads(request('/api/settings',headers=h)[1]); data['sources']['x']={'enabled':True,'paths':[]}
        self.assertEqual(request('/api/settings','PUT',data,h)[0],400)

    def test_forbidden_resolved_globs_and_walk(self):
        secret=self.root/'secrets'; secret.mkdir(); (secret/'x.jsonl').write_text('{}\n')
        (self.root/'auth.json').write_text('{}'); (self.root/'link').symlink_to(secret, target_is_directory=True)
        for value in [str(self.root/'auth.json.'),str(self.root/'auth.json::$DATA'),str(self.root/'link'/'x.jsonl'),str(self.root/'.credentials.json')]:
            with self.subTest(value=value), self.assertRaises(ForbiddenPathError): _path(value)
        for value in [str(self.root/'secret?'/'x.jsonl'),str(self.root/'secre[t]s'),str(self.root/'*.json'),str(self.root)]:
            self.assertEqual(list(iter_files(SourceCfg(True,[_path(value)]),'**/*.jsonl')),[])

    def test_failed_apply_preserves_cursor_and_collector_survives(self):
        logs=self.root/'logs'; logs.mkdir(); path=logs/'s.jsonl'
        path.write_text(json.dumps({'type':'user','sessionId':'s','timestamp':'2026-10-30T10:00:00Z','uuid':'u','message':{'role':'user','content':'hi'}})+'\n')
        self.cfg.sources={'claude_code':SourceCfg(True,[logs])}; self.cfg.accounts[0].match=[{'harness':5}]
        col=Collector(self.store,self.cfg,[ClaudeCodeAdapter()]); col.run_once()
        self.assertEqual(col.health()['claude_code']['state'],'partial'); self.assertIsNotNone(col.health()['claude_code']['last_error_at'])
        self.assertIsNone(self.store.get_cursor('claude_code',str(path)))
        self.cfg.accounts[0].match=[{'harness':'claude_code'}]; col.run_once()
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select count(*) from events').fetchone()[0],1)

    def test_invalid_settings_do_not_save(self):
        save_config(self.root,self.cfg); col=Collector(self.store,self.cfg,[]); server,token=make_server(self.store,col,self.cfg,self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); self.addCleanup(lambda: (server.shutdown(),server.server_close(),thread.join()))
        h={'X-HQ-Token':token,'Origin':f'http://127.0.0.1:{server.server_port}'}
        data={'sources':{},'accounts':[{'id':'a','provider':'p','label':'A','plan':None,'monthly_price_usd':-1,'billing_day':1,'match':[],'cap_tokens':{}}], 'prices':{},'alerts':{'thresholds':[80],'toast':False},'default_skin':'isometric','recommend_task_kind':'coding'}
        before=(self.root/'accounts.json').read_bytes(); c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3); c.request('PUT','/api/settings',json.dumps(data),h); r=c.getresponse(); r.read(); c.close()
        self.assertEqual(r.status,400); self.assertEqual(before,(self.root/'accounts.json').read_bytes())

    def test_settings_validation_fields(self):
        from copy import deepcopy
        valid={'sources':{},'accounts':[{'id':'a','provider':'p','label':'A','plan':None,'monthly_price_usd':0,'billing_day':1,'match':[{'harness':'x'}],'cap_tokens':{'day':0}}], 'prices':{'m':{'input':0,'output':1,'cache_read':2,'cache_write':3}},'alerts':{'thresholds':[0,100],'toast':False},'default_skin':'isometric','recommend_task_kind':'coding'}
        validate_settings(valid,{})
        changes=[('accounts', 'id', 3),('accounts','plan',False),('accounts','monthly_price_usd',-1),('accounts','billing_day',True),('accounts','match',[{'harness':5}]),('accounts','cap_tokens',{'day':-1})]
        for section,key,value in changes:
            data=deepcopy(valid); data[section][0][key]=value
            with self.subTest(key=key), self.assertRaises((ValueError,TypeError,KeyError)): validate_settings(data,{})
        for key,value in [('prices',{'m':{'input':-1,'output':0,'cache_read':0,'cache_write':0}}),('alerts',{'thresholds':[101],'toast':False}),('alerts',{'thresholds':[80],'toast':0}),('default_skin','bad'),('recommend_task_kind',None)]:
            data=deepcopy(valid); data[key]=value
            with self.subTest(key=key), self.assertRaises((ValueError,TypeError,KeyError)): validate_settings(data,{})

    def test_recommend_snapshot_and_exception(self):
        db=self.root/'usage.db'; sqlite3.connect(db).close()
        (self.root/'tracker.py').write_text('import sqlite3\ndef recommend(task_kind, db_path=None, providers_path=None):\n c=sqlite3.connect(db_path); c.execute("create table changed(x)"); c.close(); return [{"db":str(db_path)}]\n')
        result=recommend_bridge(self.root,'coding'); self.assertNotEqual(result[0]['db'],str(db))
        with closing(sqlite3.connect(db)) as c: self.assertEqual(c.execute("select count(*) from sqlite_master where name='changed'").fetchone()[0],0)
        (self.root/'tracker.py').write_text('def recommend(*a, **k): raise ZeroDivisionError("bad")\n')
        self.assertIsNone(recommend_bridge(self.root,'other'))

    @unittest.skipUnless(hasattr(time,'tzset'), 'tzset unavailable')
    def test_dst_boundaries_at_local_midnight(self):
        old=os.environ.get('TZ'); os.environ['TZ']='America/New_York'; time.tzset()
        try:
            r=resolve_range('month',datetime(2026,3,15,12).astimezone())
            for stamp in (r.start,r.end,r.prev_start,*_billing(datetime(2026,3,15,12).timestamp(),1)):
                self.assertEqual(datetime.fromtimestamp(stamp).hour,0)
        finally:
            if old is None: os.environ.pop('TZ',None)
            else: os.environ['TZ']=old
            time.tzset()

    def test_event_upsert_updates_tokens_only_when_changed(self):
        event=EventRec('s','x',1,'assistant',None,None,None,None,None,None,False,None); batch=Batch([], [event], [], [])
        self.assertTrue(self.store.apply(batch,lambda *a:'a')); self.assertFalse(self.store.apply(batch,lambda *a:'a'))
        self.assertTrue(self.store.apply(Batch([], [replace(event,tokens_in=42,model='m')], [], []),lambda *a:'a'))
        with closing(self.store.conn()) as c: self.assertEqual(c.execute('select tokens_in,model from events').fetchone(),(42,'m'))
