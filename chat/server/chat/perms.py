"""Chat role permissions, per-user overrides and one-time password generation."""
import json
import secrets

CATALOG = [
    ('agents.management', 'Agents', 'Management agents', 'See and use management agents.'),
    ('agents.department', 'Agents', 'Department agents', 'See and use the department agents (Sales, Finance, IT…).'),
    ('agents.all_staff', 'Agents', 'Every staff agent', 'Use every staff agent, not only their own.'),
    ('channels.create', 'Chat', 'Start channels', 'Make new channels with several agents.'),
    ('tasks.create', 'Chat', 'Give tasks', 'Create tasks on the task board.'),
    ('files.upload', 'Chat', 'Attach files', 'Send files and images in chats.'),
    ('voice.use', 'Chat', 'Talk mode and voice', 'Talk to agents out loud (talk mode, Siri voice keys).'),
    ('telegram.view', 'Chat', 'Telegram history', 'See the Telegram history of their own agents.'),
    ('chats.export', 'Chat', 'Download chats', 'Download a chat, channel or Telegram history as a file.'),
    ('activity.view', 'Agents', 'Live activity of any agent', 'See what any agent they can use is working on, including its thinking. Everyone can watch their own agents.'),
]
PERMISSIONS = [p[0] for p in CATALOG]
BUILTIN_ROLES = {
    'admin': {'label': 'Admin', 'description': 'Everything, including the Admin page.', 'permissions': PERMISSIONS},
    'manager': {'label': 'Manager', 'description': 'Everything in the chat, every agent.', 'permissions': PERMISSIONS},
    'staff': {'label': 'Staff', 'description': 'The chat with their own agents, management and department agents.',
              'permissions': [p for p in PERMISSIONS if p not in ('agents.all_staff', 'activity.view')]},
}
TEAMS = ('Management', 'Staff', '')


def clean(perms):
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise ValueError('permissions must be a list')
    unknown = [p for p in perms if p not in PERMISSIONS]
    if unknown:
        raise ValueError('unknown permission: %s' % ', '.join(unknown))
    return [p for p in PERMISSIONS if p in perms]


def clean_overrides(d):
    if not isinstance(d, dict):
        raise ValueError('overrides must be an object')
    out = {}
    for k, v in d.items():
        if k not in PERMISSIONS:
            raise ValueError('unknown permission: %s' % k)
        if v is True or v is False:
            out[k] = v
    return out


def roles(db):
    """Built-in roles (with saved edits) plus custom ones. Admin always has every permission."""
    saved = {r['id']: r for r in db.q('SELECT * FROM roles')}
    out = {}
    for rid, base in BUILTIN_ROLES.items():
        s = saved.get(rid)
        perms = list(PERMISSIONS) if rid == 'admin' else ([p for p in json.loads(s['permissions']) if p in PERMISSIONS] if s else base['permissions'])
        out[rid] = {'id': rid, 'label': s['label'] if s else base['label'], 'description': s['description'] if s else base['description'],
                    'permissions': perms, 'builtin': True, 'locked': rid == 'admin'}
    for rid, s in saved.items():
        if rid not in BUILTIN_ROLES:
            out[rid] = {'id': rid, 'label': s['label'], 'description': s['description'],
                        'permissions': [p for p in json.loads(s['permissions']) if p in PERMISSIONS], 'builtin': False, 'locked': False}
    return out


def effective(db, user, role_map=None):
    if user['role'] == 'admin':
        return set(PERMISSIONS)
    role = (role_map or roles(db)).get(user['role'])
    perms = set(role['permissions']) if role else set()
    try:
        over = json.loads(user.get('perm_overrides') or '{}')
    except ValueError:
        over = {}
    for k, v in over.items():
        if k in PERMISSIONS:
            (perms.add if v else perms.discard)(k)
    return perms


def new_password():
    """Easy to read out and type: 3 groups of 4 letters/digits without look-alikes, e.g. 'k7mq-x3tp-9wza'."""
    alphabet = 'abcdefghjkmnpqrstuvwxyz23456789'
    return '-'.join(''.join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
