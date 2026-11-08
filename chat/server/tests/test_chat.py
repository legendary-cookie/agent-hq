import time

import pytest
from fastapi.testclient import TestClient

from chat import auth
from chat.__main__ import import_fleet
from chat.app import create_app
from chat.db import now

H = {'X-Requested-With': 'agent-hq-chat'}
FLEET = {'agents': {
    'ct201-assistant': {'handle': 'assistant', 'label': 'Assistant AI', 'owner': 'Taylor', 'group': 'Management'},
    'ct203-lead': {'handle': 'lead', 'label': 'Agent lead', 'owner': 'Jordan', 'group': 'Management'},
    'ct252-sales': {'label': 'Sales', 'group': 'Department'},
    'ct300-worker': {'label': 'Worker', 'owner': 'Worker', 'group': 'Staff'},
}}


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path, tmp_path / 'web', '', secure_cookie=False)
    chat = app.state.chat
    db = chat.db
    for name, role in (('boss', 'admin'), ('worker', 'staff'), ('taylor', 'staff')):
        db.run('INSERT INTO users (username, name, pw_hash, role, created) VALUES (?,?,?,?,?)',
               (name, name.title(), auth.hash_password('password-123'), role, now()))
    import_fleet(db, FLEET)
    worker_id = db.one("SELECT id FROM users WHERE username='worker'")['id']
    db.run("UPDATE agents SET owner_user_id=? WHERE id='ct300-worker'", (worker_id,))
    with TestClient(app) as client:
        yield client, chat


def signin(client, name):
    client.cookies.clear()
    r = client.post('/api/login', json={'username': name, 'password': 'password-123'}, headers=H)
    assert r.status_code == 200, r.text


def bridge(chat, agent_id):
    return {'Authorization': 'Bearer ' + auth.new_agent_token(chat.db, agent_id)}


def test_login_and_visibility(env):
    client, _ = env
    assert client.get('/api/bootstrap').status_code == 401
    r = client.post('/api/login', json={'username': 'worker', 'password': 'nope'}, headers=H)
    assert r.status_code == 400
    signin(client, 'taylor')
    ids = {a['id'] for a in client.get('/api/bootstrap').json()['agents']}
    assert 'ct300-worker' not in ids and 'ct201-assistant' in ids  # staff agents are their owner's
    signin(client, 'worker')
    ids = {a['id'] for a in client.get('/api/bootstrap').json()['agents']}
    assert 'ct300-worker' in ids


def test_csrf_header_required(env):
    client, _ = env
    signin(client, 'worker')
    assert client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct201-assistant'}).status_code == 403


def test_dm_bridge_roundtrip(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct201-assistant'}, headers=H).json()
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Plan my week'}, headers=H)
    auth_h = bridge(chat, 'ct201-assistant')
    jobs = client.get('/bridge/v1/poll?wait=0', headers=auth_h).json()['deliveries']
    assert len(jobs) == 1 and jobs[0]['text'] == 'Plan my week' and 'private chat with Worker' in jobs[0]['prompt']
    did = jobs[0]['delivery_id']
    client.post('/bridge/v1/deliveries/%d/reply' % did, json={'text': 'Here is', 'final': False}, headers=auth_h)
    client.post('/bridge/v1/deliveries/%d/reply' % did, json={'text': 'Here is your plan', 'final': True}, headers=auth_h)
    msgs = client.get('/api/conversations/%d/messages' % c['id']).json()['messages']
    assert [m['text'] for m in msgs] == ['Plan my week', 'Here is your plan'] and msgs[1]['status'] == 'done'
    # Zylos-style follow-up through its own send script
    client.post('/bridge/v1/send', json={'endpoint': jobs[0]['endpoint'], 'text': 'One more thing'}, headers=auth_h)
    assert len(client.get('/api/conversations/%d/messages' % c['id']).json()['messages']) == 3
    # Taylor cannot read Worker's chat
    signin(client, 'taylor')
    assert client.get('/api/conversations/%d/messages' % c['id']).status_code == 403


def test_channel_modes(env):
    client, chat = env
    signin(client, 'boss')
    c = client.post('/api/conversations', headers=H, json={'kind': 'channel', 'name': 'Launch Plan',
                    'agents': ['ct201-assistant', 'ct203-lead'], 'reply_mode': 'lead', 'lead_agent': 'ct203-lead'}).json()
    assert c['name'] == 'launch-plan'
    k, j = bridge(chat, 'ct201-assistant'), bridge(chat, 'ct203-lead')

    def polled(h):
        return client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries']

    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Ideas?'}, headers=H)
    assert polled(k) == [] and len(polled(j)) == 1  # lead answers
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': '@assistant your view?'}, headers=H)
    got = polled(k)
    assert len(got) == 1 and polled(j) == []  # mention beats lead
    assert 'Ideas?' in got[0]['prompt']  # sees what happened since its last reply
    # Assistant hands over to @sales, who is pulled in only if already a member: not a member, so nothing
    client.post('/bridge/v1/deliveries/%d/reply' % got[0]['delivery_id'], json={'text': 'Ask @lead too'}, headers=k)
    assert len(polled(j)) == 1  # agent-to-agent mention
    client.patch('/api/conversations/%d' % c['id'], json={'reply_mode': 'all'}, headers=H)
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Everyone?'}, headers=H)
    assert len(polled(k)) == 1 and len(polled(j)) == 1
    client.patch('/api/conversations/%d' % c['id'], json={'reply_mode': 'mention'}, headers=H)
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Just noting'}, headers=H)
    assert polled(k) == [] and polled(j) == []
    # a person mentioning a visible agent brings it into the channel
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': '@sales numbers please'}, headers=H)
    assert len(polled(bridge(chat, 'ct252-sales'))) == 1


def test_task_status_from_agent(env):
    client, chat = env
    signin(client, 'worker')
    t = client.post('/api/tasks', json={'title': 'Draft newsletter', 'description': 'For November', 'assignee': 'ct300-worker'}, headers=H).json()
    h = bridge(chat, 'ct300-worker')
    job = client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries'][0]
    assert job['task']['title'] == 'Draft newsletter' and '[task:done]' in job['prompt']
    client.post('/bridge/v1/deliveries/%d/reply' % job['delivery_id'], json={'text': 'Draft attached.\n[task:review]'}, headers=h)
    task = [x for x in client.get('/api/tasks').json() if x['id'] == t['id']][0]
    assert task['status'] == 'review'
    msgs = client.get('/api/conversations/%d/messages' % t['conv_id']).json()['messages']
    assert any(m['text'] == 'Draft attached.' for m in msgs)


def test_demo_agent_answers(env):
    client, chat = env
    chat.db.run("INSERT INTO agents (id, handle, name, kind, created) VALUES ('demo-helper','helper','Demo Helper','demo',?)", (now(),))
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'demo-helper'}, headers=H).json()
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'hello'}, headers=H)
    for _ in range(100):
        msgs = client.get('/api/conversations/%d/messages' % c['id']).json()['messages']
        if len(msgs) == 2 and msgs[1]['status'] == 'done':
            break
        time.sleep(0.1)
    assert 'Demo Helper' in msgs[1]['text']


def test_switch_dm_agent_keeps_history(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct201-assistant'}, headers=H).json()
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Budget is 5k'}, headers=H)
    client.patch('/api/conversations/%d' % c['id'], json={'agent': 'ct203-lead'}, headers=H)
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'What do you think?'}, headers=H)
    jobs = client.get('/bridge/v1/poll?wait=0', headers=bridge(chat, 'ct203-lead')).json()['deliveries']
    assert len(jobs) == 1 and 'Budget is 5k' in jobs[0]['prompt']

def test_telegram_history(env):
    client, chat = env
    hdr = bridge(chat, 'ct300-worker')
    items = [
        {'id': 1, 'chat': '111', 'out': False, 'from': 'worker_tg', 'chat_name': 'worker_tg', 'text': 'hello from telegram', 'at': '2026-11-01 10:00:00'},
        {'id': 2, 'chat': '111', 'out': True, 'text': 'hi Worker', 'at': '2026-11-01 10:00:05'},
        {'id': 3, 'chat': '-555', 'out': False, 'from': 'someone', 'group': 'Sales team', 'text': 'group note', 'at': '2026-11-01 11:00:00'},
    ]
    r = client.post('/bridge/v1/telegram', json={'messages': items}, headers=hdr)
    assert r.status_code == 200 and r.json()['added'] == 3
    # the same batch again adds nothing
    assert client.post('/bridge/v1/telegram', json={'messages': items}, headers=hdr).json()['added'] == 0

    signin(client, 'worker')
    convs = [c for c in client.get('/api/bootstrap').json()['conversations'] if c['kind'] == 'telegram']
    assert sorted(c['name'] for c in convs) == ['Sales team · Telegram group', 'worker_tg · Telegram']
    dm = next(c for c in convs if c['name'].endswith('· Telegram'))
    msgs = client.get('/api/conversations/%d/messages' % dm['id']).json()['messages']
    assert [m['author_type'] for m in msgs] == ['external', 'agent']
    assert msgs[0]['author_name'] == 'worker_tg' and msgs[0]['created'] < msgs[1]['created']
    # read-only
    r = client.post('/api/conversations/%d/messages' % dm['id'], json={'text': 'x'}, headers=H)
    assert r.status_code == 400

    # Taylor (not the owner) can't see it
    signin(client, 'taylor')
    assert not [c for c in client.get('/api/bootstrap').json()['conversations'] if c['kind'] == 'telegram']
    assert client.get('/api/conversations/%d/messages' % dm['id']).status_code == 403

    # a new owner takes it over
    taylor = chat.db.one("SELECT id FROM users WHERE username='taylor'")['id']
    signin(client, 'boss')
    assert client.patch('/api/admin/agents/ct300-worker', json={'owner_user_id': taylor}, headers=H).status_code == 200
    signin(client, 'taylor')
    assert len([c for c in client.get('/api/bootstrap').json()['conversations'] if c['kind'] == 'telegram']) == 2

def test_tg_parse():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('acb', pathlib.Path(__file__).parents[2] / 'bridge' / 'agent_hq_chat_bridge.py')
    acb = importlib.util.module_from_spec(spec); spec.loader.exec_module(acb)
    p = acb.tg_parse
    assert p('in', None, '[Scheduled Task: x] do it') is None
    assert p('in', '5|msg:1', '[Scheduled Task: x] do it') is None
    assert p('in', '5|msg:1|req:5:1', '[TG DM] Wil said: <current-message>\nit&apos;s ok\n</current-message> ---- reply via: node c4-send.js "telegram" "5"') == ('5', 'Wil', "it's ok", None)
    chat, who, text, group = p('in', '-9|msg:2', '[TG GROUP:Sales] Wil said: <group-context>\n[bot]: old\n</group-context>\n<replying-to>\n[bot]: report\n</replying-to>\n\n<current-message>\nstop\n</current-message> ---- file: /x/media/photo-1.jpg')
    assert (chat, who, group) == ('-9', 'Wil', 'Sales') and text.startswith('> [bot]: report') and 'stop' in text and 'photo-1.jpg' in text and 'old' not in text
    assert p('out', '5', 'Done &amp; sent') == ('5', None, 'Done & sent', None)

def test_telegram_chat_named_after_person(env):
    client, chat = env
    hdr = bridge(chat, 'ct300-worker')
    client.post('/bridge/v1/telegram', json={'messages': [
        {'id': 1, 'chat': '999', 'out': True, 'chat_name': '', 'text': 'hello', 'at': '2026-11-01 10:00:00'},
        {'id': 2, 'chat': '999', 'out': False, 'from': 'JordanWeelead', 'chat_name': '', 'text': 'hi', 'at': '2026-11-01 10:01:00'}]}, headers=hdr)
    assert chat.db.one("SELECT name FROM conversations WHERE kind='telegram'")['name'] == 'JordanWeelead · Telegram'

def test_talk_in_any_chat(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct300-worker'}, headers=H).json()
    r = client.post('/api/voice/conversation', json={'conversation': c['id']}, headers=H)
    assert r.status_code == 200 and r.json()['id'] == c['id']
    m = client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'hello out loud', 'spoken': True}, headers=H).json()
    assert m['spoken'] is True
    typed = client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'typed'}, headers=H).json()
    assert not typed.get('spoken')
    assert [x.get('spoken', False) for x in client.get('/api/conversations/%d/messages' % c['id']).json()['messages'] if x['author_type'] == 'user'] == [True, False]
    # not someone else's chat, and not Telegram history
    signin(client, 'taylor')
    assert client.post('/api/voice/conversation', json={'conversation': c['id']}, headers=H).status_code == 403
    client.post('/bridge/v1/telegram', json={'messages': [{'id': 1, 'chat': '1', 'out': True, 'text': 'x', 'at': '2026-11-01 10:00:00'}]}, headers=bridge(chat, 'ct300-worker'))
    signin(client, 'worker')
    tg = chat.db.one("SELECT id FROM conversations WHERE kind='telegram'")['id']
    assert client.post('/api/voice/conversation', json={'conversation': tg}, headers=H).status_code == 400

def test_roles_permissions_and_passwords(env):
    client, chat = env
    db = chat.db
    taylor = db.one("SELECT id FROM users WHERE username='taylor'")['id']
    signin(client, 'boss')
    # roles list and catalog
    r = client.get('/api/admin/roles').json()
    assert {x['id'] for x in r['roles']} >= {'admin', 'manager', 'staff'} and len(r['catalog']) == 10
    # a manager sees every staff agent
    assert client.patch('/api/admin/users/%d' % taylor, json={'role': 'manager', 'team': 'Management'}, headers=H).status_code == 200
    signin(client, 'taylor')
    assert 'ct300-worker' in {a['id'] for a in client.get('/api/bootstrap').json()['agents']}
    assert 'agents.all_staff' in client.get('/api/bootstrap').json()['me']['permissions']
    # a per-person change takes away department agents and channels
    signin(client, 'boss')
    client.patch('/api/admin/users/%d' % taylor, json={'overrides': {'agents.department': False, 'channels.create': False}}, headers=H)
    signin(client, 'taylor')
    assert 'ct252-sales' not in {a['id'] for a in client.get('/api/bootstrap').json()['agents']}
    r = client.post('/api/conversations', json={'kind': 'channel', 'name': 'x', 'agents': ['ct201-assistant']}, headers=H)
    assert r.status_code == 403
    # editing a role changes everyone in it; admin can't be edited
    signin(client, 'boss')
    assert client.put('/api/admin/roles/admin', json={'permissions': []}, headers=H).status_code == 400
    client.put('/api/admin/roles/staff', json={'permissions': ['agents.management']}, headers=H)
    signin(client, 'worker')
    assert client.post('/api/tasks', json={'title': 't'}, headers=H).status_code == 403
    # custom role, and it can't be deleted while used
    signin(client, 'boss')
    assert client.put('/api/admin/roles/intern', json={'label': 'Intern', 'permissions': ['files.upload']}, headers=H).status_code == 200
    worker = db.one("SELECT id FROM users WHERE username='worker'")['id']
    client.patch('/api/admin/users/%d' % worker, json={'role': 'intern'}, headers=H)
    assert client.delete('/api/admin/roles/intern', headers=H).status_code == 400
    assert client.delete('/api/admin/roles/staff', headers=H).status_code == 400
    # passwords: made, viewable, and they work; the database alone doesn't reveal them
    r = client.post('/api/admin/users', json={'username': 'newbie', 'name': 'New Bie'}, headers=H).json()
    pw = r['password']
    assert len(pw) == 14 and r['role'] == 'staff' and r['team'] == 'Staff'
    assert client.get('/api/admin/users/%d/password' % r['id']).status_code == 404
    sealed = db.one('SELECT pw_sealed FROM users WHERE id=?', (r['id'],))['pw_sealed']
    assert sealed is None
    new = client.post('/api/admin/users/%d/new-password' % r['id'], headers=H).json()['password']
    assert new != pw and client.get('/api/admin/users/%d/password' % r['id']).status_code == 404
    client.cookies.clear()
    assert client.post('/api/login', json={'username': 'newbie', 'password': new}, headers=H).status_code == 200
    # no copy for a password set before copies were kept
    signin(client, 'boss')
    assert client.get('/api/admin/users/%d/password' % worker).status_code == 404
    # only admins
    signin(client, 'taylor')
    assert client.get('/api/admin/users/%d/password' % r['id']).status_code == 404

def test_export_and_activity(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct300-worker'}, headers=H).json()
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Plan for **Monday**'}, headers=H)
    r = client.get('/api/conversations/%d/export?format=md' % c['id'])
    assert r.status_code == 200 and 'attachment' in r.headers['content-disposition'] and 'Plan for **Monday**' in r.text and '**Worker**' in r.text
    assert 'Worker:' in client.get('/api/conversations/%d/export?format=txt' % c['id']).text
    j = client.get('/api/conversations/%d/export?format=json' % c['id']).json()
    assert j['messages'][0]['author'] == 'Worker' and j['conversation']['kind'] == 'dm'
    assert client.get('/api/conversations/%d/export?format=exe' % c['id']).status_code == 400
    # Telegram history exports too, with the person's name
    client.post('/bridge/v1/telegram', json={'messages': [{'id': 1, 'chat': '7', 'out': False, 'from': 'worker_tg', 'chat_name': 'worker_tg', 'text': 'hi from TG', 'at': '2026-11-01 10:00:00'}]}, headers=bridge(chat, 'ct300-worker'))
    tg = chat.db.one("SELECT id FROM conversations WHERE kind='telegram'")['id']
    assert 'worker_tg (Telegram)' in client.get('/api/conversations/%d/export' % tg).text
    # someone else's chat can't be exported
    signin(client, 'taylor')
    assert client.get('/api/conversations/%d/export' % c['id']).status_code == 403

    # live activity from the bridge
    hdr = bridge(chat, 'ct300-worker')
    steps = [{'at': '2026-11-09T01:00:00Z', 'kind': 'input', 'text': 'check stock'}, {'at': '2026-11-09T01:00:02Z', 'kind': 'thinking', 'text': 'I should look at the sheet'},
             {'at': '2026-11-09T01:00:03Z', 'kind': 'tool', 'tool': 'Bash', 'text': 'python stock.py'}, {'kind': 'bogus', 'text': 'x'}]
    assert client.post('/bridge/v1/activity', json={'steps': steps, 'updated': 1794150000}, headers=hdr).status_code == 200
    signin(client, 'worker')
    a = client.get('/api/agents/ct300-worker/activity').json()
    assert [s['kind'] for s in a['steps']] == ['input', 'thinking', 'tool'] and a['steps'][2]['tool'] == 'Bash'
    signin(client, 'taylor')  # staff role: Worker's agent is owner-only
    assert client.get('/api/agents/ct300-worker/activity').status_code == 403
    assert client.get('/api/agents/ct201-assistant/activity').status_code == 403  # staff can't watch management agents
    taylor = chat.db.one("SELECT id FROM users WHERE username='taylor'")['id']
    chat.db.run("UPDATE agents SET owner_user_id=? WHERE id='ct201-assistant'", (taylor,))
    assert client.get('/api/agents/ct201-assistant/activity').status_code == 200  # ...but can watch their own
    signin(client, 'boss')
    assert client.get('/api/agents/ct300-worker/activity').status_code == 200

def test_act_step():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('acb', pathlib.Path(__file__).parents[2] / 'bridge' / 'agent_hq_chat_bridge.py')
    acb = importlib.util.module_from_spec(spec); spec.loader.exec_module(acb)
    d = {'timestamp': 't', 'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hmm  let me\nsee'},
         {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls -la', 'description': 'List files'}}, {'type': 'text', 'text': 'Done.'}]}}
    assert [(s['kind'], s['text']) for s in acb.act_step(d)] == [('thinking', 'hmm let me see'), ('tool', 'List files'), ('text', 'Done.')]
    u = {'timestamp': 't', 'message': {'role': 'user', 'content': '[TG DM] Wil said: hi ---- reply via: node x'}}
    assert acb.act_step(u)[0]['text'] == '[TG DM] Wil said: hi'
    assert acb.act_step({'type': 'summary'}) == []

def test_reply_and_quote(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct300-worker'}, headers=H).json()
    a = client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Stock: Eye Serum 12, Snow toner 8, Eye Bright 5.'}, headers=H).json()
    # reply to a highlighted part
    r = client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Order more of this', 'reply_to': a['id'], 'reply_quote': 'Snow toner 8'}, headers=H).json()
    assert r['reply']['id'] == a['id'] and r['reply']['quote'] == 'Snow toner 8' and r['reply']['author'] == 'Worker'
    # a quote that isn't in the message is dropped; the reply keeps the start of the message
    r2 = client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'x', 'reply_to': a['id'], 'reply_quote': 'made up'}, headers=H).json()
    assert r2['reply']['quote'].startswith('Stock: Eye Serum')
    # replying to a message from another conversation fails
    other = client.post('/api/conversations', json={'kind': 'dm', 'agent_id': 'ct300-worker'}, headers=H).json()
    assert client.post('/api/conversations/%d/messages' % other['id'], json={'text': 'x', 'reply_to': a['id']}, headers=H).status_code == 400
    # the agent is told what was replied to
    d = chat.db.one('SELECT id FROM deliveries WHERE trigger_id=?', (r['id'],))
    p = chat.payload(chat.db.one('SELECT * FROM deliveries WHERE id=?', (d['id'],)))
    assert 'Worker is replying to this part of Worker:' in p['prompt'] and '> Snow toner 8' in p['prompt'] and p['replying_to']['part']
    # history lists keep it
    msgs = client.get('/api/conversations/%d/messages' % c['id']).json()['messages']
    assert [m.get('reply', {}).get('id') for m in msgs] == [None, a['id'], a['id']]


def test_hq_navigation_link(env, monkeypatch):
    client, chat = env
    signin(client, 'boss')
    monkeypatch.setenv('CHAT_HQ_URL', 'javascript:alert(1)')
    assert client.get('/api/bootstrap').json()['hq_url'] is None
    monkeypatch.setenv('CHAT_HQ_URL', 'http://127.0.0.1:7420')
    assert client.get('/api/bootstrap').json()['hq_url'] == 'http://127.0.0.1:7420'


def test_person_can_change_password_without_storing_a_copy(env):
    client, chat = env
    signin(client, 'worker')
    response = client.post('/api/me/password', json={'current': 'password-123', 'new': 'new-password-456'}, headers=H)
    assert response.status_code == 200
    user = chat.db.one("SELECT * FROM users WHERE username='worker'")
    assert user['pw_sealed'] is None and auth.check_password('new-password-456', user['pw_hash'])
    client.cookies.clear()
    assert client.post('/api/login', json={'username': 'worker', 'password': 'new-password-456'}, headers=H).status_code == 200
