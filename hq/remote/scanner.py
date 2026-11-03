"""Read-only scanner; kept compatible with Python 3.8."""
import base64
import json
import os
import sys
from pathlib import Path

SOURCES = {
    'claude_code': ('.claude/projects',),
    'codex': ('.codex/sessions',),
    'opencode': ('.local/share/opencode/opencode.db',),
    'antigravity': ('.gemini/antigravity-cli/conversations', '.gemini/antigravity-cli/history.jsonl'),
    'gemini_cli': ('.gemini/tmp',),
    'kimi': ('.kimi-code/sessions', '.kimi/sessions'),
    'dsh': ('.dsh/sessions',),
}
FORBIDDEN = {'auth.json', '.credentials.json', 'credentials', 'secrets', '.env', 'oauth_creds.json', 'google_accounts.json'}


def forbidden(name):
    return name.split(':', 1)[0].rstrip(' .').lower() in FORBIDDEN


def inside(path, root):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.abspath(root))) == os.path.abspath(root)
    except ValueError:
        return False


def candidates(root):
    if root.is_file():
        yield root
    elif root.is_dir():
        for folder, dirs, files in os.walk(str(root), followlinks=False):
            dirs[:] = sorted(d for d in dirs if not forbidden(d) and inside(Path(folder) / d, root))
            for name in sorted(files):
                path = Path(folder) / name
                if not forbidden(name) and inside(path, root):
                    yield path


def scan(request):
    home = Path.home()
    result = {'files': [], 'more': False, 'errors': []}
    remaining = min(max(0, int(request.get('cap_bytes', 20000000))), 20000000)
    db_cap = min(max(0, int(request.get('db_cap_bytes', 200000000))), 200000000)
    cursors = request.get('cursors', {})
    for source in request.get('sources', []):
        for relroot in SOURCES.get(source, ()):
            root = home / relroot
            if any(forbidden(part) for part in Path(relroot).parts) or not inside(root, home):
                continue
            for path in candidates(root):
                try:
                    stat = path.stat()
                    rel = path.relative_to(home).as_posix()
                    old_size, old_mtime = cursors.get(rel, (0, 0))
                    is_db = path.suffix.lower() == '.db'
                    if is_db:
                        if stat.st_size > db_cap or (stat.st_size == old_size and stat.st_mtime == old_mtime):
                            continue
                        if stat.st_size > remaining:
                            result['more'] = True
                            continue
                        kind, offset = 'replace', 0
                    else:
                        reset = stat.st_size < old_size or (stat.st_size == old_size and stat.st_mtime < old_mtime)
                        kind, offset = ('reset', 0) if reset else ('append', old_size)
                        if stat.st_size == offset and not reset:
                            continue
                    if remaining == 0 and stat.st_size > offset:
                        result['more'] = True
                        return result
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        data = stream.read(remaining)
                    result['files'].append({'path': rel, 'kind': kind, 'offset': offset,
                                            'data_b64': base64.b64encode(data).decode('ascii'),
                                            'size': stat.st_size, 'mtime': stat.st_mtime})
                    remaining -= len(data)
                    if offset + len(data) < stat.st_size:
                        result['more'] = True
                        return result
                except (OSError, ValueError, TypeError) as exc:
                    result['errors'].append(str(exc))
    return result


if __name__ == '__main__':
    request_text = sys.stdin.read()
    if '__HQ_REQUEST__\n' in request_text:
        request_text = request_text.split('__HQ_REQUEST__\n', 1)[1]
    print(json.dumps(scan(json.loads(request_text))))
