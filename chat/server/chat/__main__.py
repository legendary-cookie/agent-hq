"""Command line: run the server and manage people and agents.

  python -m chat serve [--host 127.0.0.1] [--port 8700]
  python -m chat add-user <username> [--name "Full Name"] [--admin]     (asks for the password)
  python -m chat set-password <username>
  python -m chat import-fleet <fleet.json>                              (agents from Agent HQ's fleet.json)
  python -m chat agent-token <agent-id>                                 (prints a new bridge token once)
  python -m chat add-demo                                               (a demo agent that answers by itself)
  python -m chat agent-for-ct <ctid>                                    (prints "<agent-id> <kind>")
  python -m chat bridge-config <agent-id>                               (new token, printed as the bridge's config JSON)
  python -m chat list-agents
  python -m chat voice-key <username> <agent-id or handle> [--label iPhone]   (prints a new "Hey Siri" key once)
  python -m chat voice-keys                                             (lists them; voice-key --revoke <id> turns one off)
All take --data <dir> (default $CHAT_DATA or ./data).
"""
import argparse
import getpass
import json
import os
import re
import sys

from . import auth
from .db import Db, now

COLORS = ['#c2410c', '#0f766e', '#7c3aed', '#be123c', '#1d4ed8', '#4d7c0f', '#a16207', '#0e7490', '#9333ea', '#b45309']
HANDLES = {}
TITLES = {'Management': 'Management agent', 'Staff': 'Staff agent', 'Department': 'Department agent', 'Specialist': 'Specialist agent'}


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m chat')
    p.add_argument('--data', default=os.environ.get('CHAT_DATA', './data'))
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve'); s.add_argument('--host', default='127.0.0.1'); s.add_argument('--port', type=int, default=8700)
    s = sub.add_parser('add-user'); s.add_argument('username'); s.add_argument('--name'); s.add_argument('--admin', action='store_true')
    s.add_argument('--password-stdin', action='store_true')
    s = sub.add_parser('set-password'); s.add_argument('username'); s.add_argument('--password-stdin', action='store_true')
    s = sub.add_parser('import-fleet'); s.add_argument('path')
    s = sub.add_parser('agent-token'); s.add_argument('agent_id')
    sub.add_parser('add-demo')
    s = sub.add_parser('agent-for-ct'); s.add_argument('ct')
    s = sub.add_parser('bridge-config'); s.add_argument('agent_id')
    s.add_argument('--server', default=os.environ.get('CHAT_PUBLIC_URL', ''))
    s.add_argument('--ca-file', default='/etc/agent-hq-chat-ca.crt')
    s.add_argument('--zylos-dir', default='')
    sub.add_parser('list-agents')
    s = sub.add_parser('voice-key'); s.add_argument('username', nargs='?'); s.add_argument('agent_id', nargs='?')
    s.add_argument('--label', default=''); s.add_argument('--revoke', type=int)
    sub.add_parser('voice-keys')
    args = p.parse_args(argv)

    if args.cmd == 'serve':
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(args.data), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips='127.0.0.1', log_level='warning')
        return
    db = Db(os.path.join(args.data, 'chat.db'))
    if args.cmd == 'add-user':
        if db.one('SELECT 1 FROM users WHERE username=?', (args.username,)):
            sys.exit('that username exists; use set-password')
        pw = _password(args.password_stdin)
        from . import perms
        db.run('INSERT INTO users (username, name, pw_hash, pw_sealed, role, team, created) VALUES (?,?,?,?,?,?,?)',
               (args.username.lower(), args.name or args.username, auth.hash_password(pw), None,
                'admin' if args.admin else 'staff', 'Management' if args.admin else 'Staff', now()))
        print('added', args.username, '(admin)' if args.admin else '')
    elif args.cmd == 'set-password':
        if not db.one('SELECT 1 FROM users WHERE username=?', (args.username,)):
            sys.exit('no such user')
        from . import perms
        pw = _password(args.password_stdin)
        db.run('UPDATE users SET pw_hash=?, pw_sealed=?, disabled=0 WHERE username=?', (auth.hash_password(pw), None, args.username))
        db.run('DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)', (args.username,))
        db.run('DELETE FROM logins WHERE username=?', (args.username,))
        print('password set; that person is signed out everywhere')
    elif args.cmd == 'import-fleet':
        print(import_fleet(db, json.load(open(args.path, encoding='utf-8'))))
    elif args.cmd == 'agent-token':
        if not db.one('SELECT 1 FROM agents WHERE id=?', (args.agent_id,)):
            sys.exit('no such agent')
        print(auth.new_agent_token(db, args.agent_id))
    elif args.cmd == 'agent-for-ct':
        row = db.one("SELECT id, kind FROM agents WHERE id LIKE ? AND disabled=0", ('ct%s-%%' % int(args.ct),))
        if not row:
            sys.exit('no agent for CT %s' % args.ct)
        print(row['id'], row['kind'])
    elif args.cmd == 'bridge-config':
        row = db.one('SELECT id, kind FROM agents WHERE id=?', (args.agent_id,))
        if not row or row['kind'] not in ('zylos', 'hermes'):
            sys.exit('no zylos or hermes agent called %s' % args.agent_id)
        if not args.server:
            sys.exit('set CHAT_PUBLIC_URL or pass --server')
        cfg = {'server': args.server, 'token': auth.new_agent_token(db, row['id']), 'kind': row['kind'], 'ca_file': args.ca_file}
        if args.zylos_dir:
            cfg['zylos'] = {'dir': args.zylos_dir}
        print(json.dumps(cfg))
    elif args.cmd == 'list-agents':
        for a in db.q('SELECT id, handle, kind, grp, last_seen FROM agents ORDER BY id'):
            seen = 'online' if a['last_seen'] and now() - a['last_seen'] < 90 else ('never' if not a['last_seen'] else 'offline')
            print('%-28s @%-18s %-7s %-11s %s' % (a['id'], a['handle'], a['kind'], a['grp'], seen))
    elif args.cmd == 'voice-key':
        from . import voice
        if args.revoke:
            db.run('UPDATE voice_keys SET disabled=1 WHERE id=?', (args.revoke,))
            print('voice key %d turned off' % args.revoke)
            return
        user = db.one('SELECT * FROM users WHERE username=? AND disabled=0', (args.username or '',))
        agent = db.one('SELECT * FROM agents WHERE (id=? OR handle=?) AND disabled=0', (args.agent_id or '', args.agent_id or ''))
        if not user or not agent:
            sys.exit('usage: voice-key <username> <agent-id or handle> (both must exist)')
        if agent['visibility'] == 'owner' and agent['owner_user_id'] != user['id'] and user['role'] != 'admin':
            sys.exit('%s cannot use %s' % (user['username'], agent['name']))
        print(voice.new_key(db, user['id'], agent['id'], args.label))
    elif args.cmd == 'voice-keys':
        for v in db.q('SELECT v.*, u.username FROM voice_keys v JOIN users u ON u.id=v.user_id ORDER BY v.id'):
            print('%-4d %-14s %-24s %-12s chat %-6s %s' % (v['id'], v['username'], v['agent_id'], v['label'] or '-',
                  v['conv_id'] or '-', 'off' if v['disabled'] else 'on'))
    elif args.cmd == 'add-demo':
        if not db.one("SELECT 1 FROM agents WHERE id='demo-helper'"):
            db.run("INSERT INTO agents (id, handle, name, kind, grp, title, visibility, color, created) VALUES "
                   "('demo-helper','helper','Demo Helper','demo','Other','Answers by itself, for trying the app','everyone','#64748b',?)", (now(),))
        print('demo agent: @helper')


def import_fleet(db, fleet):
    """Add or refresh agents from fleet.json. Existing handles, owners and settings are kept."""
    added = updated = 0
    for i, (agent_id, info) in enumerate(sorted((fleet.get('agents') or {}).items())):
        group = info.get('group') or 'Other'
        kind = 'hermes' if group == 'Department' else 'zylos'
        name = info.get('label') or agent_id
        if db.one('SELECT 1 FROM agents WHERE id=?', (agent_id,)):
            db.run('UPDATE agents SET name=?, grp=?, owner_name=CASE WHEN owner_name="" THEN ? ELSE owner_name END WHERE id=?',
                   (name, group, info.get('owner') or '', agent_id))
            updated += 1
            continue
        handle = info.get('handle') or HANDLES.get(agent_id) or re.sub(r'[^a-z0-9]+', '', name.lower()) or agent_id
        base, n = handle, 2
        while db.one('SELECT 1 FROM agents WHERE handle=?', (handle,)):
            handle, n = '%s%d' % (base, n), n + 1
        title = TITLES.get(group, 'Agent') + (' · ' + name if group == 'Department' else '')
        db.run('INSERT INTO agents (id, handle, name, kind, grp, title, department, owner_name, visibility, color, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (agent_id, handle, name, kind, group, title, info.get('department') or (name if group == 'Department' else ''),
                info.get('owner') or '', 'owner' if group == 'Staff' else 'everyone', COLORS[i % len(COLORS)], now()))
        added += 1
    return 'agents added: %d, refreshed: %d' % (added, updated)


def _password(from_stdin):
    if from_stdin:
        pw = sys.stdin.readline().rstrip('\n')
    else:
        pw = getpass.getpass('Password (at least 10 characters): ')
        if pw != getpass.getpass('Again: '):
            sys.exit('the two did not match')
    if len(pw) < 10:
        sys.exit('use at least 10 characters')
    return pw


if __name__ == '__main__':
    main()
