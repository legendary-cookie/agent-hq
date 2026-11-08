import http.client
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from hq.accounts import PERMISSIONS, Accounts, required_permission
from hq.auth import Login

GOOD = 'correct horse battery staple'
OTHER = 'another long password!'


class AccountsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = 1_800_000_000.0

    def accounts(self):
        return Accounts(self.root, clock=lambda: self.now)

    def test_old_password_becomes_admin_and_its_sessions_keep_working(self):
        old = Login(self.root / 'login.json', clock=lambda: self.now)
        old.set_password(GOOD)
        token = old.sign_in(GOOD)
        acc = self.accounts()
        self.assertEqual(acc.user_for(token), 'admin')
        self.assertTrue(acc.sign_in('admin', GOOD))
        self.assertTrue(acc.sign_in(None, GOOD))  # old clients send no username
        self.assertFalse((self.root / 'login.json').exists())
        self.assertTrue((self.root / 'login.json.pre-accounts').exists())
        self.assertNotIn(GOOD, (self.root / 'users.json').read_text())
        self.assertEqual(acc.me('admin')['permissions'], sorted(PERMISSIONS))

    def test_users_roles_and_permissions(self):
        acc = self.accounts()
        acc.set_password('admin', GOOD)
        acc.act({'action': 'create_user', 'username': 'taylor', 'name': 'Taylor', 'role': 'viewer', 'password': OTHER}, 'admin')
        token = acc.sign_in('taylor', OTHER)
        self.assertEqual(acc.user_for(token), 'taylor')
        self.assertIn('view.overview', acc.permissions_of('taylor'))
        self.assertNotIn('agents.control', acc.permissions_of('taylor'))
        # A custom role, then moving Taylor to it signs her out
        rid = acc.act({'action': 'create_role', 'label': 'Ops lead', 'permissions': ['view.overview', 'agents.message']}, 'admin')['id']
        acc.act({'action': 'update_user', 'username': 'taylor', 'role': rid}, 'admin')
        self.assertIsNone(acc.user_for(token))
        self.assertEqual(acc.permissions_of('taylor'), {'view.overview', 'agents.message'})
        with self.assertRaises(ValueError):
            acc.act({'action': 'delete_role', 'id': rid}, 'admin')  # still in use
        with self.assertRaises(ValueError):
            acc.act({'action': 'update_role', 'id': 'admin', 'permissions': []}, 'admin')
        with self.assertRaises(ValueError):
            acc.act({'action': 'update_role', 'id': rid, 'permissions': ['root.everything']}, 'admin')
        # Disabled users can't sign in
        acc.act({'action': 'update_user', 'username': 'taylor', 'disabled': True}, 'admin')
        self.assertIsNone(acc.sign_in('taylor', OTHER))

    def test_there_is_always_an_active_admin(self):
        acc = self.accounts()
        acc.set_password('admin', GOOD)
        for change in ({'action': 'update_user', 'username': 'admin', 'role': 'viewer'},
                       {'action': 'update_user', 'username': 'admin', 'disabled': True}):
            with self.assertRaises(ValueError):
                acc.act(change, 'someone-else')
        with self.assertRaises(ValueError):
            acc.act({'action': 'delete_user', 'username': 'admin'}, 'admin')  # yourself

    def test_bad_input_is_refused(self):
        acc = self.accounts()
        acc.set_password('admin', GOOD)
        for bad in ({'action': 'create_user', 'username': 'A B', 'role': 'viewer', 'password': OTHER},
                    {'action': 'create_user', 'username': 'bob', 'role': 'nope', 'password': OTHER},
                    {'action': 'create_user', 'username': 'bob', 'role': 'viewer', 'password': 'short'},
                    {'action': 'create_user', 'username': 'admin', 'role': 'viewer', 'password': OTHER},
                    {'action': 'explode'}):
            with self.assertRaises(ValueError):
                acc.act(bad, 'admin')

    def test_own_password_change_keeps_this_session_only(self):
        acc = self.accounts()
        acc.set_password('admin', GOOD)
        here, there = acc.sign_in('admin', GOOD), acc.sign_in('admin', GOOD)
        with self.assertRaises(ValueError):
            acc.change_own_password(here, 'wrong wrong wrong', OTHER)
        acc.change_own_password(here, GOOD, OTHER)
        self.assertEqual(acc.user_for(here), 'admin')
        self.assertIsNone(acc.user_for(there))
        self.assertTrue(acc.sign_in('admin', OTHER))

    def test_a_password_set_from_the_terminal_is_seen_by_the_running_server(self):
        running = self.accounts()
        running.set_password('admin', GOOD)
        token = running.sign_in('admin', GOOD)
        self.accounts().set_password('admin', OTHER)  # another process
        self.assertIsNone(running.user_for(token))
        self.assertTrue(running.sign_in('admin', OTHER))

    def test_route_permissions(self):
        self.assertIsNone(required_permission('GET', '/api/me'))
        self.assertEqual(required_permission('GET', '/api/fleet'), 'view.overview')
        self.assertEqual(required_permission('GET', '/api/sessions/abc'), 'view.transcripts')
        self.assertEqual(required_permission('GET', '/api/sessions'), 'view.agents')
        self.assertEqual(required_permission('GET', '/api/terminal', query={'mode': ['root']}), 'agents.terminal')
        self.assertEqual(required_permission('GET', '/api/terminal', query={'mode': ['watch']}), 'view.transcripts')
        self.assertEqual(required_permission('POST', '/api/control/send'), 'agents.message')
        self.assertEqual(required_permission('POST', '/api/control/power'), 'agents.control')
        self.assertEqual(required_permission('POST', '/api/router/key-action'), 'router.manage')
        self.assertEqual(required_permission('POST', '/api/accounts'), 'accounts.manage')
        self.assertIsNone(required_permission('POST', '/api/control/elevate'))
        self.assertEqual(required_permission('POST', '/api/something-new'), 'settings.manage')  # unknown writes need the most


class AccountsServerTests(unittest.TestCase):
    def setUp(self):
        from hq.collector import Collector
        from hq.config import load_config
        from hq.server import make_server
        from hq.store import Store
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        store = Store(self.root / 'hq.db')
        self.addCleanup(store.close)
        cfg = load_config(Path(__file__).resolve().parents[1] / 'config')
        (self.root / 'fleet.json').write_text(json.dumps({'brand': 'Acme HQ'}), encoding='utf-8')
        acc = Accounts(self.root)
        acc.set_password('admin', GOOD)
        acc.act({'action': 'create_user', 'username': 'viewer1', 'role': 'viewer', 'password': OTHER}, 'admin')
        self.server, self.master = make_server(store, Collector(store, cfg, []), cfg, self.root, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown(); self.server.server_close(); thread.join()
        self.addCleanup(stop)
        self.host = f'127.0.0.1:{self.server.server_port}'

    def call(self, method, path, body=None, token=None):
        headers = {'Host': self.host, 'Origin': f'http://{self.host}', 'Content-Type': 'application/json'}
        if token:
            headers['X-HQ-Token'] = token
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port)) as conn:
            conn.request(method, path, None if body is None else json.dumps(body), headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b'null')

    def test_sign_in_by_username_and_what_each_role_may_do(self):
        status, body = self.call('POST', '/api/login', {'username': 'viewer1', 'password': OTHER})
        self.assertEqual(status, 200)
        viewer = body['token']
        self.assertEqual(self.call('GET', '/api/me', token=viewer)[1]['role'], 'viewer')
        self.assertEqual(self.call('GET', '/api/fleet?range=today', token=viewer)[0], 200)
        status, body = self.call('GET', '/api/accounts', token=viewer)
        self.assertEqual((status, body['permission']), (403, 'accounts.manage'))
        self.assertEqual(self.call('POST', '/api/control/send', {'host': 'x', 'text': 'hi'}, token=viewer)[0], 403)
        admin = self.call('POST', '/api/login', {'username': 'admin', 'password': GOOD})[1]['token']
        status, body = self.call('GET', '/api/accounts', token=admin)
        self.assertEqual(status, 200)
        self.assertEqual({u['username'] for u in body['users']}, {'admin', 'viewer1'})
        self.assertNotIn('password', json.dumps(body))
        # Changes need the password check first
        self.assertEqual(self.call('POST', '/api/accounts', {'action': 'delete_user', 'username': 'viewer1'}, token=admin)[0], 403)
        self.assertEqual(self.call('POST', '/api/control/elevate', {'password': GOOD}, token=admin)[0], 200)
        self.assertEqual(self.call('POST', '/api/accounts', {'action': 'delete_user', 'username': 'viewer1'}, token=admin)[0], 200)
        self.assertEqual(self.call('GET', '/api/me', token=viewer)[0], 403)  # deleted: signed out

    def test_wrong_username_or_password(self):
        self.assertEqual(self.call('POST', '/api/login', {'username': 'nobody', 'password': GOOD})[0], 401)
        self.assertEqual(self.call('POST', '/api/login', {'username': 'viewer1', 'password': GOOD})[0], 401)


if __name__ == '__main__':
    unittest.main()
