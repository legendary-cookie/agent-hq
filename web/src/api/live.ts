import { api } from './client';

const subscribers = new Set<() => void>();
let source: EventSource | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
let debounce: ReturnType<typeof setTimeout> | undefined;
let delay = 1000;
let reconnecting = false;
let errors = 0;
export let changeCounter = 0;

function notify() {
  changeCounter++;
  subscribers.forEach((subscriber) => subscriber());
}

function connect() {
  const connection = new EventSource(
    `/api/stream?token=${encodeURIComponent(localStorage.getItem('hq-token') ?? '')}`,
  );
  source = connection;
  connection.addEventListener('changed', () => {
    if (source !== connection) return;
    changeCounter++;
    clearTimeout(debounce);
    debounce = setTimeout(notify, 1000);
  });
  connection.onopen = () => {
    if (source !== connection) return;
    if (reconnecting) notify();
    reconnecting = false;
    delay = 1000;
    errors = 0;
  };
  connection.onerror = () => {
    if (source !== connection) return;
    connection.close();
    source = undefined;
    reconnecting = true;
    errors += 1;
    if (errors === 3) void api('/api/health').catch(() => {});
    timer = setTimeout(connect, delay);
    delay = Math.min(delay * 2, 15000);
  };
}

// Browsers allow only 6 connections per host. A hidden tab closes its live feed so it never
// starves other Agent HQ tabs, and reconnects (with one refetch) when shown again.
let visibilityBound = false;
function onVisibility() {
  if (subscribers.size === 0) return;
  if (document.visibilityState === 'hidden') {
    source?.close();
    source = undefined;
    clearTimeout(timer);
  } else if (!source) {
    reconnecting = true;
    connect();
  }
}

export function subscribeChanges(onChange: () => void): () => void {
  const subscriber = () => onChange();
  subscribers.add(subscriber);
  if (!visibilityBound && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibility);
    visibilityBound = true;
  }
  if (subscribers.size === 1 && (typeof document === 'undefined' || document.visibilityState !== 'hidden')) connect();
  return () => {
    subscribers.delete(subscriber);
    if (subscribers.size === 0) {
      source?.close();
      source = undefined;
      clearTimeout(timer);
      clearTimeout(debounce);
      delay = 1000;
      reconnecting = false;
      errors = 0;
    }
  };
}
