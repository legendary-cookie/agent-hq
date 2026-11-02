"""Optional llm-usage-metrics importer; the executable is never installed automatically."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec

_EXCLUDED = frozenset({'claude', 'codex', 'opencode', 'gemini', 'antigravity', 'kimi', 'dsh'})
_ENV_SECRET = re.compile('KEY|TOKEN|SECRET|PASSWORD', re.I)
_CURSOR = 'events'
_KEY_FIELDS = ('source', 'sessionId', 'timestamp', 'model', 'inputTokens', 'outputTokens',
               'reasoningTokens', 'cacheReadTokens', 'cacheWriteTokens')


def event_key(row: dict) -> str:
    """Stable id for an imported event: the same usage re-imported (or re-formatted
    by the tool) keeps its id; events differing in any token count stay apart."""
    return hashlib.sha1('|'.join(str(row.get(k, '')) for k in _KEY_FIELDS).encode('utf-8')).hexdigest()


_RUN_TIMEOUT = 60
_INSTALL_TIMEOUT = 300  # a first npm install downloads the package and its dependencies


def _run(args, timeout=_RUN_TIMEOUT):
    env = {key: value for key, value in os.environ.items() if not _ENV_SECRET.search(key)}
    try:
        result = subprocess.run(args, shell=False, timeout=timeout, env=env, capture_output=True, text=True, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Importer command unavailable or timed out: {exc}') from exc
    if result.returncode:
        raise RuntimeError(f'Importer command failed (exit {result.returncode})')
    return result.stdout


def install(tools_dir):
    tools_dir = Path(tools_dir)
    if is_forbidden(tools_dir):
        raise ValueError('Forbidden importer tools path')
    # On Windows npm is npm.cmd; shell=False needs its full path to launch it.
    npm = shutil.which('npm')
    if not npm:
        raise RuntimeError('npm was not found on PATH; install Node.js 20+ to use the long-tail importer')
    npm_dir = Path(npm).resolve().parent
    path_dirs = {Path(entry).resolve() for entry in os.environ.get('PATH', '').split(os.pathsep) if entry}
    if npm_dir == Path.cwd().resolve() or npm_dir not in path_dirs:
        raise RuntimeError('npm must be in a PATH directory other than the current directory')
    _run([npm, 'install', '--ignore-scripts', '--prefix', str(tools_dir), 'llm-usage-metrics@0.12.1'], timeout=_INSTALL_TIMEOUT)


def _timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class ImporterAdapter:
    name = 'importer'
    tested = 'upstream'

    def __init__(self, tools_dir: Path):
        self.tools_dir = Path(tools_dir)
        self._last_doctor = 0
        self._last_import = 0
        self._sources = None

    def poll(self, store, cfg):
        now = time.time()
        executable = self.tools_dir / 'node_modules' / '.bin' / ('llm-usage.cmd' if os.name == 'nt' else 'llm-usage')
        if is_forbidden(executable):
            raise ValueError('Forbidden importer executable path')
        if not executable.is_file():
            raise RuntimeError('llm-usage executable is missing')
        if self._sources is None or now - self._last_doctor >= 1800:
            try:
                report = json.loads(_run([str(executable), 'doctor', '--json']))
                if report['schemaVersion'] != 1 or report['report'] != 'doctor':
                    raise ValueError('unexpected doctor report')
                rows = report['data']['sources']
                self._sources = sorted({row['id'] for row in rows if row.get('state') == 'found' and row.get('status') == 'ok' and isinstance(row.get('id'), str) and row['id'] not in _EXCLUDED})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise RuntimeError('Invalid importer doctor output') from exc
            self._last_doctor = now
        if not self._sources or now - self._last_import < 300:
            return Batch([], [], [], [])
        cursor = store.get_cursor(self.name, _CURSOR)
        since = datetime.fromtimestamp(max(0, cursor[1] - 86400) if cursor else 0, timezone.utc).isoformat().replace('+00:00', 'Z')
        output = _run([str(executable), 'events', '--source', ','.join(self._sources), '--since', since])
        sessions, events, warnings = [], [], 0
        latest = cursor[1] if cursor else 0
        for line in output.splitlines():
            try:
                row = json.loads(line)
                source = row['source']
                if source not in self._sources:
                    continue
                sid = f"{source}:{row['sessionId']}"
                ts = _timestamp(row['timestamp'])
                model = row.get('model')
                sessions.append(SessionRec(sid, source, row.get('provider'), model, row.get('repoRoot'), None, ts, ts, None, None, 'llm-usage-metrics', True))
                events.append(EventRec(sid, event_key(row), ts, 'assistant', None, None, row['inputTokens'], row['outputTokens'] + row['reasoningTokens'], row['cacheReadTokens'], row['cacheWriteTokens'], False, model))
                latest = max(latest, ts)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                warnings += 1
        self._last_import = now
        cursors = [(self.name, _CURSOR, 0, latest)] if latest else []
        return Batch(sessions, events, [], [], warnings, cursors)
