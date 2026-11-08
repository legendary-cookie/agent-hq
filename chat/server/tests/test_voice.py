import threading
import time

from chat import voice
from chat.voice import spoken

from test_chat import H, bridge, env, signin  # noqa: F401  (env is the shared fixture)

def _key(chat, username, agent_id):
    uid = chat.db.one('SELECT id FROM users WHERE username=?', (username,))['id']
    return {'Authorization': 'Bearer ' + voice.new_key(chat.db, uid, agent_id, 'iPhone')}

def _answer_soon(client, chat, agent_id, text, delay=0.3):
    h = bridge(chat, agent_id)
    seen = {}
    def run():
        for _ in range(60):
            jobs = client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries']
            if jobs:
                seen['prompt'] = jobs[0]['prompt']
                time.sleep(delay)
                client.post('/bridge/v1/deliveries/%d/reply' % jobs[0]['delivery_id'], json={'text': text}, headers=h)
                return
            time.sleep(0.1)
    t = threading.Thread(target=run)
    t.start()
    return t, seen

def test_voice_round_trip(env):
    client, chat = env
    k = _key(chat, 'boss', 'ct203-lead')
    t, seen = _answer_soon(client, chat, 'ct203-lead', '**Spend** today was $420.\n\n- Meta: $300\n- TikTok: $120')
    r = client.post('/voice/v1/ask', json={'text': 'How much did we spend today?'}, headers=k)
    t.join()
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/plain')
    assert r.text == 'Spend today was $420.\nMeta: $300\nTikTok: $120'
    assert 'read aloud' in seen['prompt'] and 'How much did we spend today?' in seen['prompt']
    # The exchange is a normal chat for that person, titled for voice.
    signin(client, 'boss')
    convs = client.get('/api/conversations').json()
    assert any(c['name'] == voice.VOICE_TITLE for c in convs)
    # Second question reuses the same chat; plain-text body works too (Shortcuts "Text" body).
    t, _ = _answer_soon(client, chat, 'ct203-lead', 'Yes.')
    r = client.post('/voice/v1/ask', content='Is the campaign live?', headers={**k, 'Content-Type': 'text/plain'})
    t.join()
    assert r.text == 'Yes.'
    assert len([c for c in client.get('/api/conversations').json() if c['name'] == voice.VOICE_TITLE]) == 1
    assert client.get('/voice/v1/latest', headers=k).text == 'Yes.'

def test_voice_slow_agent_then_latest(env):
    client, chat = env
    k = _key(chat, 'boss', 'ct203-lead')
    r = client.post('/voice/v1/ask?wait=1', json={'text': 'Big report please'}, headers=k)
    assert 'still working' in r.text
    assert 'still working' in client.get('/voice/v1/latest', headers=k).text
    h = bridge(chat, 'ct203-lead')
    job = client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries'][0]
    client.post('/bridge/v1/deliveries/%d/reply' % job['delivery_id'], json={'text': 'Report done.'}, headers=h)
    assert client.get('/voice/v1/latest', headers=k).text == 'Report done.'

def test_voice_key_rules(env):
    client, chat = env
    assert client.post('/voice/v1/ask', json={'text': 'hi'}).status_code == 401
    assert client.post('/voice/v1/ask', json={'text': 'hi'}, headers={'Authorization': 'Bearer acb_nope'}).status_code == 401
    # A bridge token is not a voice key.
    assert client.post('/voice/v1/ask', json={'text': 'hi'}, headers=bridge(chat, 'ct203-lead')).status_code == 401
    k = _key(chat, 'taylor', 'ct300-worker')  # Worker's staff agent is owner-only
    assert 'no longer use' in client.post('/voice/v1/ask', json={'text': 'hi'}, headers=k).text
    k = _key(chat, 'boss', 'ct203-lead')
    assert 'say something' in client.post('/voice/v1/ask', json={'text': '  '}, headers=k).text
    kid = chat.db.one('SELECT MAX(id) id FROM voice_keys')['id']
    chat.db.run('UPDATE voice_keys SET disabled=1 WHERE id=?', (kid,))
    assert client.post('/voice/v1/ask', json={'text': 'hi'}, headers=k).status_code == 401

def test_voice_failed_delivery(env):
    client, chat = env
    k = _key(chat, 'boss', 'ct203-lead')
    h = bridge(chat, 'ct203-lead')
    def fail():
        for _ in range(60):
            jobs = client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries']
            if jobs:
                client.post('/bridge/v1/deliveries/%d/fail' % jobs[0]['delivery_id'], json={'error': 'login expired'}, headers=h)
                return
            time.sleep(0.1)
    t = threading.Thread(target=fail); t.start()
    r = client.post('/voice/v1/ask', json={'text': 'hi'}, headers=k)
    t.join()
    assert 'could not answer' in r.text and 'login expired' in r.text

def test_spoken():
    assert spoken('# Title\nSee [the doc](https://x.y/z) and `code`.') == 'Title\nSee the doc and code.'
    assert spoken('| A | B |\n|---|---|\n| 1 | 2 |') == 'A, B.\n1, 2.'
    assert spoken('```py\nx=1\n```\nDone [task:done]').strip().endswith('Done')
    long = spoken('Sentence one is here. ' * 200)
    assert len(long) < 1300 and long.endswith('The rest is in Agent HQ Chat.')

def test_talk_mode_shares_voice_chat(env):
    client, chat = env
    k = _key(chat, 'boss', 'ct203-lead')
    client.post('/voice/v1/ask?wait=1', json={'text': 'First by Siri'}, headers=k)
    signin(client, 'boss')
    c = client.post('/api/voice/conversation', json={'agent': 'lead'}, headers=H).json()
    kconv = chat.db.one('SELECT conv_id FROM voice_keys').get('conv_id')
    assert c['id'] == kconv and c['name'] == voice.VOICE_TITLE
    h = bridge(chat, 'ct203-lead')
    client.get('/bridge/v1/poll?wait=0', headers=h)  # take the Siri question
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Now by talk mode', 'spoken': True}, headers=H)
    client.post('/api/conversations/%d/messages' % c['id'], json={'text': 'Typed one'}, headers=H)
    jobs = client.get('/bridge/v1/poll?wait=0', headers=h).json()['deliveries']
    assert [j['spoken'] for j in jobs] == [True, False]
    assert 'read aloud' in jobs[0]['prompt'] and 'read aloud' not in jobs[1]['prompt']

def test_talk_mode_new_chat_and_rules(env):
    client, chat = env
    signin(client, 'worker')
    c = client.post('/api/voice/conversation', json={'agent': 'ct300-worker'}, headers=H).json()
    again = client.post('/api/voice/conversation', json={'agent': 'ct300-worker'}, headers=H).json()
    assert c['id'] == again['id'] and c['name'] == 'Voice'
    signin(client, 'taylor')
    assert client.post('/api/voice/conversation', json={'agent': 'ct300-worker'}, headers=H).status_code == 403
    assert client.post('/api/voice/conversation', json={'agent': 'nobody'}, headers=H).status_code == 404
