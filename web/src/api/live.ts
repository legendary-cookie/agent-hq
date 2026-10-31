export function subscribeChanges(onChange: () => void): () => void {
  let source: EventSource; let timer: ReturnType<typeof setTimeout> | undefined; let debounce: ReturnType<typeof setTimeout> | undefined; let stopped = false; let delay = 1000; let reconnecting = false;
  function connect() {
    source = new EventSource(`/api/stream?token=${encodeURIComponent(sessionStorage.getItem('hq-token') ?? '')}`);
    source.addEventListener('changed', () => { clearTimeout(debounce); debounce = setTimeout(onChange, 1000); });
    source.onopen = () => { if (reconnecting) onChange(); reconnecting = false; delay = 1000; };
    source.onerror = () => { source.close(); if (stopped) return; reconnecting = true; timer = setTimeout(connect, delay); delay = Math.min(delay * 2, 15000); };
  }
  connect(); return () => { stopped = true; source.close(); clearTimeout(timer); clearTimeout(debounce); };
}
