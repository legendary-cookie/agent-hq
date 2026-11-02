"""Read plain and independently framed zstd DeepSeek Harness session logs."""
import json
import os
from pathlib import Path
from datetime import datetime

from .base import iter_files
from ..config import SourceCfg, is_forbidden
from ..providers import normalize_provider
from ..records import Batch, EventRec, SessionRec
from ..tail import read_new_lines
from .. import zstd_frames


def _timestamp(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return value / 1000 if abs(value) >= 100_000_000_000 else float(value)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class DshAdapter:
    name = 'dsh'
    tested = 'upstream'

    def poll(self, store, cfg):
        sessions, events, cursors = [], [], []
        warnings = 0
        home = os.environ.get('DSH_HOME', '').strip()
        default = Path.home() / '.dsh' / 'sessions'
        roots = [Path(home).expanduser() / 'sessions' if home and Path(p) == default else p for p in cfg.paths]
        search = SourceCfg(cfg.enabled, roots)
        paths = list(iter_files(search, '**/session*.jsonl')) + list(iter_files(search, '**/*.jsonl.zstd'))
        for path in paths:
            if is_forbidden(path):
                continue
            key = str(path); old = store.get_cursor(self.name, key)
            compressed = path.name.endswith('.zstd')
            if compressed:
                if not zstd_frames.available:
                    warnings += 1
                    continue
                stat = path.stat(); cursor = (stat.st_size, stat.st_mtime)
                if old and tuple(old) == cursor:
                    continue
                if is_forbidden(path):
                    continue
                lines = zstd_frames.read_text(path).splitlines()
            else:
                # Re-read the complete prefix for session and route metadata.
                lines, cursor = read_new_lines(path, None)
                if old and tuple(old) == cursor:
                    continue
            sid = path.parent.name
            provider, model, project = None, None, None
            started, last = None, 0.0
            offset = 0
            file_events = []
            for line in lines:
                begin = offset; offset += len(line.encode('utf-8')) + 1
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('not an object')
                    typ = row.get('type'); data = row.get('data') or {}
                    ts = _timestamp(row.get('time', row.get('createdAt')))
                    if ts:
                        started = ts if started is None else min(started, ts)
                        last = max(last, ts)
                    if typ == 'session':
                        sid = row.get('id') or sid; project = row.get('cwd') or project
                    elif typ == 'session/title-llm-request':
                        route = data.get('route') or data
                        provider = route.get('provider') or provider; model = route.get('model') or model
                    elif typ == 'assistant/message':
                        source = (data.get('message') or {}).get('source') or {}
                        provider = source.get('provider') or provider; model = source.get('model') or model
                        usage = data.get('usage')
                        ext = str(row.get('seq', begin))
                        emit = compressed or not old or cursor[0] < old[0] or begin >= old[0]
                        if usage is not None and emit:
                            output = usage.get('outputTokens')
                            reasoning = usage.get('reasoningTokens')
                            out = None if output is None and reasoning is None else (output or 0) + (reasoning or 0)
                            file_events.append((ext, ts, 'assistant', None, usage.get('inputTokens'), out,
                                                usage.get('cacheReadTokens'), usage.get('cacheWriteTokens'), model))
                        for index, tool in enumerate((data.get('toolCalls') or []) if emit else []):
                            file_events.append((f'{ext}:tool:{index}', ts, 'tool_call', tool.get('name'), None, None, None, None, model))
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    warnings += 1
            full_id = f'dsh:{sid}'
            sessions.append(SessionRec(full_id, self.name, normalize_provider(provider, model), model, project, None,
                                       started or 0.0, last, None, None, key))
            for ext, ts, kind, tool, tin, tout, read, write, event_model in file_events:
                events.append(EventRec(full_id, ext, ts, kind, tool, None, tin, tout, read, write, False, event_model))
            cursors.append((self.name, key, *cursor))
        return Batch(sessions, events, [], [], warnings, cursors)
