"""Read the Zylos communication bridge without modifying its database."""
from datetime import datetime, timezone
from pathlib import Path

from .base import ro_connect
from ..config import is_forbidden
from ..records import Batch, EventRec, SessionRec


class ZylosAdapter:
    name = 'zylos'
    tested = 'upstream'

    def poll(self, store, cfg):
        sessions, events, cursors = [], [], []
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
                ts = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
                timestamps.append(ts)
                text = content[:200] if getattr(cfg, 'zylos_previews', False) and content is not None else None
                events.append(EventRec('zylos:comm', f'c4:{row_id}', ts, 'user' if direction == 'in' else 'assistant', channel, text, None, None, None, None, False, None))
                if status == 'failed':
                    events.append(EventRec('zylos:comm', f'c4:{row_id}:error', ts, 'error', channel, None, None, None, None, None, False, None))
            if rows:
                sessions.append(SessionRec('zylos:comm', self.name, None, None, str(Path('~/zylos').expanduser()), 'Zylos messages', min(timestamps), max(timestamps), None, None, str(path)))
                cursors.append((self.name, str(path), rows[-1][0], path.stat().st_mtime))
        return Batch(sessions, events, [], [], 0, cursors)
