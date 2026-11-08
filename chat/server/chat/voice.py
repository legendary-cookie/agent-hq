"""Voice: "Hey Siri, ask lead ...".

An iPhone Shortcut posts what the person said to /voice/v1/ask with a voice key. The key belongs to one person
and one agent. The question goes into that person's "Voice" chat with the agent, like any message, so it shows
in Agent HQ Chat too. The server waits for the agent's reply and returns it as plain text for Siri to read out.
If the agent takes longer than the wait, Siri says so, and /voice/v1/latest reads the answer later.
"""
import asyncio
import re
import secrets

from . import auth
from .db import now
from .service import Forbidden, Invalid

VOICE_TITLE = 'Voice'
DEFAULT_WAIT = 45     # iOS Shortcuts give up on a web request after about a minute
MAX_WAIT = 55
SPOKEN_LIMIT = 1200   # characters read out; the rest stays in the chat
VOICE_NOTE = ('This message was spoken to you out loud, and your reply will be read aloud. Answer in a few short, '
              'plain sentences: no tables, lists, markdown, links or emoji. If the full answer is long, say the key '
              'point and that the details are in Agent HQ Chat.')

def new_key(db, user_id, agent_id, label=''):
    token = 'avk_' + secrets.token_urlsafe(32)
    db.run('INSERT INTO voice_keys (token_hash, user_id, agent_id, label, created) VALUES (?,?,?,?,?)',
           (auth.token_hash(token), user_id, agent_id, label[:80], now()))
    return token

def key_for_token(db, token):
    if not token or not token.startswith('avk_'):
        return None
    return db.one('SELECT * FROM voice_keys WHERE token_hash=? AND disabled=0', (auth.token_hash(token),))

def is_voice_conv(db, conv_id):
    return bool(db.one('SELECT 1 FROM voice_keys WHERE conv_id=?', (conv_id,)))

class Voice:
    def __init__(self, chat):
        self.chat = chat
        self.db = chat.db
        self.busy = set()

    def _owner(self, key):
        user = self.db.one('SELECT * FROM users WHERE id=? AND disabled=0', (key['user_id'],))
        if not user:
            raise Forbidden('this voice key belongs to a disabled login')
        a = self.chat.agent(key['agent_id'])
        if a['disabled'] or not self.chat.can_use_agent(user, a):
            raise Forbidden('this login can no longer use %s' % a['name'])
        self.chat.need(user, 'voice.use', 'use voice')
        return user, a

    def _conv(self, key, user, a):
        c = self.db.one('SELECT * FROM conversations WHERE id=?', (key['conv_id'],)) if key['conv_id'] else None
        if c and not c['archived'] and self.chat.is_member(c['id'], 'agent', a['id']):
            return c
        c = self.chat.create_dm(user, a['id'], VOICE_TITLE)
        self.db.run('UPDATE voice_keys SET conv_id=? WHERE id=?', (c['id'], key['id']))
        return c

    async def ask(self, key, text, wait=DEFAULT_WAIT):
        text = (text or '').strip()
        if not text:
            raise Invalid('say something after the agent name')
        if len(text) > 4000:
            raise Invalid('that was too long for one voice message')
        if key['id'] in self.busy:
            return 'I am still waiting on the last question. Ask again in a moment.'
        user, a = self._owner(key)
        self.busy.add(key['id'])
        try:
            c = self._conv(key, user, a)
            self.db.run('UPDATE voice_keys SET last_used=? WHERE id=?', (now(), key['id']))
            m = self.chat.post(user, c['id'], text, spoken=True)
            return await self._wait_reply(a, c['id'], m['id'], max(1, min(MAX_WAIT, int(wait))))
        finally:
            self.busy.discard(key['id'])

    async def _wait_reply(self, a, conv_id, after_id, wait):
        end = now() + wait
        while True:
            reply = self._reply_after(a, conv_id, after_id)
            if reply is not None:
                # A Zylos agent may send its answer in more than one message; give a second part a moment.
                await asyncio.sleep(min(2.0, max(0.0, end - now())))
                return self._reply_after(a, conv_id, after_id)
            failed = self.db.one("SELECT error FROM deliveries WHERE conv_id=? AND trigger_id=? AND state='error' ORDER BY id DESC LIMIT 1",
                                 (conv_id, after_id))
            if failed:
                return '%s could not answer that. %s' % (a['name'], failed['error'] or 'Try again in a moment.')
            if now() >= end:
                return ('%s is still working on it. The answer will be in Agent HQ Chat, or say: Hey Siri, %s check.'
                        % (a['name'], a['name']))
            await asyncio.sleep(0.5)

    def _reply_after(self, a, conv_id, after_id):
        """The agent's finished replies after the question, joined, or None while there are none yet."""
        rows = self.db.q("SELECT text FROM messages WHERE conv_id=? AND id>? AND author_type='agent' AND author_id=? "
                         "AND status='done' ORDER BY id", (conv_id, after_id, a['id']))
        if not rows:
            return None
        return spoken('\n\n'.join(r['text'] for r in rows if r['text']))

    def conversation_for(self, user, agent_id):
        """The person's voice chat with an agent: the one their Siri key uses, else their latest "Voice" chat, else a new one."""
        a = self.chat.agent(agent_id)
        if not self.chat.can_use_agent(user, a):
            raise Forbidden('you cannot use this agent')
        key = self.db.one('SELECT * FROM voice_keys WHERE user_id=? AND agent_id=? AND disabled=0 AND conv_id IS NOT NULL ORDER BY id DESC LIMIT 1',
                          (user['id'], a['id']))
        if key:
            c = self.db.one('SELECT * FROM conversations WHERE id=? AND archived=0', (key['conv_id'],))
            if c and self.chat.is_member(c['id'], 'agent', a['id']):
                return c
        c = self.db.one("SELECT c.* FROM conversations c JOIN members mu ON mu.conv_id=c.id AND mu.member_type='user' AND mu.member_id=? "
                        "JOIN members ma ON ma.conv_id=c.id AND ma.member_type='agent' AND ma.member_id=? "
                        "WHERE c.kind='dm' AND c.archived=0 AND c.name IN ('Voice', 'Voice (Siri)') ORDER BY c.id DESC LIMIT 1",
                        (str(user['id']), a['id']))
        return c or self.chat.create_dm(user, a['id'], VOICE_TITLE)

    def latest(self, key):
        """The agent's answer to the last question asked by voice (or by typing in the voice chat)."""
        user, a = self._owner(key)
        if not key['conv_id']:
            return 'You have not asked %s anything by voice yet.' % a['name']
        q = self.db.one("SELECT id FROM messages WHERE conv_id=? AND author_type='user' ORDER BY id DESC LIMIT 1", (key['conv_id'],))
        if not q:
            return 'You have not asked %s anything by voice yet.' % a['name']
        reply = self._reply_after(a, key['conv_id'], q['id'])
        if reply is None:
            working = self.db.one("SELECT 1 FROM deliveries WHERE conv_id=? AND trigger_id=? AND state IN ('queued','sent','working')",
                                  (key['conv_id'], q['id']))
            return '%s is still working on it.' % a['name'] if working else '%s has not answered yet.' % a['name']
        return reply

def spoken(text):
    """Markdown to sentences Siri can read out."""
    t = text.replace('\r\n', '\n')
    t = re.sub(r'```.*?```', ' (code is in the chat) ', t, flags=re.S)
    t = re.sub(r'`([^`]*)`', r'\1', t)
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', t)
    t = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'https?://\S+', 'a link in the chat', t)
    t = re.sub(r'^[ \t]*\|?[ \t]*:?-{3,}.*$', '', t, flags=re.M)    # table rules
    t = re.sub(r'^[ \t]*\|(.*)\|[ \t]*$', lambda m: ', '.join(x.strip() for x in m.group(1).split('|') if x.strip()) + '.', t, flags=re.M)
    t = re.sub(r'^\s{0,3}#{1,6}\s*', '', t, flags=re.M)
    t = re.sub(r'^\s*(?:[-*+•]|\d+[.)])\s+', '', t, flags=re.M)
    t = re.sub(r'(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1', r'\2', t)
    t = re.sub(r'^\s*>\s?', '', t, flags=re.M)
    t = re.sub(r'\[task:\w+\]', '', t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{2,}', '\n', t).strip()
    if len(t) > SPOKEN_LIMIT:
        cut = t[:SPOKEN_LIMIT]
        stop = max(cut.rfind('. '), cut.rfind('.\n'), cut.rfind('? '), cut.rfind('! '))
        t = (cut[:stop + 1] if stop > SPOKEN_LIMIT // 2 else cut.rsplit(' ', 1)[0] + '.') + ' The rest is in Agent HQ Chat.'
    return t or 'The answer has no text. Check Agent HQ Chat.'
