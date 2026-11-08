"""The record of every control action, and the window after a password check that allows them.

The window lasts a working day (8 hours) per signed-in browser, and is kept in memory only, so a
restart of HQ asks once more.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

ELEVATED_S = 8 * 3600
CURRENT = threading.local()  # .user: who the request being handled on this thread is from


def _key(token):
    return hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) and token else None


class ControlLog:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()
        self._elevated = {}

    def log(self, entry):
        # The signed-in person making the request, when there is one (set per request by the server).
        user = getattr(CURRENT, 'user', None)
        line = json.dumps({'at': self.clock(), **({'user': user} if user and 'user' not in entry else {}), **entry})
        with self._lock, open(self.path, 'a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    def recent(self, limit=50):
        limit = max(1, min(500, limit))
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def elevate(self, token):
        key = _key(token)
        if not key:
            raise ValueError('not signed in')
        until = self.clock() + ELEVATED_S
        with self._lock:
            self._elevated = {k: v for k, v in self._elevated.items() if v > self.clock()}
            self._elevated[key] = until
        return until

    def elevated_until(self, token):
        key = _key(token)
        with self._lock:
            until = self._elevated.get(key) if key else None
        return until if until and until > self.clock() else None

    def elevated(self, token):
        return self.elevated_until(token) is not None
