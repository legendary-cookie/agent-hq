"""The chat itself: who can see what, conversations, messages, which agents answer, tasks.

Web routes and the agent bridge both go through here, so the rules live in one place.
"""
import json
import re
import secrets
import calendar
import time

from . import perms as P
from .db import loads, now

ONLINE_SECONDS = 90
MAX_DEPTH = 3          # agent -> agent hand-offs allowed after one person's message
CONTEXT_MESSAGES = 20  # messages from others an agent is shown since its last reply
HISTORY_MESSAGES = 30  # recent turns sent to agents that keep no memory of their own
MENTION = re.compile(r'(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]{0,40})')
TASK_MARK = re.compile(r'^\s*\[task:(todo|doing|review|done)\]\s*$', re.I | re.M)
EVERYONE = {'all', 'channel', 'here', 'everyone'}
TASK_STATES = ('todo', 'doing', 'review', 'done')
REPLY_MODES = ('lead', 'all', 'mention')
SERVER_KINDS = ('demo', 'openai')  # answered by the server itself, no bridge


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def clean_text(text, limit=20000):
    if not isinstance(text, str):
        raise Invalid('text is required')
    text = text.replace('\r\n', '\n').strip()
    if len(text) > limit:
        raise Invalid('message is too long (%d characters, limit %d)' % (len(text), limit))
    return text


class Chat:
    def __init__(self, db, hub, files_dir=None, public_url=''):
        self.db, self.hub, self.files_dir, self.public_url = db, hub, files_dir, public_url.rstrip('/')

    # ---------------------------------------------------------------- users and agents
    def user_public(self, u):
        return {'id': u['id'], 'username': u['username'], 'name': u['name'], 'role': u['role'], 'disabled': bool(u['disabled'])}

    def user_admin(self, u, role_map=None):
        """What the Admin page shows about a person (not the password: that has its own endpoint)."""
        out = self.user_public(u)
        out.update(team=u.get('team') or '', overrides=loads(u.get('perm_overrides'), {}), has_password_copy=False,
                   permissions=sorted(P.effective(self.db, u, role_map)))
        return out

    def can(self, user, perm):
        return perm in P.effective(self.db, user)

    def need(self, user, perm, what):
        if not self.can(user, perm):
            raise Forbidden('you are not allowed to %s' % what)

    def users(self):
        return [self.user_public(u) for u in self.db.q('SELECT * FROM users ORDER BY name')]

    def admins(self):
        return {r['id'] for r in self.db.q("SELECT id FROM users WHERE role = 'admin' AND disabled = 0")}

    def agent_public(self, a, admin=False):
        online = a['kind'] in SERVER_KINDS or bool(a['last_seen'] and now() - a['last_seen'] < ONLINE_SECONDS)
        out = {k: a[k] for k in ('id', 'handle', 'name', 'kind', 'grp', 'title', 'department', 'owner_name',
                                 'owner_user_id', 'reports_to', 'visibility', 'color')}
        out.update(online=online, last_seen=a['last_seen'], disabled=bool(a['disabled']))
        if admin:
            info = loads(a['bridge_info'], {})
            cfg = loads(a['config'], {})
            out.update(bridge=info, has_token=bool(a['token_hash']),
                       config={k: v for k, v in cfg.items() if k != 'api_key'}, has_api_key=bool(cfg.get('api_key')))
        return out

    def can_use_agent(self, user, a, perms=None):
        if not a or a['disabled']:
            return False
        if user['role'] == 'admin' or a['owner_user_id'] == user['id']:
            return True
        perms = P.effective(self.db, user) if perms is None else perms
        if a['grp'] == 'Management' and 'agents.management' not in perms:
            return False
        if a['grp'] == 'Department' and 'agents.department' not in perms:
            return False
        if a['grp'] == 'Staff' and 'agents.all_staff' in perms:
            return True
        return a['visibility'] == 'everyone'

    def visible_agents(self, user):
        rows = self.db.q('SELECT * FROM agents ORDER BY grp, name')
        admin = user['role'] == 'admin'
        perms = P.effective(self.db, user)
        return [self.agent_public(a, admin) for a in rows if admin or self.can_use_agent(user, a, perms)]

    def agent(self, agent_id):
        a = self.db.one('SELECT * FROM agents WHERE id = ?', (agent_id,))
        if not a:
            raise NotFound('no such agent')
        return a

    # ---------------------------------------------------------------- conversations
    def conv(self, conv_id):
        c = self.db.one('SELECT * FROM conversations WHERE id = ?', (conv_id,))
        if not c:
            raise NotFound('no such conversation')
        return c

    def is_member(self, conv_id, mtype, mid):
        return bool(self.db.one('SELECT 1 FROM members WHERE conv_id=? AND member_type=? AND member_id=?', (conv_id, mtype, str(mid))))

    def check_conv(self, user, conv_id):
        c = self.conv(conv_id)
        if c['kind'] == 'telegram' and user['role'] != 'admin' and not self.can(user, 'telegram.view'):
            raise Forbidden('you are not allowed to see Telegram history')
        if user['role'] == 'admin' or self.is_member(conv_id, 'user', user['id']):
            return c
        if c['kind'] == 'channel' and not c['private']:
            return c  # public channels can be read and joined by anyone signed in
        raise Forbidden('you are not in this conversation')

    def audience(self, conv_id):
        c = self.db.one('SELECT kind, private FROM conversations WHERE id = ?', (conv_id,))
        if c and c['kind'] == 'channel' and not c['private']:
            return None
        rows = self.db.q("SELECT member_id FROM members WHERE conv_id=? AND member_type='user'", (conv_id,))
        return {int(r['member_id']) for r in rows} | self.admins()

    def members(self, conv_id):
        return self.db.q('SELECT member_type, member_id, last_read_id FROM members WHERE conv_id = ? ORDER BY joined', (conv_id,))

    def conv_public(self, c, user=None):
        out = dict(c)
        out['private'], out['archived'] = bool(c['private']), bool(c['archived'])
        mem = self.members(c['id'])
        out['members'] = [{'type': m['member_type'], 'id': m['member_id']} for m in mem]
        if user:
            mine = next((m for m in mem if m['member_type'] == 'user' and m['member_id'] == str(user['id'])), None)
            out['joined'] = bool(mine)
            last_read = mine['last_read_id'] if mine else 0
            row = self.db.one("SELECT COUNT(*) n FROM messages WHERE conv_id=? AND id>? AND author_type!='system' AND NOT (author_type='user' AND author_id=?)",
                              (c['id'], last_read, str(user['id']))) if mine else {'n': 0}
            out['unread'] = row['n']
            out['last_read_id'] = last_read
        last = self.db.one('SELECT author_type, author_id, text, created FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT 1', (c['id'],))
        out['preview'] = {'author_type': last['author_type'], 'author_id': last['author_id'],
                          'text': last['text'][:140], 'created': last['created']} if last else None
        if c['kind'] == 'task':
            t = self.db.one('SELECT id, status FROM tasks WHERE conv_id = ?', (c['id'],))
            out['task'] = t
        return out

    def list_conversations(self, user):
        if user['role'] == 'admin':
            # Admins see their own chats and every channel; other people's private chats stay out of the
            # sidebar (reachable from the admin page) so the list stays usable.
            rows = self.db.q("SELECT c.* FROM conversations c WHERE c.kind='channel' OR EXISTS (SELECT 1 FROM members m "
                             "WHERE m.conv_id=c.id AND m.member_type='user' AND m.member_id=?) ORDER BY COALESCE(last_message_at, created) DESC",
                             (str(user['id']),))
        else:
            rows = self.db.q("SELECT c.* FROM conversations c WHERE (c.kind='channel' AND c.private=0) OR EXISTS (SELECT 1 FROM members m "
                             "WHERE m.conv_id=c.id AND m.member_type='user' AND m.member_id=?) ORDER BY COALESCE(last_message_at, created) DESC",
                             (str(user['id']),))
        if not self.can(user, 'telegram.view'):
            rows = [c for c in rows if c['kind'] != 'telegram']
        return [self.conv_public(c, user) for c in rows]

    def _add_member(self, conv_id, mtype, mid):
        self.db.run('INSERT OR IGNORE INTO members (conv_id, member_type, member_id, joined) VALUES (?,?,?,?)',
                    (conv_id, mtype, str(mid), now()))

    def create_dm(self, user, agent_id, title=''):
        a = self.agent(agent_id)
        if not self.can_use_agent(user, a):
            raise Forbidden('you cannot use this agent')
        cid = self.db.run('INSERT INTO conversations (kind, name, created_by, created, reply_mode, lead_agent, private) VALUES (?,?,?,?,?,?,1)',
                          ('dm', title[:120], user['id'], now(), 'lead', a['id']))
        self._add_member(cid, 'user', user['id'])
        self._add_member(cid, 'agent', a['id'])
        return self._announce_conv(cid)

    def create_channel(self, user, name, topic='', agents=(), users=(), reply_mode='lead', lead_agent=None, private=False):
        self.need(user, 'channels.create', 'start channels')
        name = re.sub(r'[^a-z0-9_-]+', '-', (name or '').strip().lower()).strip('-')[:60]
        if not name:
            raise Invalid('channel name is required')
        if reply_mode not in REPLY_MODES:
            raise Invalid('unknown reply mode')
        if self.db.one("SELECT 1 FROM conversations WHERE kind='channel' AND name=? AND archived=0", (name,)):
            raise Invalid('a channel called #%s already exists' % name)
        picked = []
        for agent_id in agents:
            a = self.agent(agent_id)
            if not self.can_use_agent(user, a):
                raise Forbidden('you cannot add %s' % a['name'])
            picked.append(a['id'])
        if lead_agent and lead_agent not in picked:
            raise Invalid('the lead agent must be in the channel')
        if not lead_agent and picked and reply_mode == 'lead':
            lead_agent = picked[0]
        cid = self.db.run('INSERT INTO conversations (kind, name, topic, created_by, created, reply_mode, lead_agent, private) VALUES (?,?,?,?,?,?,?,?)',
                          ('channel', name, (topic or '')[:300], user['id'], now(), reply_mode, lead_agent, 1 if private else 0))
        self._add_member(cid, 'user', user['id'])
        for uid in users:
            if self.db.one('SELECT 1 FROM users WHERE id=?', (int(uid),)):
                self._add_member(cid, 'user', int(uid))
        for agent_id in picked:
            self._add_member(cid, 'agent', agent_id)
        self.system(cid, '%s created #%s' % (user['name'], name))
        return self._announce_conv(cid)

    def update_conv(self, user, conv_id, changes):
        c = self.check_conv(user, conv_id)
        if c['kind'] == 'dm' and user['role'] != 'admin' and c['created_by'] != user['id']:
            raise Forbidden('not your chat')
        sets, args, notes = [], [], []
        if 'name' in changes:
            name = (changes['name'] or '').strip()[:120]
            if c['kind'] == 'channel':
                name = re.sub(r'[^a-z0-9_-]+', '-', name.lower()).strip('-')[:60]
                if not name:
                    raise Invalid('channel name is required')
                notes.append('renamed the channel to #%s' % name)
            sets.append('name=?'); args.append(name)
        if 'topic' in changes:
            sets.append('topic=?'); args.append((changes['topic'] or '')[:300])
        if 'reply_mode' in changes:
            if changes['reply_mode'] not in REPLY_MODES:
                raise Invalid('unknown reply mode')
            sets.append('reply_mode=?'); args.append(changes['reply_mode'])
            notes.append({'lead': 'set replies to: mentioned agents, otherwise the lead',
                          'all': 'set replies to: every agent answers', 'mention': 'set replies to: only mentioned agents'}[changes['reply_mode']])
        if 'lead_agent' in changes:
            lead = changes['lead_agent'] or None
            if lead and not self.is_member(conv_id, 'agent', lead):
                raise Invalid('the lead agent must be in the channel')
            sets.append('lead_agent=?'); args.append(lead)
            if lead:
                notes.append('made %s the lead agent' % self.agent(lead)['name'])
        if 'agent' in changes and c['kind'] == 'dm':
            # Hand a private chat to another agent. The new agent is shown the earlier messages.
            a = self.agent(changes['agent'])
            if not self.can_use_agent(user, a):
                raise Forbidden('you cannot use this agent')
            if a['id'] != c['lead_agent']:
                self.db.run("DELETE FROM members WHERE conv_id=? AND member_type='agent'", (conv_id,))
                self._add_member(conv_id, 'agent', a['id'])
                sets.append('lead_agent=?'); args.append(a['id'])
                notes.append('switched this chat to %s' % a['name'])
        if 'archived' in changes:
            sets.append('archived=?'); args.append(1 if changes['archived'] else 0)
        if 'private' in changes and c['kind'] == 'channel':
            sets.append('private=?'); args.append(1 if changes['private'] else 0)
        if sets:
            self.db.run('UPDATE conversations SET %s WHERE id=?' % ', '.join(sets), (*args, conv_id))
        for note in notes:
            self.system(conv_id, '%s %s' % (user['name'], note))
        return self._announce_conv(conv_id)

    def add_members(self, user, conv_id, agents=(), users=()):
        c = self.check_conv(user, conv_id)
        if c['kind'] != 'channel':
            raise Invalid('people and agents can only be added to channels')
        for agent_id in agents:
            a = self.agent(agent_id)
            if not self.can_use_agent(user, a):
                raise Forbidden('you cannot add %s' % a['name'])
            if not self.is_member(conv_id, 'agent', a['id']):
                self._add_member(conv_id, 'agent', a['id'])
                self.system(conv_id, '%s added %s' % (user['name'], a['name']))
        for uid in users:
            u = self.db.one('SELECT * FROM users WHERE id=?', (int(uid),))
            if u and not self.is_member(conv_id, 'user', u['id']):
                self._add_member(conv_id, 'user', u['id'])
                self.system(conv_id, '%s added %s' % (user['name'], u['name']) if u['id'] != user['id'] else '%s joined' % u['name'])
        return self._announce_conv(conv_id)

    def remove_member(self, user, conv_id, mtype, mid):
        c = self.check_conv(user, conv_id)
        if c['kind'] != 'channel':
            raise Invalid('only channel members can be removed')
        self.db.run('DELETE FROM members WHERE conv_id=? AND member_type=? AND member_id=?', (conv_id, mtype, str(mid)))
        if mtype == 'agent':
            if c['lead_agent'] == mid:
                self.db.run('UPDATE conversations SET lead_agent=NULL WHERE id=?', (conv_id,))
            name = self.agent(mid)['name']
        else:
            u = self.db.one('SELECT name FROM users WHERE id=?', (int(mid),))
            name = u['name'] if u else 'someone'
        self.system(conv_id, ('%s left' % name) if mtype == 'user' and str(user['id']) == str(mid) else '%s removed %s' % (user['name'], name))
        return self._announce_conv(conv_id)

    def delete_conv(self, user, conv_id):
        c = self.check_conv(user, conv_id)
        if user['role'] != 'admin' and c['created_by'] != user['id']:
            raise Forbidden('only the person who started it or an admin can delete this')
        audience = self.audience(conv_id)
        self.db.run('DELETE FROM conversations WHERE id=?', (conv_id,))
        self.hub.publish('conversation_deleted', {'id': conv_id}, audience)

    def _announce_conv(self, conv_id):
        c = self.conv(conv_id)
        self.hub.publish('conversation', self.conv_public(c), self.audience(conv_id))
        return c

    def mark_read(self, user, conv_id, last_id):
        self.db.run("UPDATE members SET last_read_id=MAX(last_read_id, ?) WHERE conv_id=? AND member_type='user' AND member_id=?",
                    (int(last_id), conv_id, str(user['id'])))

    # ---------------------------------------------------------------- messages
    def msg_public(self, m):
        out = dict(m)
        out['files'] = loads(m['files'], [])
        if m.get('reply_to'):
            r = self.db.one('SELECT id, author_type, author_id, text FROM messages WHERE id=?', (m['reply_to'],))
            out['reply'] = {'id': m['reply_to'], 'quote': m.get('reply_quote') or (_short(r['text'], 300) if r else ''),
                            'author': self._author_name(r, {}) if r else '', 'author_type': r['author_type'] if r else '',
                            'author_id': r['author_id'] if r else '', 'gone': not r}
        if m['author_type'] == 'user' and self.db.one('SELECT 1 FROM spoken_messages WHERE message_id=?', (m['id'],)):
            out['spoken'] = True
        if m['author_type'] == 'external':
            row = self.db.one('SELECT name FROM external_names WHERE message_id=?', (m['id'],))
            out['author_name'] = row['name'] if row else 'Telegram'
        return out

    def messages(self, user, conv_id, before=None, limit=60):
        self.check_conv(user, conv_id)
        limit = max(1, min(200, int(limit)))
        if before:
            rows = self.db.q('SELECT * FROM messages WHERE conv_id=? AND id<? ORDER BY id DESC LIMIT ?', (conv_id, int(before), limit))
        else:
            rows = self.db.q('SELECT * FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?', (conv_id, limit))
        rows.reverse()
        active = self.db.q("SELECT id, agent_id, state, trigger_id, error FROM deliveries WHERE conv_id=? AND state IN ('queued','sent','working')", (conv_id,))
        return {'messages': [self.msg_public(m) for m in rows], 'deliveries': active, 'more': len(rows) == limit}

    def _insert_message(self, conv_id, author_type, author_id, text, status='done', files=(), delivery_id=None, reply_to=None, reply_quote=''):
        mid = self.db.run('INSERT INTO messages (conv_id, author_type, author_id, text, status, delivery_id, files, created, reply_to, reply_quote) '
                          'VALUES (?,?,?,?,?,?,?,?,?,?)',
                          (conv_id, author_type, str(author_id), text, status, delivery_id, json.dumps(list(files)), now(), reply_to, reply_quote or ''))
        self.db.run('UPDATE conversations SET last_message_at=? WHERE id=?', (now(), conv_id))
        m = self.db.one('SELECT * FROM messages WHERE id=?', (mid,))
        self.hub.publish('message', self.msg_public(m), self.audience(conv_id))
        return m

    def system(self, conv_id, text):
        return self._insert_message(conv_id, 'system', '', text)

    def post(self, user, conv_id, text, file_ids=(), spoken=False, reply_to=None, reply_quote=''):
        c = self.check_conv(user, conv_id)
        if c['kind'] == 'telegram':
            raise Invalid('Telegram history is read-only here. Start a new chat with the agent to continue.')
        if c['archived']:
            raise Invalid('this conversation is archived')
        text = clean_text(text)
        files = self._claim_files(user, conv_id, file_ids)
        if not text and not files:
            raise Invalid('write a message first')
        if not self.is_member(conv_id, 'user', user['id']):
            self._add_member(conv_id, 'user', user['id'])  # posting in a public channel joins it
        if c['kind'] == 'dm' and not c['name']:
            self.db.run('UPDATE conversations SET name=? WHERE id=?', (_title(text or files[0]['name']), conv_id))
            self._announce_conv(conv_id)
        reply = None
        if reply_to:
            reply = self.db.one('SELECT id, text FROM messages WHERE id=? AND conv_id=?', (int(reply_to), conv_id))
            if not reply:
                raise Invalid('the message you replied to is not in this conversation')
            reply_quote = clean_text(str(reply_quote or ''), 1000)
            if reply_quote and reply_quote not in ' '.join(reply['text'].split()) and reply_quote not in reply['text']:
                reply_quote = ''  # only text that's really in that message
        m = self._insert_message(conv_id, 'user', user['id'], text, files=files,
                                 reply_to=reply['id'] if reply else None, reply_quote=reply_quote if reply else '')
        if spoken:
            self.db.run('INSERT OR IGNORE INTO spoken_messages VALUES (?)', (m['id'],))
            self.hub.publish('message_update', self.msg_public(m), self.audience(conv_id))  # now with the mic mark
        self.mark_read(user, conv_id, m['id'])
        self.route(self.conv(conv_id), m, user=user)
        return self.msg_public(m)

    def _claim_files(self, user, conv_id, file_ids):
        out = []
        for fid in list(file_ids)[:10]:
            f = self.db.one('SELECT * FROM files WHERE id=? AND user_id=?', (str(fid), user['id']))
            if not f:
                raise Invalid('attachment not found')
            self.db.run('UPDATE files SET conv_id=? WHERE id=?', (conv_id, f['id']))
            out.append({'id': f['id'], 'name': f['name'], 'mime': f['mime'], 'size': f['size']})
        return out

    def edit_message(self, user, message_id, text):
        m = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
        if not m or m['author_type'] != 'user' or m['author_id'] != str(user['id']):
            raise Forbidden('you can only edit your own messages')
        text = clean_text(text)
        self.db.run('UPDATE messages SET text=?, edited=? WHERE id=?', (text, now(), message_id))
        m = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
        self.hub.publish('message_update', self.msg_public(m), self.audience(m['conv_id']))
        return self.msg_public(m)

    def delete_message(self, user, message_id):
        m = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
        if not m:
            raise NotFound('no such message')
        if user['role'] != 'admin' and not (m['author_type'] == 'user' and m['author_id'] == str(user['id'])):
            raise Forbidden('you can only delete your own messages')
        audience = self.audience(m['conv_id'])
        self.db.run('DELETE FROM messages WHERE id=?', (message_id,))
        self.hub.publish('message_deleted', {'id': message_id, 'conv_id': m['conv_id']}, audience)

    def search(self, user, query, limit=50):
        query = (query or '').strip()
        if len(query) < 2:
            return []
        like = '%' + query.replace('%', r'\%').replace('_', r'\_') + '%'
        rows = self.db.q(r"SELECT m.*, c.kind, c.name conv_name FROM messages m JOIN conversations c ON c.id=m.conv_id "
                         r"WHERE m.text LIKE ? ESCAPE '\' AND m.author_type != 'system' ORDER BY m.id DESC LIMIT 400", (like,))
        out = []
        for r in rows:
            try:
                self.check_conv(user, r['conv_id'])
            except (Forbidden, NotFound):
                continue
            out.append({**self.msg_public(r), 'conv_kind': r['kind'], 'conv_name': r['conv_name']})
            if len(out) >= limit:
                break
        return out

    # ---------------------------------------------------------------- who answers
    def agent_members(self, conv_id):
        rows = self.db.q("SELECT a.* FROM members m JOIN agents a ON a.id=m.member_id WHERE m.conv_id=? AND m.member_type='agent' AND a.disabled=0",
                         (conv_id,))
        return rows

    def mentioned(self, conv_id, text, user=None, exclude=None):
        """Agents named with @handle. A person mentioning a visible agent outside the channel brings it in."""
        handles = {h.lower().rstrip('.') for h in MENTION.findall(text or '')}
        members = self.agent_members(conv_id)
        if handles & EVERYONE:
            return [a for a in members if a['id'] != exclude], True
        found = [a for a in members if a['handle'].lower() in handles and a['id'] != exclude]
        c = self.conv(conv_id)
        if user and c['kind'] == 'channel':
            have = {a['handle'].lower() for a in members}
            for handle in handles - have:
                a = self.db.one('SELECT * FROM agents WHERE handle=? AND disabled=0', (handle,))
                if a and self.can_use_agent(user, a):
                    self._add_member(conv_id, 'agent', a['id'])
                    self.system(conv_id, '%s added %s' % (user['name'], a['name']))
                    self._announce_conv(conv_id)
                    found.append(a)
        return found, False

    def route(self, c, message, user=None, from_agent=None, depth=0):
        """Queue a delivery for each agent that should answer this message."""
        if depth > MAX_DEPTH or c['archived']:
            return []
        exclude = from_agent['id'] if from_agent else None
        mentioned, everyone = self.mentioned(c['id'], message['text'], user=user, exclude=exclude)
        members = self.agent_members(c['id'])
        if c['kind'] == 'dm':
            targets = [] if from_agent else members[:1]
        elif c['kind'] == 'task':
            t = self.db.one('SELECT assignee FROM tasks WHERE conv_id=?', (c['id'],))
            targets = mentioned or ([a for a in members if t and a['id'] == t['assignee']] if not from_agent else [])
        elif from_agent:
            targets = mentioned  # agents only pull others in by name
        elif mentioned:
            targets = mentioned
        elif c['reply_mode'] == 'all':
            targets = members
        elif c['reply_mode'] == 'lead':
            targets = [a for a in members if a['id'] == c['lead_agent']]
        else:
            targets = []
        out = []
        for a in targets:
            if a['id'] == exclude:
                continue
            out.append(self.queue_delivery(a, c['id'], message['id'], depth))
        return out

    def queue_delivery(self, a, conv_id, trigger_id, depth=0):
        did = self.db.run('INSERT INTO deliveries (agent_id, conv_id, trigger_id, depth, created) VALUES (?,?,?,?,?)',
                          (a['id'], conv_id, trigger_id, depth, now()))
        self._announce_delivery(did)
        self.hub.wake_agent(a['id'])
        return did

    def _announce_delivery(self, did):
        d = self.db.one('SELECT id, agent_id, conv_id, state, trigger_id, error FROM deliveries WHERE id=?', (did,))
        if d:
            self.hub.publish('delivery', d, self.audience(d['conv_id']))
        return d

    def retry(self, user, message_id):
        """Ask the agents again: for a failed agent reply, or the person's own last message."""
        m = self.db.one('SELECT * FROM messages WHERE id=?', (message_id,))
        if not m:
            raise NotFound('no such message')
        c = self.check_conv(user, m['conv_id'])
        if m['author_type'] == 'agent' and m['delivery_id']:
            d = self.db.one('SELECT * FROM deliveries WHERE id=?', (m['delivery_id'],))
            return [self.queue_delivery(self.agent(d['agent_id']), c['id'], d['trigger_id'], d['depth'])]
        if m['author_type'] == 'user':
            return self.route(c, m, user=user)
        raise Invalid('nothing to retry')

    def retry_delivery(self, user, delivery_id):
        d = self.db.one('SELECT * FROM deliveries WHERE id=?', (delivery_id,))
        if not d:
            raise NotFound('no such request')
        self.check_conv(user, d['conv_id'])
        return self.queue_delivery(self.agent(d['agent_id']), d['conv_id'], d['trigger_id'], d['depth'])

    def cancel(self, user, delivery_id):
        d = self.db.one('SELECT * FROM deliveries WHERE id=?', (delivery_id,))
        if not d:
            raise NotFound('no such request')
        self.check_conv(user, d['conv_id'])
        if d['state'] in ('queued', 'sent', 'working'):
            self.db.run("UPDATE deliveries SET state='cancelled', finished=? WHERE id=?", (now(), delivery_id))
            self.db.run("UPDATE messages SET status='done' WHERE delivery_id=? AND status='streaming'", (delivery_id,))
            self._announce_delivery(delivery_id)
            self.hub.wake_agent(d['agent_id'])

    def sweep(self, stale_after=1800):
        """Fail requests an agent picked up but never answered, so the page stops showing 'thinking'."""
        rows = self.db.q("SELECT id FROM deliveries WHERE state IN ('sent','working') AND picked < ?", (now() - stale_after,))
        for r in rows:
            self.db.run("UPDATE deliveries SET state='error', error=?, finished=? WHERE id=?",
                        ('No reply after %d minutes' % (stale_after // 60), now(), r['id']))
            self._announce_delivery(r['id'])

    # ---------------------------------------------------------------- what an agent is sent
    def _author_name(self, m, cache):
        key = (m['author_type'], m['author_id'])
        if key not in cache:
            if m['author_type'] == 'user':
                u = self.db.one('SELECT name FROM users WHERE id=?', (int(m['author_id']),)) if m['author_id'].isdigit() else None
                cache[key] = u['name'] if u else 'Someone'
            elif m['author_type'] == 'agent':
                a = self.db.one('SELECT name, handle FROM agents WHERE id=?', (m['author_id'],))
                cache[key] = '%s (@%s)' % (a['name'], a['handle']) if a else m['author_id']
            else:
                cache[key] = 'System'
        return cache[key]

    def payload(self, d):
        """Everything a bridge needs to hand one request to its agent, including a ready-made prompt."""
        a = self.agent(d['agent_id'])
        c = self.conv(d['conv_id'])
        trigger = self.db.one('SELECT * FROM messages WHERE id=?', (d['trigger_id'],))
        names = {}
        sender = self._author_name(trigger, names) if trigger else 'Someone'
        mine = self.db.one("SELECT MAX(id) id FROM messages WHERE conv_id=? AND author_type='agent' AND author_id=? AND id<?",
                           (c['id'], a['id'], d['trigger_id']))
        since = mine['id'] or 0
        context = self.db.q("SELECT * FROM messages WHERE conv_id=? AND id>? AND id<? AND author_type!='system' ORDER BY id DESC LIMIT ?",
                            (c['id'], since, d['trigger_id'], CONTEXT_MESSAGES))
        context.reverse()
        history = self.db.q("SELECT * FROM messages WHERE conv_id=? AND id<? AND author_type!='system' AND status!='error' ORDER BY id DESC LIMIT ?",
                            (c['id'], d['trigger_id'], HISTORY_MESSAGES))
        history.reverse()
        members = []
        for m in self.members(c['id']):
            if m['member_type'] == 'agent':
                ag = self.db.one('SELECT name, handle FROM agents WHERE id=?', (m['member_id'],))
                if ag and m['member_id'] != a['id']:
                    members.append({'type': 'agent', 'name': ag['name'], 'handle': ag['handle']})
            else:
                u = self.db.one('SELECT name FROM users WHERE id=?', (int(m['member_id']),))
                if u:
                    members.append({'type': 'user', 'name': u['name']})
        task = self.db.one('SELECT * FROM tasks WHERE conv_id=?', (c['id'],)) if c['kind'] == 'task' else None
        files = [{'id': f['id'], 'name': f['name'], 'mime': f['mime'], 'size': f['size'],
                  'url': '%s/bridge/v1/files/%s' % (self.public_url, f['id'])} for f in loads(trigger['files'], [])] if trigger else []
        text = trigger['text'] if trigger else ''
        endpoint = '%d:%d' % (c['id'], d['id'])
        spoken = bool(trigger and self.db.one('SELECT 1 FROM spoken_messages WHERE message_id=?', (trigger['id'],)))
        replying = None
        if trigger and trigger.get('reply_to'):
            r = self.db.one('SELECT * FROM messages WHERE id=?', (trigger['reply_to'],))
            if r:
                replying = {'author': self._author_name(r, names), 'text': trigger.get('reply_quote') or _short(r['text'], 1200),
                            'part': bool(trigger.get('reply_quote'))}
        return {
            'delivery_id': d['id'], 'endpoint': endpoint, 'agent': {'id': a['id'], 'handle': a['handle'], 'name': a['name']},
            'conversation': {'id': c['id'], 'kind': c['kind'], 'name': c['name'], 'topic': c['topic']},
            'from': {'type': trigger['author_type'] if trigger else 'user', 'name': sender},
            'text': text, 'files': files, 'members': members,
            'context': [{'author': self._author_name(m, names), 'text': m['text']} for m in context],
            'history': [{'role': 'assistant' if (m['author_type'] == 'agent' and m['author_id'] == a['id']) else 'user',
                         'name': self._author_name(m, names), 'text': m['text']} for m in history],
            'task': {'id': task['id'], 'title': task['title'], 'description': task['description'], 'status': task['status'],
                     'priority': task['priority'], 'due': task['due']} if task else None,
            'spoken': spoken, 'replying_to': replying,
            'prompt': self.prompt(a, c, sender, text, files, context, members, task, names, spoken, replying),
        }

    def prompt(self, a, c, sender, text, files, context, members, task, names, spoken=False, replying=None):
        if c['kind'] == 'dm':
            where = 'a private chat with %s' % sender
        elif c['kind'] == 'task':
            where = 'task #%d' % task['id'] if task else 'a task'
        else:
            where = 'the #%s channel' % c['name']
        lines = ['[Agent HQ Chat · %s · message from %s]' % (where, sender)]
        if c['kind'] == 'task' and task:
            lines += ['', 'You are assigned task #%d: %s (priority %s%s, status %s).' % (
                task['id'], task['title'], task['priority'], ', due ' + task['due'] if task['due'] else '', task['status'])]
            if task['description']:
                lines += ['Task details:', task['description']]
        if c['kind'] == 'channel' and c['topic']:
            lines.append('Channel topic: %s' % c['topic'])
        if context:
            lines += ['', 'Messages in this conversation since your last reply:']
            lines += ['- %s: %s' % (self._author_name(m, names), _short(m['text'])) for m in context]
        if replying:
            lines += ['', '%s is replying to %s %s:' % (sender, 'this part of' if replying['part'] else 'this message from', replying['author'])]
            lines += ['> ' + ln for ln in replying['text'].splitlines()[:30]]
        lines += ['', 'Message:', text or '(no text)']
        if files:
            lines += ['', 'Attached files (download with your Agent HQ Chat token):']
            lines += ['- %s (%s, %d bytes): %s' % (f['name'], f['mime'], f['size'], f['url']) for f in files]
        lines.append('')
        if c['kind'] == 'channel':
            others = ', '.join('@%s (%s)' % (m['handle'], m['name']) for m in members if m['type'] == 'agent')
            people = ', '.join(m['name'] for m in members if m['type'] == 'user')
            lines.append('This is a shared channel. People here: %s.%s' % (people or 'none', ' Other agents here: %s.' % others if others else ''))
            lines.append('Your reply is posted to the channel. Mention another agent with @handle only when you need them to act.')
        if spoken:
            from .voice import VOICE_NOTE
            lines.append(VOICE_NOTE)
        if c['kind'] == 'task':
            lines.append('Post progress and results as replies. When you start, include a line [task:doing]; when finished, '
                         'end with [task:done], or [task:review] if a person should check it first.')
        return '\n'.join(lines)

    # ---------------------------------------------------------------- bridge side
    def heartbeat(self, a, info=None):
        sets, args = ['last_seen=?'], [now()]
        if isinstance(info, dict) and info and 'check' not in info:  # a manual "check" keeps the running bridge's info
            sets.append('bridge_info=?'); args.append(json.dumps({k: str(v)[:200] for k, v in list(info.items())[:20]}))
        was_online = a['last_seen'] and now() - a['last_seen'] < ONLINE_SECONDS
        self.db.run('UPDATE agents SET %s WHERE id=?' % ', '.join(sets), (*args, a['id']))
        if not was_online:
            self.hub.publish('agent', self.agent_public(self.agent(a['id'])))

    def take(self, a, limit=5):
        """Queued requests for this agent, marked as sent."""
        rows = self.db.q("SELECT * FROM deliveries WHERE agent_id=? AND state='queued' ORDER BY id LIMIT ?", (a['id'], limit))
        out = []
        for d in rows:
            self.db.run("UPDATE deliveries SET state='sent', picked=? WHERE id=? AND state='queued'", (now(), d['id']))
            self._announce_delivery(d['id'])
            out.append(self.payload(d))
        return out

    def cancelled_for(self, a):
        rows = self.db.q("SELECT id FROM deliveries WHERE agent_id=? AND state='cancelled' AND finished > ?", (a['id'], now() - 600))
        return [r['id'] for r in rows]

    def _delivery_for(self, a, delivery_id):
        d = self.db.one('SELECT * FROM deliveries WHERE id=? AND agent_id=?', (int(delivery_id), a['id']))
        if not d:
            raise NotFound('no such request for this agent')
        return d

    def working(self, a, delivery_id):
        d = self._delivery_for(a, delivery_id)
        if d['state'] in ('queued', 'sent'):
            self.db.run("UPDATE deliveries SET state='working', picked=COALESCE(picked, ?) WHERE id=?", (now(), d['id']))
            self._announce_delivery(d['id'])

    def reply(self, a, delivery_id, text, final=True, replace=True):
        """A reply to one request. Streaming bridges call this repeatedly with final=False and the text so far."""
        d = self._delivery_for(a, delivery_id)
        if d['state'] == 'cancelled':
            return None
        text = clean_text(text, 60000)
        text, status_mark = _task_mark(text)
        existing = self.db.one("SELECT * FROM messages WHERE delivery_id=? AND status='streaming' ORDER BY id DESC LIMIT 1", (d['id'],))
        status = 'done' if final else 'streaming'
        if existing:
            new_text = text if replace else existing['text'] + text
            self.db.run('UPDATE messages SET text=?, status=? WHERE id=?', (new_text, status, existing['id']))
            m = self.db.one('SELECT * FROM messages WHERE id=?', (existing['id'],))
            self.hub.publish('message_update', self.msg_public(m), self.audience(d['conv_id']))
        else:
            if not text and final:
                return None
            m = self._insert_message(d['conv_id'], 'agent', a['id'], text, status=status, delivery_id=d['id'])
        if d['state'] not in ('done', 'error'):
            self.db.run("UPDATE deliveries SET state=?, finished=? WHERE id=?", ('done' if final else 'working', now() if final else None, d['id']))
            self._announce_delivery(d['id'])
        if status_mark:
            self._agent_task_status(a, d['conv_id'], status_mark)
        if final:
            self.route(self.conv(d['conv_id']), m, from_agent=a, depth=d['depth'] + 1)
        return self.msg_public(m)

    def fail(self, a, delivery_id, error):
        d = self._delivery_for(a, delivery_id)
        self.db.run("UPDATE deliveries SET state='error', error=?, finished=? WHERE id=?", (str(error)[:500], now(), d['id']))
        self.db.run("UPDATE messages SET status='error' WHERE delivery_id=? AND status='streaming'", (d['id'],))
        self._announce_delivery(d['id'])

    def send(self, a, endpoint, text):
        """A message from the agent's own channel script (Zylos c4-send). The endpoint is 'conv' or 'conv:delivery'."""
        conv_part, _, delivery_part = str(endpoint).partition(':')
        if not conv_part.isdigit():
            raise Invalid('bad endpoint')
        conv_id = int(conv_part)
        if not self.is_member(conv_id, 'agent', a['id']):
            raise Forbidden('this agent is not in that conversation')
        if delivery_part.isdigit():
            d = self.db.one('SELECT * FROM deliveries WHERE id=? AND agent_id=? AND conv_id=?', (int(delivery_part), a['id'], conv_id))
            if d and d['state'] in ('queued', 'sent', 'working'):
                return self.reply(a, d['id'], text, final=True)
            if d and d['state'] == 'cancelled':
                return None
        text, status_mark = _task_mark(clean_text(text, 60000))
        if not text:
            return None
        m = self._insert_message(conv_id, 'agent', a['id'], text)
        if status_mark:
            self._agent_task_status(a, conv_id, status_mark)
        self.route(self.conv(conv_id), m, from_agent=a, depth=1)
        return self.msg_public(m)

    # ---------------------------------------------------------------- export
    def export(self, user, conv_id, fmt='md'):
        """The whole conversation as Markdown, plain text or JSON: (filename, media type, body)."""
        c = self.check_conv(user, conv_id)
        self.need(user, 'chats.export', 'download chats')
        if fmt not in ('md', 'txt', 'json'):
            raise Invalid('format is md, txt or json')
        rows = self.db.q('SELECT * FROM messages WHERE conv_id=? ORDER BY created, id', (conv_id,))
        names = {}
        title = c['name'] or ('Chat %d' % c['id'])
        kind = {'dm': 'Private chat', 'channel': 'Channel', 'task': 'Task', 'telegram': 'Telegram history'}.get(c['kind'], c['kind'])
        stamp = time.strftime('%Y-%m-%d', time.localtime())
        safe = re.sub(r'[^A-Za-z0-9._-]+', '-', title).strip('-')[:60] or 'chat'
        fname = 'example-%s-%s.%s' % (safe, stamp, fmt)

        def who(m):
            if m['author_type'] == 'external':
                row = self.db.one('SELECT name FROM external_names WHERE message_id=?', (m['id'],))
                return (row['name'] if row else 'Telegram') + ' (Telegram)'
            return self._author_name(m, names)

        def when(t):
            return time.strftime('%Y-%m-%d %H:%M', time.localtime(t))

        if fmt == 'json':
            body = json.dumps({'conversation': {'id': c['id'], 'kind': c['kind'], 'name': title, 'topic': c['topic']},
                               'exported': time.time(), 'exported_by': user['name'],
                               'messages': [{'id': m['id'], 'at': m['created'], 'author_type': m['author_type'], 'author': who(m),
                                             'text': m['text'], 'files': [f['name'] for f in loads(m['files'], [])],
                                             'status': m['status']} for m in rows]}, ensure_ascii=False, indent=1)
            return fname, 'application/json', body
        lines = []
        count = '%d message%s' % (len(rows), '' if len(rows) == 1 else 's')
        if fmt == 'md':
            lines += ['# %s' % title, '', '%s · %s · downloaded %s by %s' % (kind, count, when(time.time()), user['name']), '']
        else:
            lines += [title, '%s, %s, downloaded %s by %s' % (kind, count, when(time.time()), user['name']), '']
        for m in rows:
            files = loads(m['files'], [])
            extra = (' [files: %s]' % ', '.join(f['name'] for f in files)) if files else ''
            if m['author_type'] == 'system':
                lines += ['_%s · %s_' % (when(m['created']), m['text']) if fmt == 'md' else '[%s] -- %s' % (when(m['created']), m['text']), '']
            elif fmt == 'md':
                lines += ['**%s** · %s%s' % (who(m), when(m['created']), extra), '', m['text'], '', '---', '']
            else:
                lines += ['[%s] %s:%s' % (when(m['created']), who(m), extra), m['text'], '']
        return fname, 'text/markdown' if fmt == 'md' else 'text/plain', '\n'.join(lines)

    # ---------------------------------------------------------------- live activity
    def save_activity(self, a, steps, updated=None):
        if not isinstance(steps, list):
            raise Invalid('steps must be a list')
        clean = []
        for s in steps[-60:]:
            if isinstance(s, dict) and s.get('kind') in ('input', 'thinking', 'text', 'tool'):
                clean.append({'at': str(s.get('at') or '')[:40], 'kind': s['kind'], 'tool': str(s.get('tool') or '')[:60],
                              'text': clean_text(str(s.get('text') or ''), 2000)})
        upd = float(updated) if isinstance(updated, (int, float)) else now()
        self.db.run('INSERT INTO agent_activity (agent_id, steps, log_updated, received) VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET '
                    'steps=excluded.steps, log_updated=excluded.log_updated, received=excluded.received', (a['id'], json.dumps(clean), upd, now()))
        viewers = {u['id'] for u in self.db.q('SELECT * FROM users WHERE disabled=0') if self._may_watch(u, a)}
        self.hub.publish('activity', {'agent_id': a['id'], 'steps': clean, 'log_updated': upd, 'received': now()}, viewers)

    def _may_watch(self, user, a):
        return user['role'] == 'admin' or a['owner_user_id'] == user['id'] or (self.can(user, 'activity.view') and self.can_use_agent(user, a))

    def activity(self, user, agent_id):
        a = self.agent(agent_id)
        if not self._may_watch(user, a):
            raise Forbidden('you are not allowed to watch this agent')
        row = self.db.one('SELECT * FROM agent_activity WHERE agent_id=?', (a['id'],))
        if not row:
            return {'agent_id': a['id'], 'steps': [], 'log_updated': None, 'received': None,
                    'note': 'No activity yet. Its bridge may be older than 0.3, or it is a department agent.'}
        return {'agent_id': a['id'], 'steps': loads(row['steps'], []), 'log_updated': row['log_updated'], 'received': row['received']}

    # ---------------------------------------------------------------- Telegram history
    def telegram_import(self, a, items):
        """Messages the bridge copied from the agent's Telegram log (read-only history).

        One conversation per Telegram chat, shared by the agent and its owner; admins can open it too.
        Rows already copied (by c4 id) are skipped, so the bridge can safely send a batch twice."""
        if not isinstance(items, list) or len(items) > 1000:
            raise Invalid('send up to 1000 messages')
        added, touched = 0, set()
        for it in items:
            if not isinstance(it, dict):
                continue
            chat_id = str(it.get('chat') or '').strip()[:40]
            text = clean_text(str(it.get('text') or ''), 20000)
            try:
                src = int(it.get('id'))
            except (TypeError, ValueError):
                continue
            if not chat_id or not text:
                continue
            tc = self.db.one('SELECT * FROM telegram_chats WHERE agent_id=? AND chat_id=?', (a['id'], chat_id))
            if not tc:
                tc = self._telegram_chat(a, chat_id, it)
            if src <= tc['last_source_id']:
                continue
            if not it.get('out') and it.get('from') and not it.get('group'):
                c = self.conv(tc['conv_id'])
                if c['name'].startswith(chat_id + ' '):  # named after the bare chat id: use the person's Telegram name
                    self.db.run('UPDATE conversations SET name=? WHERE id=?', (('%s · Telegram' % str(it['from'])[:80]), c['id']))
            created = _ts(it.get('at')) or now()
            if it.get('out'):
                mid = self.db.run("INSERT INTO messages (conv_id, author_type, author_id, text, status, files, created) VALUES (?,?,?,?,?,?,?)",
                                  (tc['conv_id'], 'agent', a['id'], text, 'done', '[]', created))
            else:
                mid = self.db.run("INSERT INTO messages (conv_id, author_type, author_id, text, status, files, created) VALUES (?,?,?,?,?,?,?)",
                                  (tc['conv_id'], 'external', 'telegram', text, 'done', '[]', created))
                self.db.run('INSERT INTO external_names (message_id, name) VALUES (?,?)', (mid, str(it.get('from') or 'Telegram')[:80]))
            self.db.run('UPDATE telegram_chats SET last_source_id=? WHERE agent_id=? AND chat_id=?', (src, a['id'], chat_id))
            tc['last_source_id'] = src
            self.db.run('UPDATE conversations SET last_message_at=MAX(COALESCE(last_message_at, 0), ?) WHERE id=?', (created, tc['conv_id']))
            touched.add(tc['conv_id'])
            added += 1
            if len(items) <= 20:  # live sync: push to open pages; big history batches only refresh the list
                self.hub.publish('message', self.msg_public(self.db.one('SELECT * FROM messages WHERE id=?', (mid,))), self.audience(tc['conv_id']))
        for cid in touched:
            self._announce_conv(cid)
        return added

    def _telegram_chat(self, a, chat_id, it):
        group = (it.get('group') or '').strip()
        name = group or (it.get('chat_name') or '').strip() or chat_id
        title = ('%s · Telegram group' % name) if group or chat_id.startswith('-') else ('%s · Telegram' % name)
        cid = self.db.run("INSERT INTO conversations (kind, name, topic, created_by, created, reply_mode, lead_agent, private) VALUES (?,?,?,?,?,?,?,1)",
                          ('telegram', title[:120], 'Telegram chat %s' % chat_id, None, _ts(it.get('at')) or now(), 'mention', a['id']))
        self._add_member(cid, 'agent', a['id'])
        if a['owner_user_id']:
            self._add_member(cid, 'user', a['owner_user_id'])
        self.db.run('INSERT INTO telegram_chats (agent_id, chat_id, conv_id) VALUES (?,?,?)', (a['id'], chat_id, cid))
        return self.db.one('SELECT * FROM telegram_chats WHERE agent_id=? AND chat_id=?', (a['id'], chat_id))

    def telegram_owner_changed(self, agent_id):
        """Keep the agent's owner (and only them) as the person in its Telegram history."""
        a = self.agent(agent_id)
        for tc in self.db.q('SELECT conv_id FROM telegram_chats WHERE agent_id=?', (agent_id,)):
            self.db.run("DELETE FROM members WHERE conv_id=? AND member_type='user'", (tc['conv_id'],))
            if a['owner_user_id']:
                self._add_member(tc['conv_id'], 'user', a['owner_user_id'])

    def agent_file(self, a, file_id):
        f = self.db.one('SELECT * FROM files WHERE id=?', (file_id,))
        if not f or not f['conv_id'] or not self.is_member(f['conv_id'], 'agent', a['id']):
            raise NotFound('no such file')
        return f

    # ---------------------------------------------------------------- tasks
    def task_public(self, t):
        return dict(t)

    def tasks(self, user):
        rows = self.db.q('SELECT * FROM tasks ORDER BY updated DESC')
        out = []
        for t in rows:
            if user['role'] == 'admin' or t['created_by'] == user['id'] or (t['conv_id'] and self.is_member(t['conv_id'], 'user', user['id'])):
                out.append(self.task_public(t))
        return out

    def create_task(self, user, title, description='', assignee=None, priority='normal', due=None):
        self.need(user, 'tasks.create', 'give tasks')
        title = clean_text(title, 200)
        if not title:
            raise Invalid('a task needs a title')
        if priority not in ('low', 'normal', 'high'):
            raise Invalid('unknown priority')
        a = None
        if assignee:
            a = self.agent(assignee)
            if not self.can_use_agent(user, a):
                raise Forbidden('you cannot assign work to this agent')
        description = clean_text(description or '', 10000)
        cid = self.db.run("INSERT INTO conversations (kind, name, created_by, created, reply_mode, lead_agent, private) VALUES ('task',?,?,?,'lead',?,1)",
                          (title, user['id'], now(), a['id'] if a else None))
        self._add_member(cid, 'user', user['id'])
        if a:
            self._add_member(cid, 'agent', a['id'])
        tid = self.db.run('INSERT INTO tasks (title, description, priority, assignee, conv_id, created_by, due, created, updated) VALUES (?,?,?,?,?,?,?,?,?)',
                          (title, description, priority, a['id'] if a else None, cid, user['id'], due or None, now(), now()))
        brief = description or title
        m = self._insert_message(cid, 'user', user['id'], brief)
        self._announce_conv(cid)
        self._announce_task(tid)
        if a:
            self.route(self.conv(cid), m, user=user)
        return self.task_public(self.db.one('SELECT * FROM tasks WHERE id=?', (tid,)))

    def update_task(self, user, task_id, changes):
        t = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not t:
            raise NotFound('no such task')
        if t['conv_id']:
            self.check_conv(user, t['conv_id'])
        sets, args, notes = [], [], []
        if 'status' in changes:
            if changes['status'] not in TASK_STATES:
                raise Invalid('unknown status')
            if changes['status'] != t['status']:
                sets.append('status=?'); args.append(changes['status'])
                notes.append('%s moved this to %s' % (user['name'], _state_name(changes['status'])))
        for key, limit in (('title', 200), ('description', 10000)):
            if key in changes:
                sets.append('%s=?' % key); args.append(clean_text(changes[key] or '', limit))
        if 'priority' in changes:
            if changes['priority'] not in ('low', 'normal', 'high'):
                raise Invalid('unknown priority')
            sets.append('priority=?'); args.append(changes['priority'])
        if 'due' in changes:
            sets.append('due=?'); args.append(changes['due'] or None)
        new_agent = None
        if 'assignee' in changes and changes['assignee'] != t['assignee']:
            if changes['assignee']:
                new_agent = self.agent(changes['assignee'])
                if not self.can_use_agent(user, new_agent):
                    raise Forbidden('you cannot assign work to this agent')
            sets.append('assignee=?'); args.append(changes['assignee'] or None)
        if not sets:
            return self.task_public(t)
        sets.append('updated=?'); args.append(now())
        self.db.run('UPDATE tasks SET %s WHERE id=?' % ', '.join(sets), (*args, task_id))
        if 'title' in changes and t['conv_id']:
            self.db.run('UPDATE conversations SET name=? WHERE id=?', (clean_text(changes['title'], 200), t['conv_id']))
        if t['conv_id']:
            for note in notes:
                self.system(t['conv_id'], note)
            if new_agent:
                self._add_member(t['conv_id'], 'agent', new_agent['id'])
                self.db.run('UPDATE conversations SET lead_agent=? WHERE id=?', (new_agent['id'], t['conv_id']))
                self.system(t['conv_id'], '%s assigned this to %s' % (user['name'], new_agent['name']))
                first = self.db.one("SELECT * FROM messages WHERE conv_id=? AND author_type='user' ORDER BY id LIMIT 1", (t['conv_id'],))
                if first:
                    self.queue_delivery(new_agent, t['conv_id'], first['id'])
            self._announce_conv(t['conv_id'])
        return self._announce_task(task_id)

    def delete_task(self, user, task_id):
        t = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        if not t:
            raise NotFound('no such task')
        if user['role'] != 'admin' and t['created_by'] != user['id']:
            raise Forbidden('only the person who made the task or an admin can delete it')
        audience = self.audience(t['conv_id']) if t['conv_id'] else None
        self.db.run('DELETE FROM tasks WHERE id=?', (task_id,))
        if t['conv_id']:
            self.db.run('DELETE FROM conversations WHERE id=?', (t['conv_id'],))
            self.hub.publish('conversation_deleted', {'id': t['conv_id']}, audience)
        self.hub.publish('task_deleted', {'id': task_id}, audience)

    def _announce_task(self, task_id):
        t = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        self.hub.publish('task', self.task_public(t), self.audience(t['conv_id']) if t['conv_id'] else None)
        return self.task_public(t)

    def _agent_task_status(self, a, conv_id, status):
        t = self.db.one('SELECT * FROM tasks WHERE conv_id=?', (conv_id,))
        if t and t['status'] != status:
            self.db.run('UPDATE tasks SET status=?, updated=? WHERE id=?', (status, now(), t['id']))
            self.system(conv_id, '%s moved this to %s' % (a['name'], _state_name(status)))
            self._announce_task(t['id'])
            self._announce_conv(conv_id)

    # ---------------------------------------------------------------- files
    def save_file(self, user, name, mime, data):
        self.need(user, 'files.upload', 'attach files')
        if len(data) > 25 * 1024 * 1024:
            raise Invalid('files can be up to 25 MB')
        fid = secrets.token_urlsafe(12)
        (self.files_dir / fid).write_bytes(data)
        name = re.sub(r'[\x00-\x1f/\\]', '_', name or 'file')[:200]
        self.db.run('INSERT INTO files (id, user_id, name, mime, size, created) VALUES (?,?,?,?,?,?)',
                    (fid, user['id'], name, (mime or 'application/octet-stream')[:100], len(data), now()))
        return {'id': fid, 'name': name, 'mime': mime, 'size': len(data)}

    def user_file(self, user, file_id):
        f = self.db.one('SELECT * FROM files WHERE id=?', (file_id,))
        if not f:
            raise NotFound('no such file')
        if f['conv_id']:
            self.check_conv(user, f['conv_id'])
        elif f['user_id'] != user['id']:
            raise NotFound('no such file')
        return f


def _ts(value):
    """'2026-11-08 16:02:40' (UTC, as Zylos stores it) -> epoch seconds."""
    try:
        return float(calendar.timegm(time.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')))
    except (TypeError, ValueError):
        return None

def _title(text):
    text = ' '.join(re.sub(r'[*_`#>~]+|\[([^\]]*)\]\([^)]*\)', lambda m: m.group(1) or '', text).split())
    return text[:60] + ('…' if len(text) > 60 else '')


def _short(text, limit=1500):
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + ' …'


def _task_mark(text):
    marks = TASK_MARK.findall(text)
    if not marks:
        return text, None
    return TASK_MARK.sub('', text).strip(), marks[-1].lower()


def _state_name(status):
    return {'todo': 'To do', 'doing': 'In progress', 'review': 'Review', 'done': 'Done'}[status]
