"""Read plain and independently framed zstd DeepSeek Harness session logs."""
import json
from dataclasses import replace
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
    tested = 'real'  # verified on 28 real v4 sessions (tokens, tools, prompts, turns, subagents)

    def __init__(self):
        # Subagent links. In v4 logs a child session (delegationDepth > 0) reports to
        # its parent: the PARENT's log holds a user/message whose source.kind is
        # 'agent-message' and whose senderSessionId is the child (verified on real logs).
        # The report and the child's own log may change in different passes, so the
        # links and each child's last record are kept across passes.
        self._parent_of = {}
        self._depth = {}
        self._last = {}

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
            provider, model, project, parent, depth = None, None, None, None, 0
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
                    ext = str(row.get('seq', begin))
                    emit = compressed or not old or cursor[0] < old[0] or begin >= old[0]
                    if typ == 'session':
                        sid = row.get('id') or sid; project = row.get('cwd') or project
                        depth = row.get('delegationDepth') or 0
                    # v4 records (shapes confirmed on real session.v4.jsonl.zstd logs):
                    elif typ == 'request/header':
                        config = (data.get('header') or {}).get('config') or {}
                        provider = config.get('provider') or provider; model = config.get('model') or model
                    elif typ == 'user/message':
                        source = data.get('source') or {}
                        kind = source.get('kind')
                        # Only real prompts and messages from a delegating agent start a task;
                        # tool-jobs, time-context, skill-catalog etc. are injected context.
                        if kind in (None, 'user', 'agent-message') and emit:
                            file_events.append((ext, ts, 'user', None, None, None, None, None, None))
                        sender = source.get('senderSessionId')
                        if kind == 'agent-message' and sender and sender != sid:
                            self._parent_of.setdefault(f'dsh:{sender}', f'dsh:{sid}')
                    elif typ == 'tool/call':
                        if emit:
                            file_events.append((f"call:{data.get('callId') or ext}", ts, 'tool_call', data.get('name'),
                                                None, None, None, None, model))
                    elif typ == 'turn/end':
                        if emit:
                            reason = str(data.get('reason') or '')
                            kind = 'error' if 'error' in reason or 'fail' in reason else 'turn_end'
                            file_events.append((f'{ext}:end', ts, kind, None, None, None, None, None, None))
                    elif typ == 'compaction/summary':
                        usage = data.get('usage')
                        if usage is not None and emit:
                            out = (usage.get('outputTokens') or 0) + (usage.get('reasoningTokens') or 0)
                            file_events.append((f'{ext}:compaction', ts, 'assistant', None, usage.get('inputTokens'), out,
                                                usage.get('cacheReadTokens'), usage.get('cacheWriteTokens'),
                                                data.get('model') or model))
                    # v3 records (upstream test logs):
                    elif typ == 'session/title-llm-request':
                        route = data.get('route') or data
                        provider = route.get('provider') or provider; model = route.get('model') or model
                    elif typ == 'assistant/message':
                        source = (data.get('message') or {}).get('source') or {}
                        provider = source.get('provider') or provider; model = source.get('model') or model
                        usage = data.get('usage')
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
            self._depth[full_id] = depth
            record = SessionRec(full_id, self.name, normalize_provider(provider, model), model, project, None,
                                started or 0.0, last, None, None, key)
            self._last[full_id] = record
            sessions.append(record)
            for ext, ts, kind, tool, tin, tout, read, write, event_model in file_events:
                events.append(EventRec(full_id, ext, ts, kind, tool, None, tin, tout, read, write, False, event_model))
            cursors.append((self.name, key, *cursor))
        # Attach parents to children (depth > 0), including children whose own log did
        # not change this pass but whose parent's report just arrived.
        linked = {s.id: s for s in sessions}
        for child, parent_id in self._parent_of.items():
            if self._depth.get(child, 0) > 0 and child in self._last:
                linked[child] = replace(linked.get(child, self._last[child]), parent_id=parent_id)
        return Batch(list(linked.values()), events, [], [], warnings, cursors)
