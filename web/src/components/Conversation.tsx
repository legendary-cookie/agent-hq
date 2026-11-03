import { useState } from 'react';
import { prepareMessage, sendMessage } from '../api/client';
import type { Approval, Run } from '../api/types';

export function Conversation({ run, output, approvals, onSent }: { run: Pick<Run, 'id' | 'status' | 'prompt_preview'>; output: string[]; approvals: Approval[]; onSent?: () => void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  async function send() {
    setSending(true); setError('');
    try { const { nonce } = await prepareMessage(run.id, text); await sendMessage(run.id, text, nonce); setText(''); onSent?.(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSending(false); }
  }
  return <section><h3>Conversation</h3><p><strong>Your task:</strong> {run.prompt_preview}</p>
    <pre className="run-output">{output.join('\n')}</pre>
    {approvals.filter((item) => item.run_id === run.id).map((item) => <p key={item.id}>Approval: <a href={`#/inbox?approval=${encodeURIComponent(item.id)}`}>{item.tool_name} · {item.summary}</a></p>)}
    <label>Message<textarea maxLength={100000} value={text} onChange={(e) => setText(e.target.value)} /></label>
    <button disabled={!['idle', 'done'].includes(run.status) || !text.trim() || sending} onClick={() => void send()}>{sending ? 'Sending…' : 'Send'}</button>{error && <p role="alert">{error}</p>}
  </section>;
}
