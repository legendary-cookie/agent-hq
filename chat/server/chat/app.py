"""HTTP: the web app's API (/api), the agent bridge (/bridge/v1), and the built web page."""
import asyncio
import contextlib
import json
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from . import __version__, auth, perms, voice
from .db import Db, loads, now
from .events import Hub
from .responders import Responders
from .service import Chat, Forbidden, Invalid, NotFound  # noqa: F401

COOKIE = 'ac_session'
HANDLE = re.compile(r'[a-z0-9][a-z0-9_.-]{0,30}')
KINDS = ('zylos', 'hermes', 'openai', 'demo')
GROUPS = ('Management', 'Staff', 'Department', 'Specialist', 'Other')


def create_app(data_dir=None, web_dir=None, public_url=None, secure_cookie=None):
    data_dir = Path(data_dir or os.environ.get('CHAT_DATA', './data'))
    files_dir = data_dir / 'files'
    files_dir.mkdir(parents=True, exist_ok=True)
    web_dir = Path(web_dir or os.environ.get('CHAT_WEB', Path(__file__).resolve().parents[2] / 'web' / 'dist'))
    public_url = public_url if public_url is not None else os.environ.get('CHAT_PUBLIC_URL', '')
    secure_cookie = secure_cookie if secure_cookie is not None else os.environ.get('CHAT_SECURE_COOKIE', '1') == '1'

    db = Db(data_dir / 'chat.db')
    hub = Hub()
    chat = Chat(db, hub, files_dir, public_url)
    responders = Responders(chat)
    speaker = voice.Voice(chat)
    @contextlib.asynccontextmanager
    async def lifespan(_):
        hub.attach(asyncio.get_running_loop())
        responders.start()
        yield
        responders.stop.set()

    app = FastAPI(title='Agent HQ Chat', version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.chat = chat

    @app.exception_handler(Forbidden)
    async def _forbidden(_, exc):
        return JSONResponse({'error': str(exc)}, 403)

    @app.exception_handler(NotFound)
    async def _missing(_, exc):
        return JSONResponse({'error': str(exc)}, 404)

    @app.exception_handler(Invalid)
    async def _invalid(_, exc):
        return JSONResponse({'error': str(exc)}, 400)

    @app.exception_handler(ValueError)
    async def _value(_, exc):
        return JSONResponse({'error': str(exc)}, 400)

    @app.middleware('http')
    async def _headers(request, call_next):
        # Requests that change things must come from this page, not another site.
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.url.path.startswith('/api/') \
                and request.headers.get('x-requested-with') != 'agent-hq-chat':
            return JSONResponse({'error': 'missing request header'}, 403)
        resp = await call_next(request)
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        return resp

    # ------------------------------------------------------------ helpers
    def current_user(request: Request):
        user = auth.session_user(db, request.cookies.get(COOKIE))
        if not user:
            raise HTTPException(401, 'sign in first')
        return user

    def admin_user(user=Depends(current_user)):
        if user['role'] != 'admin':
            raise HTTPException(403, 'admins only')
        return user

    def current_agent(request: Request):
        header = request.headers.get('authorization', '')
        a = auth.agent_for_token(db, header[7:] if header.lower().startswith('bearer ') else '')
        if not a:
            raise HTTPException(401, 'bad agent token')
        return a

    async def body(request):
        try:
            data = await request.json()
        except ValueError:
            raise Invalid('send JSON') from None
        if not isinstance(data, dict):
            raise Invalid('send a JSON object')
        return data

    # ------------------------------------------------------------ sign-in
    @app.post('/api/login')
    async def login(request: Request, response: Response):
        data = await body(request)
        user = await asyncio.to_thread(auth.login, db, str(data.get('username', '')).strip(), str(data.get('password', '')))
        token = auth.new_session(db, user['id'])
        response.set_cookie(COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite='lax', secure=secure_cookie, path='/')
        return {'user': chat.user_public(user)}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        auth.end_session(db, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path='/')
        return {'ok': True}

    @app.post('/api/me/password')
    async def change_password(request: Request, user=Depends(current_user)):
        data = await body(request)
        if not auth.check_password(str(data.get('current', '')), user['pw_hash']):
            raise Invalid('your current password is wrong')
        new = str(data.get('new', ''))
        if len(new) < 10:
            raise Invalid('use at least 10 characters')
        db.run('UPDATE users SET pw_hash=?, pw_sealed=? WHERE id=?', (auth.hash_password(new), None, user['id']))
        db.run('DELETE FROM sessions WHERE user_id=? AND token_hash != ?', (user['id'], auth.token_hash(request.cookies.get(COOKIE, ''))))
        return {'ok': True}

    def hq_url():
        from urllib.parse import urlsplit
        value = os.environ.get('CHAT_HQ_URL', '')
        try:
            parsed = urlsplit(value)
            return value if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password else None
        except ValueError:
            return None

    @app.get('/api/bootstrap')
    def bootstrap(user=Depends(current_user)):
        return {'me': {**chat.user_public(user), 'permissions': sorted(perms.effective(db, user))}, 'version': __version__, 'hq_url': hq_url(), 'agents': chat.visible_agents(user),
                'users': [u for u in chat.users() if not u['disabled']], 'conversations': chat.list_conversations(user),
                'tasks': chat.tasks(user)}

    # ------------------------------------------------------------ conversations
    @app.post('/api/conversations')
    async def new_conversation(request: Request, user=Depends(current_user)):
        data = await body(request)
        if data.get('kind') == 'dm':
            c = chat.create_dm(user, str(data.get('agent_id', '')), str(data.get('name', '')))
        elif data.get('kind') == 'channel':
            c = chat.create_channel(user, data.get('name'), data.get('topic', ''), list(data.get('agents') or []), list(data.get('users') or []),
                                    data.get('reply_mode', 'lead'), data.get('lead_agent'), bool(data.get('private')))
        else:
            raise Invalid('kind must be dm or channel')
        return chat.conv_public(c, user)

    @app.get('/api/conversations')
    def conversations(user=Depends(current_user)):
        return chat.list_conversations(user)

    @app.get('/api/conversations/{conv_id}')
    def conversation(conv_id: int, user=Depends(current_user)):
        return chat.conv_public(chat.check_conv(user, conv_id), user)

    @app.patch('/api/conversations/{conv_id}')
    async def update_conversation(conv_id: int, request: Request, user=Depends(current_user)):
        return chat.conv_public(chat.update_conv(user, conv_id, await body(request)), user)

    @app.delete('/api/conversations/{conv_id}')
    def delete_conversation(conv_id: int, user=Depends(current_user)):
        chat.delete_conv(user, conv_id)
        return {'ok': True}

    @app.post('/api/conversations/{conv_id}/members')
    async def add_members(conv_id: int, request: Request, user=Depends(current_user)):
        data = await body(request)
        return chat.conv_public(chat.add_members(user, conv_id, list(data.get('agents') or []), list(data.get('users') or [])), user)

    @app.delete('/api/conversations/{conv_id}/members/{mtype}/{mid}')
    def remove_member(conv_id: int, mtype: str, mid: str, user=Depends(current_user)):
        if mtype not in ('user', 'agent'):
            raise Invalid('unknown member type')
        return chat.conv_public(chat.remove_member(user, conv_id, mtype, mid), user)

    @app.get('/api/conversations/{conv_id}/messages')
    def messages(conv_id: int, before: int = 0, limit: int = 60, user=Depends(current_user)):
        return chat.messages(user, conv_id, before or None, limit)

    @app.get('/api/conversations/{conv_id}/export')
    def export_conversation(conv_id: int, format: str = 'md', user=Depends(current_user)):
        name, mime, text = chat.export(user, conv_id, format)
        return Response(text.encode('utf-8'), media_type=mime + '; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="%s"' % name, 'Cache-Control': 'no-store'})

    @app.get('/api/agents/{agent_id}/activity')
    def agent_activity(agent_id: str, user=Depends(current_user)):
        return chat.activity(user, agent_id)

    @app.post('/api/conversations/{conv_id}/messages')
    async def post_message(conv_id: int, request: Request, user=Depends(current_user)):
        data = await body(request)
        return chat.post(user, conv_id, data.get('text', ''), list(data.get('files') or []), spoken=bool(data.get('spoken')),
                         reply_to=data.get('reply_to'), reply_quote=data.get('reply_quote', ''))

    @app.post('/api/conversations/{conv_id}/read')
    async def read(conv_id: int, request: Request, user=Depends(current_user)):
        chat.check_conv(user, conv_id)
        chat.mark_read(user, conv_id, int((await body(request)).get('last_id', 0)))
        return {'ok': True}

    @app.patch('/api/messages/{message_id}')
    async def edit_message(message_id: int, request: Request, user=Depends(current_user)):
        return chat.edit_message(user, message_id, (await body(request)).get('text', ''))

    @app.delete('/api/messages/{message_id}')
    def delete_message(message_id: int, user=Depends(current_user)):
        chat.delete_message(user, message_id)
        return {'ok': True}

    @app.post('/api/messages/{message_id}/retry')
    def retry(message_id: int, user=Depends(current_user)):
        return {'deliveries': chat.retry(user, message_id)}

    @app.post('/api/deliveries/{delivery_id}/retry')
    def retry_delivery(delivery_id: int, user=Depends(current_user)):
        return {'delivery': chat.retry_delivery(user, delivery_id)}

    @app.post('/api/deliveries/{delivery_id}/cancel')
    def cancel(delivery_id: int, user=Depends(current_user)):
        chat.cancel(user, delivery_id)
        return {'ok': True}

    @app.get('/api/search')
    def search(q: str = '', user=Depends(current_user)):
        return chat.search(user, q)

    # ------------------------------------------------------------ files
    @app.post('/api/files')
    async def upload(file: UploadFile = File(...), user=Depends(current_user)):
        data = await file.read(25 * 1024 * 1024 + 1)
        return chat.save_file(user, file.filename, file.content_type, data)

    @app.get('/api/files/{file_id}')
    def download(file_id: str, user=Depends(current_user)):
        f = chat.user_file(user, file_id)
        inline = f['mime'].startswith('image/') and f['mime'] != 'image/svg+xml'
        return FileResponse(files_dir / f['id'], media_type=f['mime'] if inline else 'application/octet-stream',
                            filename=f['name'], content_disposition_type='inline' if inline else 'attachment')

    # ------------------------------------------------------------ tasks
    @app.get('/api/tasks')
    def tasks(user=Depends(current_user)):
        return chat.tasks(user)

    @app.post('/api/tasks')
    async def new_task(request: Request, user=Depends(current_user)):
        d = await body(request)
        return chat.create_task(user, d.get('title', ''), d.get('description', ''), d.get('assignee'), d.get('priority', 'normal'), d.get('due'))

    @app.patch('/api/tasks/{task_id}')
    async def update_task(task_id: int, request: Request, user=Depends(current_user)):
        return chat.update_task(user, task_id, await body(request))

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: int, user=Depends(current_user)):
        chat.delete_task(user, task_id)
        return {'ok': True}

    # ------------------------------------------------------------ live updates
    @app.get('/api/events')
    async def events(request: Request, user=Depends(current_user)):
        queue = hub.subscribe(user['id'])

        async def stream():
            try:
                yield 'retry: 3000\n\n'
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        text = await asyncio.wait_for(queue.get(), timeout=20)
                        yield 'data: %s\n\n' % text
                    except asyncio.TimeoutError:
                        yield ': ping\n\n'
            finally:
                hub.unsubscribe(queue)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # ------------------------------------------------------------ admin
    @app.get('/api/admin/users')
    def admin_users(_=Depends(admin_user)):
        role_map = perms.roles(db)
        return [chat.user_admin(u, role_map) for u in db.q('SELECT * FROM users ORDER BY name')]

    @app.post('/api/admin/users/{user_id}/new-password')
    def admin_user_new_password(user_id: int, _=Depends(admin_user)):
        if not db.one('SELECT 1 FROM users WHERE id=?', (user_id,)):
            raise NotFound('no such user')
        pw = perms.new_password()
        _set_password(user_id, pw)
        return {'password': pw}

    def _set_password(user_id, pw):
        db.run('UPDATE users SET pw_hash=?, pw_sealed=? WHERE id=?', (auth.hash_password(pw), None, user_id))
        db.run('DELETE FROM sessions WHERE user_id=?', (user_id,))
        db.run('DELETE FROM logins WHERE username=(SELECT username FROM users WHERE id=?)', (user_id,))

    @app.get('/api/admin/roles')
    def admin_roles(_=Depends(admin_user)):
        return {'roles': list(perms.roles(db).values()),
                'catalog': [{'id': p[0], 'group': p[1], 'label': p[2], 'description': p[3]} for p in perms.CATALOG]}

    @app.put('/api/admin/roles/{role_id}')
    async def admin_save_role(role_id: str, request: Request, _=Depends(admin_user)):
        d = await body(request)
        if role_id == 'admin':
            raise Invalid('the Admin role always has everything')
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,31}', role_id):
            raise Invalid('role ids are 2 to 32 lowercase letters, digits or dashes')
        existing = perms.roles(db).get(role_id) or {}
        label = str(d.get('label') or existing.get('label') or role_id).strip()[:40]
        try:
            plist = perms.clean(d['permissions']) if 'permissions' in d else existing.get('permissions', [])
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        db.run('INSERT INTO roles (id, label, description, permissions) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
               'label=excluded.label, description=excluded.description, permissions=excluded.permissions',
               (role_id, label, str(d.get('description', existing.get('description', '')))[:200], json.dumps(plist)))
        return perms.roles(db)[role_id]

    @app.delete('/api/admin/roles/{role_id}')
    def admin_delete_role(role_id: str, _=Depends(admin_user)):
        if role_id in perms.BUILTIN_ROLES:
            raise Invalid('built-in roles can be changed but not deleted')
        if db.one('SELECT 1 FROM users WHERE role=?', (role_id,)):
            raise Invalid('move the people with this role to another role first')
        db.run('DELETE FROM roles WHERE id=?', (role_id,))
        return {'ok': True}

    @app.post('/api/admin/users')
    async def admin_new_user(request: Request, _=Depends(admin_user)):
        d = await body(request)
        username = str(d.get('username', '')).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{1,30}', username):
            raise Invalid('usernames are 2 to 31 letters, digits, dots, dashes or underscores')
        pw = str(d.get('password') or '') or perms.new_password()
        if len(pw) < 10:
            raise Invalid('passwords need at least 10 characters')
        if db.one('SELECT 1 FROM users WHERE username=?', (username,)):
            raise Invalid('that username is taken')
        role = d.get('role') if d.get('role') in perms.roles(db) else 'staff'
        team = d.get('team') if d.get('team') in perms.TEAMS else ('Management' if role in ('admin', 'manager') else 'Staff')
        uid = db.run('INSERT INTO users (username, name, pw_hash, pw_sealed, role, team, created) VALUES (?,?,?,?,?,?,?)',
                     (username, str(d.get('name') or username)[:80], auth.hash_password(pw), None, role, team, now()))
        return {**chat.user_admin(db.one('SELECT * FROM users WHERE id=?', (uid,))), 'password': pw}

    @app.patch('/api/admin/users/{user_id}')
    async def admin_edit_user(user_id: int, request: Request, me=Depends(admin_user)):
        d = await body(request)
        u = db.one('SELECT * FROM users WHERE id=?', (user_id,))
        if not u:
            raise NotFound('no such user')
        if 'name' in d:
            db.run('UPDATE users SET name=? WHERE id=?', (str(d['name'])[:80], user_id))
        if 'role' in d:
            if d['role'] not in perms.roles(db):
                raise Invalid('unknown role')
            if user_id == me['id'] and d['role'] != 'admin':
                raise Invalid('you cannot remove your own admin role')
            db.run('UPDATE users SET role=? WHERE id=?', (d['role'], user_id))
        if 'team' in d:
            if d['team'] not in perms.TEAMS:
                raise Invalid('team is Management or Staff')
            db.run('UPDATE users SET team=? WHERE id=?', (d['team'], user_id))
        if 'overrides' in d:
            try:
                db.run('UPDATE users SET perm_overrides=? WHERE id=?', (json.dumps(perms.clean_overrides(d['overrides'])), user_id))
            except ValueError as exc:
                raise Invalid(str(exc)) from None
        if 'disabled' in d:
            if user_id == me['id']:
                raise Invalid('you cannot disable yourself')
            db.run('UPDATE users SET disabled=? WHERE id=?', (1 if d['disabled'] else 0, user_id))
            if d['disabled']:
                db.run('DELETE FROM sessions WHERE user_id=?', (user_id,))
        if d.get('password'):
            if len(str(d['password'])) < 10:
                raise Invalid('passwords need at least 10 characters')
            _set_password(user_id, str(d['password']))
        return chat.user_admin(db.one('SELECT * FROM users WHERE id=?', (user_id,)))

    @app.get('/api/admin/agents')
    def admin_agents(_=Depends(admin_user)):
        return [chat.agent_public(a, True) for a in db.q('SELECT * FROM agents ORDER BY grp, name')]

    @app.post('/api/admin/agents')
    async def admin_new_agent(request: Request, _=Depends(admin_user)):
        d = await body(request)
        agent_id = str(d.get('id', '')).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{1,40}', agent_id):
            raise Invalid('agent ids are like ct201-assistant')
        if db.one('SELECT 1 FROM agents WHERE id=?', (agent_id,)):
            raise Invalid('that agent id exists')
        db.run('INSERT INTO agents (id, handle, name, created) VALUES (?,?,?,?)', (agent_id, _free_handle(db, d.get('handle') or agent_id), agent_id, now()))
        return _edit_agent(agent_id, d)

    @app.patch('/api/admin/agents/{agent_id}')
    async def admin_edit_agent(agent_id: str, request: Request, _=Depends(admin_user)):
        chat.agent(agent_id)
        return _edit_agent(agent_id, await body(request))

    def _edit_agent(agent_id, d):
        sets, args = [], []
        for key, limit in (('name', 80), ('title', 200), ('department', 80), ('owner_name', 80), ('color', 20)):
            if key in d:
                sets.append('%s=?' % key); args.append(str(d[key] or '')[:limit])
        if 'handle' in d:
            handle = str(d['handle'] or '').strip().lower().lstrip('@')
            if not HANDLE.fullmatch(handle):
                raise Invalid('handles are letters, digits, dots, dashes or underscores')
            if db.one('SELECT 1 FROM agents WHERE handle=? AND id!=?', (handle, agent_id)):
                raise Invalid('@%s is taken' % handle)
            sets.append('handle=?'); args.append(handle)
        if 'kind' in d:
            if d['kind'] not in KINDS:
                raise Invalid('unknown kind')
            sets.append('kind=?'); args.append(d['kind'])
        if 'grp' in d:
            if d['grp'] not in GROUPS:
                raise Invalid('unknown group')
            sets.append('grp=?'); args.append(d['grp'])
        if 'visibility' in d:
            if d['visibility'] not in ('everyone', 'owner'):
                raise Invalid('visibility is everyone or owner')
            sets.append('visibility=?'); args.append(d['visibility'])
        if 'owner_user_id' in d:
            sets.append('owner_user_id=?'); args.append(int(d['owner_user_id']) if d['owner_user_id'] else None)
        if 'reports_to' in d:
            sets.append('reports_to=?'); args.append(d['reports_to'] or None)
        if 'disabled' in d:
            sets.append('disabled=?'); args.append(1 if d['disabled'] else 0)
        if 'config' in d and isinstance(d['config'], dict):
            cfg = loads(db.one('SELECT config FROM agents WHERE id=?', (agent_id,))['config'], {})
            for key in ('base_url', 'model', 'system', 'api_key'):
                if key in d['config']:
                    if d['config'][key]:
                        cfg[key] = str(d['config'][key])[:8000]
                    else:
                        cfg.pop(key, None)
            sets.append('config=?'); args.append(json.dumps(cfg))
        if sets:
            db.run('UPDATE agents SET %s WHERE id=?' % ', '.join(sets), (*args, agent_id))
        if 'owner_user_id' in d:
            chat.telegram_owner_changed(agent_id)
        a = chat.agent(agent_id)
        hub.publish('agent', chat.agent_public(a))
        return chat.agent_public(a, True)

    @app.post('/api/admin/agents/{agent_id}/token')
    def admin_agent_token(agent_id: str, _=Depends(admin_user)):
        chat.agent(agent_id)
        return {'token': auth.new_agent_token(db, agent_id)}

    @app.delete('/api/admin/agents/{agent_id}')
    def admin_delete_agent(agent_id: str, _=Depends(admin_user)):
        chat.agent(agent_id)
        db.run('DELETE FROM agents WHERE id=?', (agent_id,))
        hub.publish('agent_deleted', {'id': agent_id})
        return {'ok': True}

    @app.get('/api/admin/conversations')
    def admin_conversations(_=Depends(admin_user)):
        return [chat.conv_public(c) for c in db.q('SELECT * FROM conversations ORDER BY COALESCE(last_message_at, created) DESC LIMIT 500')]

    # ------------------------------------------------------------ agent bridge
    @app.post('/bridge/v1/hello')
    async def bridge_hello(request: Request, a=Depends(current_agent)):
        data = await body(request)
        chat.heartbeat(a, data.get('info'))
        return {'agent': {'id': a['id'], 'handle': a['handle'], 'name': a['name'], 'kind': a['kind']}, 'server': __version__}

    @app.get('/bridge/v1/poll')
    async def bridge_poll(wait: int = 25, a=Depends(current_agent)):
        """Long poll: returns as soon as there is work, or after `wait` seconds."""
        wait = max(0, min(50, wait))
        await asyncio.to_thread(chat.heartbeat, a)
        jobs = await asyncio.to_thread(chat.take, a)
        if not jobs and wait:
            event = hub.agent_waiter(a['id'])
            try:
                await asyncio.wait_for(event.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            finally:
                hub.drop_waiter(a['id'], event)
            jobs = await asyncio.to_thread(chat.take, a)
        return {'deliveries': jobs, 'cancelled': chat.cancelled_for(a)}

    @app.post('/bridge/v1/deliveries/{delivery_id}/working')
    def bridge_working(delivery_id: int, a=Depends(current_agent)):
        chat.working(a, delivery_id)
        return {'ok': True}

    @app.post('/bridge/v1/deliveries/{delivery_id}/reply')
    async def bridge_reply(delivery_id: int, request: Request, a=Depends(current_agent)):
        d = await body(request)
        m = chat.reply(a, delivery_id, d.get('text', ''), final=bool(d.get('final', True)), replace=not d.get('append'))
        return {'ok': True, 'message_id': m['id'] if m else None, 'cancelled': m is None}

    @app.post('/bridge/v1/deliveries/{delivery_id}/fail')
    async def bridge_fail(delivery_id: int, request: Request, a=Depends(current_agent)):
        chat.fail(a, delivery_id, (await body(request)).get('error', 'failed'))
        return {'ok': True}

    @app.post('/bridge/v1/send')
    async def bridge_send(request: Request, a=Depends(current_agent)):
        d = await body(request)
        m = chat.send(a, d.get('endpoint', ''), d.get('text', ''))
        return {'ok': True, 'message_id': m['id'] if m else None}

    @app.post('/bridge/v1/activity')
    async def bridge_activity(request: Request, a=Depends(current_agent)):
        d = await body(request)
        await asyncio.to_thread(chat.save_activity, a, d.get('steps'), d.get('updated'))
        return {'ok': True}

    @app.post('/bridge/v1/telegram')
    async def bridge_telegram(request: Request, a=Depends(current_agent)):
        d = await body(request)
        return {'ok': True, 'added': await asyncio.to_thread(chat.telegram_import, a, d.get('messages'))}

    @app.get('/bridge/v1/files/{file_id}')
    def bridge_file(file_id: str, a=Depends(current_agent)):
        f = chat.agent_file(a, file_id)
        return FileResponse(files_dir / f['id'], media_type='application/octet-stream', filename=f['name'])

    # ------------------------------------------------------------ voice ("Hey Siri, ask lead")
    def voice_key(request: Request):
        header = request.headers.get('authorization', '')
        key = voice.key_for_token(db, header[7:] if header.lower().startswith('bearer ') else '')
        if not key:
            raise HTTPException(401, 'bad voice key')
        return key

    def said(text):
        return PlainTextResponse(text, headers={'Cache-Control': 'no-store'})

    @app.post('/voice/v1/ask')
    async def voice_ask(request: Request, wait: int = voice.DEFAULT_WAIT, key=Depends(voice_key)):
        """Body: JSON {"text": "..."} or the words as plain text. Answers with plain text to read out."""
        raw = (await request.body()).decode('utf-8', 'replace')
        text = raw
        if 'json' in request.headers.get('content-type', ''):
            try:
                data = json.loads(raw or '{}')
            except ValueError:
                data = {}
            text = str(data.get('text', '')) if isinstance(data, dict) else ''
        try:
            return said(await speaker.ask(key, text, wait))
        except (Forbidden, Invalid) as exc:
            return said('Sorry, %s.' % exc)

    @app.post('/api/voice/conversation')
    async def voice_conversation(request: Request, user=Depends(current_user)):
        """Talk mode: the person's voice chat with an agent (shared with their Siri key)."""
        d = await body(request)
        chat.need(user, 'voice.use', 'use talk mode')
        if d.get('conversation'):
            # Talk inside an existing chat: any private chat, task or channel the person can post in.
            c = chat.check_conv(user, int(d['conversation']))
            if c['kind'] == 'telegram':
                raise Invalid('Telegram history is read-only')
            if c['archived']:
                raise Invalid('this conversation is archived')
            return chat.conv_public(c, user)
        agent = str(d.get('agent', ''))
        row = db.one('SELECT id FROM agents WHERE id=? OR handle=?', (agent, agent.lstrip('@')))
        if not row:
            raise NotFound('no such agent')
        return chat.conv_public(speaker.conversation_for(user, row['id']), user)

    @app.get('/voice/v1/latest')
    def voice_latest(key=Depends(voice_key)):
        try:
            return said(speaker.latest(key))
        except Forbidden as exc:
            return said('Sorry, %s.' % exc)

    @app.get('/api/admin/voice-keys')
    def admin_voice_keys(_=Depends(admin_user)):
        return db.q('SELECT v.id, v.user_id, u.username, v.agent_id, v.conv_id, v.label, v.created, v.last_used, v.disabled '
                    'FROM voice_keys v JOIN users u ON u.id = v.user_id ORDER BY v.id')

    @app.post('/api/admin/voice-keys')
    async def admin_new_voice_key(request: Request, _=Depends(admin_user)):
        d = await body(request)
        user = db.one('SELECT * FROM users WHERE id=? AND disabled=0', (int(d.get('user_id') or 0),))
        if not user:
            raise Invalid('pick a person')
        a = chat.agent(str(d.get('agent_id', '')))
        if not chat.can_use_agent(user, a):
            raise Invalid('%s cannot use %s' % (user['name'], a['name']))
        return {'token': voice.new_key(db, user['id'], a['id'], str(d.get('label', '')))}

    @app.delete('/api/admin/voice-keys/{key_id}')
    def admin_drop_voice_key(key_id: int, _=Depends(admin_user)):
        db.run('UPDATE voice_keys SET disabled=1 WHERE id=?', (key_id,))
        return {'ok': True}

    # ------------------------------------------------------------ the web page
    @app.get('/healthz')
    def health():
        return {'ok': True, 'version': __version__}

    @app.get('/{path:path}')
    def page(path: str):
        if path.startswith(('api/', 'bridge/', 'voice/')):
            raise HTTPException(404)
        target = (web_dir / path).resolve()
        if path and target.is_file() and web_dir.resolve() in target.parents:
            cache = 'public, max-age=31536000, immutable' if path.startswith('assets/') else 'no-cache'
            return FileResponse(target, headers={'Cache-Control': cache})
        index = web_dir / 'index.html'
        if not index.is_file():
            return JSONResponse({'error': 'the web app is not built (run npm run build in web/)'}, 503)
        return FileResponse(index, headers={'Cache-Control': 'no-cache'})

    return app


def _free_handle(db, wanted):
    base = re.sub(r'^ct\d+-', '', str(wanted).lower()).strip('-') or 'agent'
    base = re.sub(r'[^a-z0-9_.-]+', '-', base)[:28] or 'agent'
    handle, n = base, 2
    while db.one('SELECT 1 FROM agents WHERE handle=?', (handle,)):
        handle, n = '%s%d' % (base, n), n + 1
    return handle
