import threading
import time
from .config import match_account

class Collector:
    def __init__(self, store, cfg, adapters, interval=5.0):
        self.store, self.cfg, self.adapters, self.interval = store, cfg, adapters, interval
        self._stop = threading.Event()
        self._thread = None
        self._callbacks = []
        self._health = {a.name: {'state': 'disabled', 'last_pass': None, 'warnings': 0, 'error': None} for a in adapters}
        self._lock = threading.RLock()

    def on_change(self, callback):
        with self._lock: self._callbacks.append(callback)

    def health(self):
        with self._lock: return {k: v.copy() for k, v in self._health.items()}

    def run_once(self):
        changed = False
        for adapter in self.adapters:
            source = self.cfg.sources.get(adapter.name)
            now = time.time()
            if source is None or not source.enabled:
                state = {'state': 'disabled', 'last_pass': now, 'warnings': 0, 'error': None}
            else:
                try:
                    batch = adapter.poll(self.store, source)
                except Exception as exc:  # Isolate adapter failures and report their cause.
                    state = {'state': 'partial', 'last_pass': now, 'warnings': 0, 'error': str(exc)}
                else:
                    written = self.store.apply(batch, lambda h, p, m: match_account(self.cfg, h, p, m))
                    if written:
                        self.store.derive_tasks({e.session_id for e in batch.events})
                    changed |= written
                    state = {'state': 'ok', 'last_pass': now, 'warnings': batch.warnings, 'error': None}
            with self._lock: self._health[adapter.name] = state
        if changed:
            with self._lock: callbacks = list(self._callbacks)
            for callback in callbacks: callback()
        return changed

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        def run():
            while not self._stop.is_set():
                self.run_once()
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join()
