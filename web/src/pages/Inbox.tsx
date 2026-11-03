import { useEffect, useRef, useState } from 'react';
import { answerApproval, useApi } from '../api/client';
import type { Approval } from '../api/types';
import './inbox.css';

export function Inbox() {
  const { data, error, reload } = useApi<Approval[]>('/api/approvals?state=waiting');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);
  const [enabled, setEnabled] = useState(false);
  const notified = useRef(new Set<string>());
  useEffect(() => {
    const id = new URLSearchParams(location.hash.split('?')[1] ?? '').get('approval');
    if (id && data?.some((item) => item.id === id)) document.getElementById(`approval-${id}`)?.scrollIntoView?.({ block: 'center' });
  }, [data]);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    if (!enabled || typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    for (const item of data ?? []) if (!notified.current.has(item.id)) {
      notified.current.add(item.id);
      new Notification(`Run ${item.run_id}`, { body: item.tool_name });
    }
  }, [data, enabled]);
  async function enableNotifications() {
    if (typeof Notification === 'undefined') { setMessage('Notifications are unavailable'); return; }
    const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
    if (permission === 'granted') { setEnabled(true); window.dispatchEvent(new Event('hq-notifications-enabled')); }
    else setMessage('Notifications were not allowed');
  }
  async function answer(item: Approval, decision: string) {
    setBusy(item.id); setMessage('');
    try { await answerApproval(item.id, decision, notes[item.id] ?? ''); reload(); }
    catch (err) { setMessage(err instanceof Error ? err.message : String(err)); reload(); }
    finally { setBusy(''); }
  }
  const countdown = (expires: number) => { const seconds = Math.max(0, Math.ceil(expires - now)); return seconds < 60 ? `${seconds} s left` : `${Math.floor(seconds / 60)} min left`; };
  return <section className="inbox-page"><header><h1>Inbox</h1><button onClick={() => void enableNotifications()}>Enable notifications</button></header>
    {error && <p role="alert">{error.message}</p>}{message && <p role="status">{message}</p>}
    {data?.length === 0 && <p>No approvals waiting.</p>}
    {(data ?? []).map((item) => <article className="inbox-item" id={`approval-${item.id}`} key={item.id}>
      <h2>{item.run_id} · {item.tool_name}</h2><p>{item.summary}</p><p>{countdown(item.expires_at)}</p>
      <label htmlFor={`note-${item.id}`}>Note</label><textarea id={`note-${item.id}`} maxLength={500} value={notes[item.id] ?? ''} onChange={(e) => setNotes({ ...notes, [item.id]: e.target.value })} />
      <div><button disabled={!!busy} onClick={() => void answer(item, 'allow_once')}>Allow once</button> <button disabled={!!busy} onClick={() => void answer(item, 'allow_run')}>Allow for this run</button> <button disabled={!!busy} onClick={() => void answer(item, 'deny')}>Deny</button></div>
    </article>)}
  </section>;
}
