import threading
import time
from .config import match_account

class Collector:
    def __init__(self, store, cfg, adapters, interval=5.0):
        self.store, self.cfg, self.adapters, self.interval = store, cfg, adapters, interval
        self._stop = threading.Event()
        self._thread = None
        self._callbacks = []
        self._health = {a.name: {'state': 'disabled', 'last_pass': None, 'warnings': 0, 'error': None, 'last_error_at': None} for a in adapters}
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
            last_error_at = self._health[adapter.name]['last_error_at']
            if source is None or not source.enabled:
                state = {'state': 'disabled', 'last_pass': now, 'warnings': 0, 'error': None, 'last_error_at': last_error_at}
            else:
                try:
                    batch = adapter.poll(self.store, source)
                    written = self.store.apply(batch, lambda h, p, m: match_account(self.cfg, h, p, m))
                    if written:
                        self.store.derive_tasks({e.session_id for e in batch.events})
                    changed |= written
                    state = {'state': 'ok', 'last_pass': now, 'warnings': batch.warnings, 'error': None, 'last_error_at': last_error_at}
                except Exception as exc:
                    state = {'state': 'partial', 'last_pass': now, 'warnings': 0, 'error': str(exc), 'last_error_at': time.time()}
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
                try: self.run_once()
                except Exception: pass
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join()
