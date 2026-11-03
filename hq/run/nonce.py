import secrets
import threading
import time
from collections.abc import Callable

from hq.run.request import RunRequest, request_hash


class NonceBook:
    def __init__(self, ttl_s: float = 60, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self.clock = clock
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._entries = {key: value for key, value in self._entries.items() if value[0] > now}

    def issue(self, req: RunRequest, resolved=None) -> tuple[str, float]:
        with self._lock:
            now = self.clock()
            self._prune(now)
            nonce = secrets.token_urlsafe(32)
            expires = now + self.ttl_s
            self._entries[nonce] = (expires, request_hash(req), resolved or req)
            return nonce, expires

    def redeem(self, nonce: str, req: RunRequest) -> bool:
        return self.redeem_resolved(nonce, req) is not None

    def redeem_resolved(self, nonce: str, req: RunRequest):
        with self._lock:
            self._prune(self.clock())
            entry = self._entries.pop(nonce, None) if isinstance(nonce, str) else None
            return entry[2] if entry is not None and secrets.compare_digest(entry[1], request_hash(req)) else None
