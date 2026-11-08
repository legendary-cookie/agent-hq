import threading
import time
from dataclasses import replace
from pathlib import Path

from hq.config import SourceCfg, match_account
from hq.records import Batch
from hq.remote import plan_limits, ssh, zylos_metrics
from hq.remote.config import SOURCES
from hq.remote.mirror import _target, apply_answer, cursors_for, prune_hosts
from hq.remote.namespace import prefix_batch


def _safe_target(root, host, name):
    try:
        return _target(root, host, name)
    except (ValueError, TypeError):
        return None


class RemoteCollector:
    def __init__(self, store, cfg, hosts, data_dir, adapter_factory, run_scan=None, scanner_text=None, clock=time.time):
        self.store, self.cfg, self.hosts = store, cfg, hosts
        self.remote_root = Path(data_dir) / 'remote'
        self.run_scan = run_scan or ssh.run_scan
        self.scanner_text = scanner_text if scanner_text is not None else Path(__file__).with_name('scanner.py').read_text(encoding='utf-8')
        self.clock = clock
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads = []
        self._callbacks = []
        self._health = {h.name: dict(state='pending', last_pass=None, last_ok=None, bytes=0, catching_up=False, error=None) for h in hosts}
        self._backoff = {h.name: h.interval_s for h in hosts}
        self._adapter_factory, self._data_dir = adapter_factory, Path(data_dir)
        self._adapters = {h.name: [a for a in adapter_factory(cfg, Path(data_dir)) if a.name in h.sources and a.name in SOURCES] for h in hosts}
        prune_hosts(self.remote_root, {h.name for h in hosts})

    def on_change(self, callback):
        with self._lock:
            self._callbacks.append(callback)

    def health(self):
        with self._lock:
            return {name: state.copy() for name, state in self._health.items()}

    def _pass(self, host):
        root = self.remote_root / host.name
        try:
            request = dict(sources=list(host.sources), cursors=cursors_for(root, host), cap_bytes=20000000, db_cap_bytes=30000000)
            metrics_cursor = None
            if zylos_metrics.NAME in host.sources:
                metrics_cursor = zylos_metrics.read_cursor(self.store, host)
                request['zylos_after'] = metrics_cursor[0]
            answer = self.run_scan(host, request, self.scanner_text)
            entries = answer.get('files', []) if isinstance(answer, dict) else []
            files = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) and len(entries) <= 2000 else []
            # Invalid paths are skipped here; apply_answer reports them as errors.
            pending = {e['path'] for e in files
                       if e.get('kind') == 'append' and e.get('offset') == 0 and e.get('path') not in request['cursors']
                       and (target := _safe_target(root, host, e.get('path'))) is not None and target.is_file()}
            result = apply_answer(answer, host, root)
            for entry in files:
                if entry.get('kind') == 'reset' or entry.get('path') in pending:
                    target = _safe_target(root, host, entry.get('path'))
                    if target is None:
                        continue
                    path = str(target)
                    for adapter in self._adapters[host.name]:
                        if self.store.get_cursor(adapter.name, path):
                            self.store.set_cursor(adapter.name, path, 0, 0)
            changed = False
            # Limit readings belong to the agent that saw them, so agents can be told apart.
            mine = plan_limits.agent_account(host.name)
            for adapter in self._adapters[host.name]:
                source = SourceCfg(True, [root / path for path in SOURCES[adapter.name]])
                batch = prefix_batch(adapter.poll(self.store, source), host.name)
                written = self.store.apply(replace(batch, limits=[]), lambda h, p, m: match_account(self.cfg, h, p, m), self.cfg.relabel, host=host.name)
                if batch.limits:
                    written |= self.store.apply(Batch([], [], batch.limits, []), lambda h, p, m: mine, (), host=host.name)
                if written:
                    self.store.derive_tasks({e.session_id for e in batch.events})
                changed |= written
            if plan_limits.NAME in host.sources and isinstance(answer, dict):
                changed |= self.store.apply(plan_limits.to_batch(answer.get(plan_limits.NAME)), lambda h, p, m: mine, (), host=host.name)
            metrics_more, metrics_notes = False, []
            if metrics_cursor is not None:
                # No derive_tasks here: one "task" per message would be meaningless, and rebuilding
                # them would rescan every message of a busy agent on every pass.
                batch, metrics_more, metrics_notes = zylos_metrics.to_batch(
                    answer.get('zylos_metrics') if isinstance(answer, dict) else None, host, metrics_cursor)
                changed |= self.store.apply(prefix_batch(batch, host.name), lambda h, p, m: match_account(self.cfg, h, p, m),
                                            self.cfg.relabel, host=host.name)
            with self._lock:
                self._backoff[host.name] = host.interval_s
                self._health[host.name] = dict(state='partial' if result.errors else 'ok', last_pass=self.clock(),
                                               last_ok=self._health[host.name].get('last_ok') if result.errors else self.clock(), bytes=result.bytes,
                                               catching_up=result.more or bool(result.resets) or metrics_more,
                                               error='; '.join(result.errors)[:500] or None,
                                               notes=(result.notes + metrics_notes)[:10])
                callbacks = list(self._callbacks) if changed else []
            for callback in callbacks:
                callback()
        except Exception as exc:
            with self._lock:
                self._backoff[host.name] = min(600, self._backoff[host.name] * 2)
                self._health[host.name].update(state='partial', last_pass=self.clock(), error=str(exc))

    def run_once(self):
        threads = [threading.Thread(target=self._pass, args=(host,), daemon=True) for host in self.hosts]
        for thread in threads: thread.start()
        for thread in threads: thread.join()

    def start(self):
        if any(t.is_alive() for t in self._threads): return
        self._stop.clear()
        def run(host):
            while not self._stop.is_set():
                self._pass(host)
                if self._stop.wait(self._backoff[host.name]): break
        self._threads = [threading.Thread(target=run, args=(host,), daemon=True) for host in self.hosts]
        for thread in self._threads: thread.start()

    def add_host(self, host, adapter_factory=None, data_dir=None):
        """Start watching a new agent without restarting HQ."""
        with self._lock:
            if any(h.name == host.name for h in self.hosts):
                return
            self.hosts.append(host)
            self._health[host.name] = dict(state='pending', last_pass=None, last_ok=None, bytes=0, catching_up=False, error=None)
            self._backoff[host.name] = host.interval_s
            self._adapters[host.name] = [a for a in self._adapter_factory(self.cfg, self._data_dir) if a.name in host.sources and a.name in SOURCES]
        if self._threads and not self._stop.is_set():
            def run():
                while not self._stop.is_set():
                    self._pass(host)
                    if self._stop.wait(self._backoff[host.name]): break
            thread = threading.Thread(target=run, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self):
        self._stop.set()
        for thread in self._threads: thread.join()
