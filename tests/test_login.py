import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hq.auth import FREE_ATTEMPTS, SESSION_S, Locked, Login, prompt_for_password
from hq.collector import Collector
from hq.config import load_config
from hq.server import make_server
from hq.store import Store

ROOT = Path(__file__).resolve().parent.parent
GOOD = 'correct horse battery staple'
PUBLIC = 'https://hq.example.test'


class LoginUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'login.json'
        self.now = 1000.0
        self.login = Login(self.path, clock=lambda: self.now)
        self.login.set_password(GOOD)

    def test_password_is_not_stored(self):
        self.assertNotIn(GOOD, self.path.read_text())
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            self.login.set_password('short')

    def test_two_passwords_never_share_a_hash(self):
        first = json.loads(self.path.read_text())['hash']
        self.login.set_password(GOOD)
        self.assertNotEqual(first, json.loads(self.path.read_text())['hash'])

    def test_session_expires(self):
        token = self.login.sign_in(GOOD)
        self.assertTrue(self.login.valid(token))
        self.now += SESSION_S + 1
        self.assertFalse(self.login.valid(token))

    def test_session_survives_a_restart_but_not_sign_out(self):
        token = self.login.sign_in(GOOD)
        self.assertNotIn(token, self.login.sessions_path.read_text())
        restarted = Login(self.path, clock=lambda: self.now)
        self.assertTrue(restarted.valid(token))
        restarted.sign_out(token)
        self.assertFalse(Login(self.path, clock=lambda: self.now).valid(token))

    def test_damaged_sessions_file_just_signs_out(self):
        self.login.sessions_path.write_text('{not json')
        self.assertFalse(Login(self.path, clock=lambda: self.now).valid('anything'))

    def test_sign_out_ends_session(self):
        token = self.login.sign_in(GOOD)
        self.login.sign_out(token)
        self.assertFalse(self.login.valid(token))

    def test_changing_password_ends_sessions(self):
        token = self.login.sign_in(GOOD)
        self.login.set_password('another long password')
        os.utime(self.path, ns=(1, 1))
        self.assertFalse(self.login.valid(token))

    def test_repeated_failures_lock_even_the_right_password(self):
        for _ in range(FREE_ATTEMPTS):
            self.assertIsNone(self.login.sign_in('wrong password'))
        with self.assertRaises(Locked) as caught:
            self.login.sign_in(GOOD)
        self.assertEqual(caught.exception.retry_after, 31)
        self.now += 31
        self.assertTrue(self.login.sign_in(GOOD))

    def test_lock_grows_with_more_failures(self):
        for _ in range(FREE_ATTEMPTS):
            self.login.sign_in('wrong password')
        self.now += 31
        self.login.sign_in('wrong password')
        with self.assertRaises(Locked) as caught:
            self.login.sign_in('wrong password')
        self.assertEqual(caught.exception.retry_after, 61)

    def test_bad_inputs_are_just_wrong(self):
        for value in (None, 7, '', 'x' * 2000, ['a']):
            self.assertIsNone(self.login.sign_in(value))
        self.assertFalse(self.login.valid(None))
        self.assertFalse(self.login.valid(''))

    def test_prompt_needs_matching_passwords(self):
        path = self.path.with_name('other.json')
        with patch('getpass.getpass', side_effect=[GOOD, 'different password']), patch('builtins.print'):
            self.assertEqual(prompt_for_password(path), 1)
        self.assertFalse(path.exists())
        with patch('getpass.getpass', side_effect=[GOOD, GOOD]), patch('builtins.print'):
            self.assertEqual(prompt_for_password(path), 0)
        self.assertTrue(Login(path).sign_in(GOOD))


class LoginServerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        store = Store(self.root / 'hq.db')
        self.addCleanup(store.close)
        cfg = load_config(ROOT / 'config')
        self.server, self.master = make_server(store, Collector(store, cfg, []), cfg, self.root, port=0, public_url=PUBLIC)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(stop)
        Login(self.root / 'login.json').set_password(GOOD)

    def call(self, method, path, body=None, host='hq.example.test', origin=PUBLIC, proto='https', token=None):
        headers = {'Host': host, 'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        if proto:
            headers['X-Forwarded-Proto'] = proto
        if token:
            headers['X-HQ-Token'] = token
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port)) as conn:
            conn.request(method, path, None if body is None else json.dumps(body), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b'null')

    def sign_in(self):
        status, body = self.call('POST', '/api/login', {'password': GOOD})
        self.assertEqual(status, 200)
        return body['token']

    def test_session_token_opens_the_api_and_differs_from_master(self):
        token = self.sign_in()
        self.assertNotEqual(token, self.master)
        self.assertEqual(self.call('GET', '/api/limits', token=token)[0], 200)

    def test_api_is_closed_without_a_session(self):
        for token in (None, 'guess'):
            self.assertEqual(self.call('GET', '/api/limits', token=token)[0], 403)
            self.assertEqual(self.call('GET', '/api/sessions', token=token)[0], 403)
            self.assertEqual(self.call('PUT', '/api/settings', {}, token=token)[0], 403)

    def test_wrong_password(self):
        self.assertEqual(self.call('POST', '/api/login', {'password': 'nope nope nope'})[0], 401)

    def test_lockout_reaches_the_client(self):
        for _ in range(FREE_ATTEMPTS):
            self.call('POST', '/api/login', {'password': 'nope nope nope'})
        status, body = self.call('POST', '/api/login', {'password': GOOD})
        self.assertEqual(status, 429)
        self.assertGreater(body['retry_after'], 0)

    def test_sign_out(self):
        token = self.sign_in()
        self.assertEqual(self.call('POST', '/api/logout', {}, token=token)[0], 200)
        self.assertEqual(self.call('GET', '/api/limits', token=token)[0], 403)

    def test_other_sites_cannot_sign_in_or_write(self):
        token = self.sign_in()
        self.assertEqual(self.call('POST', '/api/login', {'password': GOOD}, origin='https://evil.test')[0], 403)
        self.assertEqual(self.call('POST', '/api/login', {'password': GOOD}, origin=None)[0], 403)
        self.assertEqual(self.call('POST', '/api/logout', {}, origin='https://evil.test', token=token)[0], 403)
        self.assertEqual(self.call('POST', '/api/login', {'password': GOOD}, origin='http://hq.example.test')[0], 403)

    def test_unknown_host_is_refused(self):
        token = self.sign_in()
        self.assertEqual(self.call('GET', '/api/limits', host='evil.test', origin=None, token=token)[0], 403)

    def test_sign_in_refuses_plain_http_through_the_public_name(self):
        self.assertEqual(self.call('POST', '/api/login', {'password': GOOD}, proto='http')[0], 403)
        self.assertEqual(self.call('POST', '/api/login', {'password': GOOD}, proto=None)[0], 403)

    def test_startup_code_is_refused_on_the_public_name(self):
        code = self.server.bootstrap_code
        self.assertEqual(self.call('POST', '/api/exchange', {'code': code})[0], 403)
        self.assertEqual(self.server.bootstrap_code, code)

    def test_startup_code_still_works_locally(self):
        host = f'127.0.0.1:{self.server.server_port}'
        status, body = self.call('POST', '/api/exchange', {'code': self.server.bootstrap_code}, host=host, origin=f'http://{host}', proto=None)
        self.assertEqual((status, body['token']), (200, self.master))

    def test_not_set_up_yet(self):
        (self.root / 'login.json').unlink()
        status, body = self.call('POST', '/api/login', {'password': GOOD})
        self.assertEqual(status, 403)
        self.assertIn('not set up', body['error'])

    def test_public_name_is_refused_when_not_configured(self):
        store = Store(self.root / 'other.db')
        self.addCleanup(store.close)
        cfg = load_config(ROOT / 'config')
        server, _ = make_server(store, Collector(store, cfg, []), cfg, self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        with closing(http.client.HTTPConnection('127.0.0.1', server.server_port)) as conn:
            conn.request('GET', '/', headers={'Host': 'hq.example.test'})
            self.assertEqual(conn.getresponse().status, 403)
