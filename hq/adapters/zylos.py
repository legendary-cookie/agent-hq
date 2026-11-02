"""Read the Zylos communication bridge without modifying its database."""
from datetime import datetime, timezone
from pathlib import Path

from .base import ro_connect
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec


class ZylosAdapter:
    name = 'zylos'
    tested = 'upstream'

    def __init__(self, settings=None):
        # The app Config (or any object with `zylos_previews`). The collector passes
        # poll() only this source's SourceCfg, so the app-wide previews switch must
        # come from here.
        self.settings = settings

    def _previews(self, cfg) -> bool:
        if self.settings is not None:
            return bool(getattr(self.settings, 'zylos_previews', False))
        return bool(getattr(cfg, 'zylos_previews', False))

    def poll(self, store, cfg):
        sessions, events, cursors = [], [], []
        warnings = 0
        for source in cfg.paths:
            path = Path(source)
            if is_forbidden(path) or not path.is_file():
                continue
            old = store.get_cursor(self.name, str(path))
            if is_forbidden(path):
                continue
            conn = ro_connect(path)
            try:
                rows = conn.execute('SELECT id, direction, channel, content, status, timestamp FROM conversations WHERE id > ? ORDER BY id', (old[0] if old else 0,)).fetchall()
            finally:
                conn.close()
            timestamps = []
            for row_id, direction, channel, content, status, timestamp in rows:
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    ts = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
                except (ValueError, TypeError, AttributeError, OverflowError):
                    warnings += 1
                    continue
                timestamps.append(ts)
                text = content[:200] if self._previews(cfg) and content is not None else None
                events.append(EventRec('zylos:comm', f'c4:{row_id}', ts, 'user' if direction == 'in' else 'assistant', channel, text, None, None, None, None, False, None))
                if status == 'failed':
                    events.append(EventRec('zylos:comm', f'c4:{row_id}:error', ts, 'error', channel, None, None, None, None, None, False, None))
            if timestamps:
                sessions.append(SessionRec('zylos:comm', self.name, None, None, str(Path('~/zylos').expanduser()), 'Zylos messages', min(timestamps), max(timestamps), None, None, str(path)))
            if rows:
                cursors.append((self.name, str(path), rows[-1][0], path.stat().st_mtime))
        return Batch(sessions, events, [], [], warnings, cursors)
