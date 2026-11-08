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
ZYLOS_DB = 'zylos/comm-bridge/c4.db'
ZYLOS_ROW_LIMIT = 20000


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


def zylos_metrics(home, after):
    """Message metadata only. The content column is never selected, so it cannot leave the host."""
    import sqlite3
    # No inside() check: Zylos links ~/zylos/comm-bridge to /opt/zylos/comm-bridge. The path is
    # fixed here rather than requested, and only these metadata columns are ever returned.
    path = home / ZYLOS_DB
    if not path.is_file():
        return {'found': False, 'rows': [], 'more': False, 'max_id': 0}
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        rows = conn.execute('SELECT id, direction, channel, status, timestamp FROM conversations '
                            'WHERE id > ? ORDER BY id LIMIT ?', (after, ZYLOS_ROW_LIMIT + 1)).fetchall()
        max_id = conn.execute('SELECT max(id) FROM conversations').fetchone()[0] or 0
    finally:
        conn.close()
    return {'found': True, 'more': len(rows) > ZYLOS_ROW_LIMIT, 'max_id': max_id,
            'rows': [[row_id, direction, channel, status == 'failed', timestamp]
                     for row_id, direction, channel, status, timestamp in rows[:ZYLOS_ROW_LIMIT]]}


LIMITS_FILE = '.claude/hq-limits.json'


def claude_limits(home):
    """Plan limit use, as Claude Code last gave it to the agent's status line. No login is read."""
    path = home / LIMITS_FILE
    if not path.is_file() or path.stat().st_size > 20000:
        return {'found': False}
    data = json.loads(path.read_text(encoding='utf-8'))
    windows = {}
    for name, value in (data.get('rate_limits') or {}).items():
        if isinstance(name, str) and len(name) <= 40 and isinstance(value, dict):
            pct, reset = value.get('used_percentage'), value.get('resets_at')
            if isinstance(pct, (int, float)) and (reset is None or isinstance(reset, (int, float))):
                windows[name] = [float(pct), reset]
    captured = data.get('captured_at')
    return {'found': True, 'captured_at': captured if isinstance(captured, (int, float)) else path.stat().st_mtime, 'windows': windows}


def scan(request):
    home = Path.home()
    result = {'files': [], 'more': False, 'errors': []}
    if 'claude_limits' in request.get('sources', []):
        try:
            result['claude_limits'] = claude_limits(home)
        except Exception as exc:  # A half-written file must not stop the scan.
            result['errors'].append('claude_limits: ' + type(exc).__name__ + ': ' + str(exc)[:200])
    if 'zylos_metrics' in request.get('sources', []):
        try:
            result['zylos_metrics'] = zylos_metrics(home, int(request.get('zylos_after', 0)))
            if not result['zylos_metrics']['found']:
                result['errors'].append(ZYLOS_DB + ': not found')
        except Exception as exc:  # A locked or unexpected database must not stop the file scan.
            result['errors'].append('zylos_metrics: ' + type(exc).__name__ + ': ' + str(exc)[:200])
    remaining = min(max(0, int(request.get('cap_bytes', 20000000))), 20000000)
    db_cap = min(max(0, int(request.get('db_cap_bytes', 30000000))), 30000000)
    db_sent = False
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
                        if stat.st_size > db_cap:
                            if stat.st_size > 30000000:
                                result['errors'].append(rel + ': database larger than 30 MB, not mirrored')
                            continue
                        if stat.st_size == old_size and stat.st_mtime == old_mtime:
                            continue
                        if db_sent:
                            result['more'] = True
                            continue
                        kind, offset = 'replace', 0
                    else:
                        reset = stat.st_size < old_size or (stat.st_size == old_size and stat.st_mtime < old_mtime)
                        kind, offset = ('reset', 0) if reset else ('append', old_size)
                        if stat.st_size == offset and not reset:
                            continue
                    if not is_db and remaining == 0 and stat.st_size > offset:
                        result['more'] = True
                        continue
                    with path.open('rb') as stream:
                        stream.seek(offset)
                        data = stream.read(stat.st_size if is_db else remaining)
                    result['files'].append({'path': rel, 'kind': kind, 'offset': offset,
                                            'data_b64': base64.b64encode(data).decode('ascii'),
                                            'size': stat.st_size, 'mtime': stat.st_mtime})
                    if is_db:
                        db_sent = True
                    else:
                        remaining -= len(data)
                    if offset + len(data) < stat.st_size:
                        result['more'] = True
                        continue
                except (OSError, ValueError, TypeError) as exc:
                    result['errors'].append(str(exc))
    return result


def main(request_text: str) -> None:
    print(json.dumps(scan(json.loads(request_text))))


if __name__ == '__main__' and '_HQ_REQUEST' not in globals() and sys.argv[0] != '-':
    main(sys.stdin.read())
