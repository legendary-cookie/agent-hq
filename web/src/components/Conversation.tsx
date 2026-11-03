import { useState } from 'react';
import { prepareMessage, sendMessage } from '../api/client';
import type { Approval, Run } from '../api/types';

type Entry = { kind: 'text' | 'note' | 'tool' | 'approval'; text: string; approval?: Approval };

function conversationEntries(output: string[], approvals: Approval[]): Entry[] {
  const entries: Entry[] = [];
  const placed = new Set<string>();
  const addText = (value: unknown) => { if (typeof value === 'string' && value.trim()) entries.push({ kind: 'text', text: value }); };
  for (const line of output) {
    if (line.startsWith('[agent-hq]')) {
      entries.push({ kind: 'note', text: line.slice('[agent-hq]'.length).trim() });
      continue;
    }
    try {
      const data = JSON.parse(line);
      if (!data || typeof data !== 'object') continue;
      const kind = data.type ?? data.event;
      if (kind === 'system' || kind === 'rate_limit_event') continue;
      if (kind === 'control_request') {
        const approval = approvals.find((item) => item.id === data.request_id || item.id.endsWith(`:${data.request_id}`));
        if (approval && !placed.has(approval.id)) { entries.push({ kind: 'approval', text: '', approval }); placed.add(approval.id); }
      } else if (kind === 'assistant' && Array.isArray(data.message?.content)) {
        for (const block of data.message.content) {
          if (block.type === 'text') addText(block.text);
          if (block.type === 'tool_use' && typeof block.name === 'string') {
            const value = block.input && typeof block.input === 'object' ? Object.values(block.input)[0] : undefined;
            const input = value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value);
            entries.push({ kind: 'tool', text: `${block.name}${input ? ` ${input.slice(0, 120)}` : ''}` });
          }
        }
      } else if (kind === 'item.completed' && data.item?.type === 'agent_message') addText(data.item.text);
      else if (kind === 'text' || data.part?.type === 'text') addText(data.part?.text ?? data.text);
      else if (kind === 'step_update') addText(data.text ?? data.step?.text ?? data.step_update?.text);
      else if (kind === 'result') addText(typeof data.result === 'string' ? data.result : data.result?.text);
    } catch { /* Ignore non-JSON output. */ }
  }
  for (const approval of approvals) if (!placed.has(approval.id)) entries.push({ kind: 'approval', text: '', approval });
  return entries;
}

export function Conversation({ run, output, approvals, onSent }: { run: Pick<Run, 'id' | 'status' | 'prompt_preview'>; output: string[]; approvals: Approval[]; onSent?: () => void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [raw, setRaw] = useState(false);
  const [error, setError] = useState('');
  async function send() {
    setSending(true); setError('');
    try { const { nonce } = await prepareMessage(run.id, text); await sendMessage(run.id, text, nonce); setText(''); onSent?.(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setSending(false); }
  }
  const entries = conversationEntries(output, approvals.filter((item) => item.run_id === run.id));
  return <section><h3>Conversation</h3><p><strong>Your task:</strong> {run.prompt_preview}</p>
    <button onClick={() => setRaw(!raw)}>{raw ? 'Hide raw output' : 'Show raw output'}</button>
    {raw ? <pre className="run-output">{output.join('\n')}</pre> : <div>{entries.map((entry, index) => entry.kind === 'approval' && entry.approval ? <p key={index}>Approval: <a href={`#/inbox?approval=${encodeURIComponent(entry.approval.id)}`}>{entry.approval.tool_name} · {entry.approval.summary}</a></p> : entry.kind === 'note' ? <p key={index}><small>{entry.text}</small></p> : <p key={index} style={{ whiteSpace: 'pre-wrap' }}>{entry.text}</p>)}{!entries.some((entry) => entry.kind === 'text') && <p><small>{run.status}</small></p>}</div>}
    <label>Message<textarea maxLength={100000} value={text} onChange={(e) => setText(e.target.value)} /></label>
    <button disabled={!['idle', 'done', 'stopped', 'interrupted', 'timed_out'].includes(run.status) || !text.trim() || sending} onClick={() => void send()}>{sending ? 'Sending…' : 'Send'}</button>{error && <p role="alert">{error}</p>}
  </section>;
}
