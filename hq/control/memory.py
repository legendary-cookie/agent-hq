"""Browse and edit an agent's memory files and skills, with history.

The script runs inside the container as the agent's own user (never root), so it can only touch what the
agent itself can. Memory lives in the Zylos memory folder, which is a git repository: every save is a
commit, and history is that file's git log. Skills live in ~/.claude/skills; they have no history of their
own, so each save first keeps the old text under ~/.claude/hq-history/skills/.
"""
import json
import re

from .pve import ControlError

MAX_TEXT = 400_000
PATH = re.compile(r'[A-Za-z0-9_.\- /]{1,200}')
TEXT_ENDINGS = ('.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.csv', '.sh', '.py', '.js', '.ts')

SCRIPT = r'''
import json, os, shutil, subprocess, sys, time
import pwd
req = json.load(sys.stdin)
home = pwd.getpwuid(os.getuid()).pw_dir
ENDINGS = tuple(req['endings'])
def fail(text):
    print(json.dumps({'error': text}))
    sys.exit(0)
if req['root'] == 'memory':
    root = next((p for p in (home + '/zylos/memory', '/opt/zylos/memory') if os.path.isdir(p)), None)
else:
    root = home + '/.claude/skills'
    if not os.path.isdir(root):
        root = None
if not root:
    fail('this agent has no ' + ('Zylos memory folder' if req['root'] == 'memory' else 'skills folder'))
root = os.path.realpath(root)
history_root = home + '/.claude/hq-history/skills'
git = os.path.isdir(root + '/.git') and shutil.which('git') is not None
def target(rel):
    parts = rel.split('/') if rel else []
    if not rel or rel.startswith('/') or any(p in ('', '..') or p.startswith('.') for p in parts) or not rel.endswith(ENDINGS):
        fail('that path isn’t allowed')
    path = os.path.join(root, rel)
    real = os.path.realpath(path)
    if not real.startswith(root + '/') or os.path.islink(path):
        fail('that path is a link or outside the folder')
    return path
def run(*args):
    return subprocess.run(args, capture_output=True, text=True, cwd=root, timeout=30)
op = req['op']
if op == 'list':
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != 'node_modules')
        for name in sorted(names):
            path = os.path.join(base, name)
            if os.path.islink(path) or not name.endswith(ENDINGS):
                continue
            st = os.stat(path)
            files.append({'path': os.path.relpath(path, root), 'size': st.st_size, 'modified': st.st_mtime})
            if len(files) >= 3000:
                break
        if len(files) >= 3000:
            break
    print(json.dumps({'root': root, 'git': git, 'files': files, 'truncated': len(files) >= 3000}))
elif op == 'read':
    path = target(req['path'])
    if not os.path.isfile(path):
        fail('that file doesn’t exist')
    if os.path.getsize(path) > req['max']:
        fail('that file is too big to edit here')
    text = open(path, encoding='utf-8', errors='replace').read()
    history = []
    if req['root'] == 'memory' and git:
        out = run('git', 'log', '-n', '30', '--format=%H%x09%at%x09%s', '--', req['path']).stdout
        for line in out.splitlines():
            parts = line.split('\t', 2)
            if len(parts) == 3:
                history.append({'id': parts[0], 'at': int(parts[1]), 'label': parts[2]})
    elif req['root'] == 'skills':
        folder = os.path.join(history_root, req['path'])
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder), reverse=True)[:30]:
                history.append({'id': name, 'at': os.stat(os.path.join(folder, name)).st_mtime, 'label': 'Before an edit in HQ'})
    print(json.dumps({'text': text, 'history': history, 'modified': os.stat(path).st_mtime}))
elif op == 'version':
    target(req['path'])
    vid = req['id']
    if req['root'] == 'memory':
        if not git or not all(c in '0123456789abcdef' for c in vid) or len(vid) != 40:
            fail('unknown version')
        done = run('git', 'show', vid + ':' + req['path'])
        if done.returncode:
            fail('that version has no copy of this file')
        print(json.dumps({'text': done.stdout}))
    else:
        if '/' in vid or vid.startswith('.'):
            fail('unknown version')
        path = os.path.join(history_root, req['path'], vid)
        if not os.path.isfile(path):
            fail('unknown version')
        print(json.dumps({'text': open(path, encoding='utf-8', errors='replace').read()}))
elif op == 'write':
    path = target(req['path'])
    if not req['path'].endswith(ENDINGS):
        fail('only text files can be edited here')
    if req.get('create') and os.path.lexists(path):
        fail('a file with that name already exists, so nothing was saved')
    if req.get('expect') is not None and os.path.exists(path) and abs(os.stat(path).st_mtime - req['expect']) > 0.001:
        fail('the file changed since you opened it, so nothing was saved. Open it again to see the new text')
    existed = os.path.exists(path)
    if req['root'] == 'skills' and existed:
        folder = os.path.join(history_root, req['path'])
        os.makedirs(folder, exist_ok=True)
        now = time.time()
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now)) + '-%06d' % int(now % 1 * 1e6)
        with open(os.path.join(folder, stamp), 'w', encoding='utf-8') as out:
            out.write(open(path, encoding='utf-8', errors='replace').read())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.hq-tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write(req['text'])
    if existed:
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)
    committed = False
    if req['root'] == 'memory' and git:
        run('git', 'add', '--', req['path'])
        done = run('git', '-c', 'user.email=hq@agent-hq.local', '-c', 'user.name=HQ', 'commit', '-q', '-m', ('Edited in HQ: ' if existed else 'Added in HQ: ') + req['path'], '--', req['path'])
        committed = done.returncode == 0
    print(json.dumps({'saved': True, 'created': not existed, 'committed': committed, 'modified': os.stat(path).st_mtime}))
'''


class Memory:
    def __init__(self, pve, log):
        self.pve, self.log = pve, log

    def _call(self, host, request, timeout=60):
        if request.get('root') not in ('memory', 'skills'):
            raise ValueError('choose memory or skills')
        path = request.get('path')
        if path is not None and (not isinstance(path, str) or not PATH.fullmatch(path) or path.startswith('/')
                                 or any(part in ('', '..') or part.startswith('.') for part in path.split('/')) or not path.endswith(TEXT_ENDINGS)):
            raise ValueError('that path isn’t allowed')
        out = self.pve.python_as_agent(host, SCRIPT, {**request, 'endings': list(TEXT_ENDINGS), 'max': MAX_TEXT}, timeout=timeout)
        try:
            answer = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ControlError('the agent gave an answer HQ couldn’t read') from None
        if answer.get('error'):
            raise ControlError(answer['error'])
        return answer

    def files(self, host, root):
        return self._call(host, {'op': 'list', 'root': root})

    def read(self, host, root, path):
        return self._call(host, {'op': 'read', 'root': root, 'path': path})

    def version(self, host, root, path, version_id):
        if not isinstance(version_id, str) or not re.fullmatch(r'[0-9a-f]{40}|\d{8}-\d{6}(-\d{6})?', version_id):
            raise ValueError('unknown version')
        return self._call(host, {'op': 'version', 'root': root, 'path': path, 'id': version_id})

    def write(self, host, root, path, text, expect=None, create=False):
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise ValueError('the text must be under 400,000 characters')
        if not isinstance(path, str) or not path.endswith(TEXT_ENDINGS):
            raise ValueError('only text files can be edited here')
        if expect is not None and not isinstance(expect, (int, float)):
            raise ValueError('bad file time')
        answer = self._call(host, {'op': 'write', 'root': root, 'path': path, 'text': text, 'expect': expect, 'create': bool(create)}, timeout=90)
        self.log.log({'action': 'memory_edited' if root == 'memory' else 'skill_edited', 'host': host, 'path': path, 'chars': len(text),
                      'created': answer.get('created', False)})
        return answer
