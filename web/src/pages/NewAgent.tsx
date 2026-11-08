import { useEffect, useState } from 'react';
import { api, useApi } from '../api/client';
import type { Fleet } from '../api/types';
import { usePasswordCheck } from './AgentControl';
import './fleet.css';
import './control.css';
import './new-agent.css';

interface CreateJob { id: string; name: string; label: string; vmid: number; host: string; state: 'running' | 'done' | 'failed'; steps: { at: number; text: string }[]; error: string | null }
interface Suggest { vmid?: number; ip?: string | null; ips?: string[]; sources?: string[]; error?: string; jobs: CreateJob[] }

const slug = (text: string) => text.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 25);

export function NewAgent() {
  const { data, reload } = useApi<Suggest>('/api/agents/new', { live: false });
  const { data: fleet } = useApi<Fleet>('/api/fleet?range=today', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const [form, setForm] = useState({ label: '', name: '', owner: '', group: 'Staff', role: '', source: 'ct309-template', vmid: '', ip: '', bot_token: '', owner_chat_id: '', owner_username: '' });
  const [nameTouched, setNameTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const job = data?.jobs[0];
  useEffect(() => {
    if (data?.vmid && !form.vmid) setForm((old) => ({ ...old, vmid: String(data.vmid), ip: data.ip ?? '' }));
  }, [data]);
  useEffect(() => {
    if (job?.state !== 'running') return;
    const timer = window.setInterval(reload, 3000);
    return () => window.clearInterval(timer);
  }, [job?.state, reload]);
  const set = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const value = event.target.value;
    setForm((old) => ({ ...old, [key]: value, ...(key === 'label' && !nameTouched ? { name: slug(value) } : {}) }));
  };
  const label = (host: string) => fleet?.agents.find((agent) => agent.host === host)?.label ?? host;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const question = `Create ${form.label} as CT ${form.vmid} at ${form.ip}, copied from ${label(form.source)}?\n\nThe copy is wiped before it can connect to anything: memory about people, conversations, logs, logins and identities are removed, and company knowledge is kept. It takes several minutes.`;
    if (!window.confirm(question) || !(await ensure())) return;
    setBusy(true);
    try {
      await api('/api/agents/create', { method: 'POST', body: JSON.stringify({ ...form, vmid: Number(form.vmid) }) });
      setForm((old) => ({ ...old, bot_token: '' }));
      reload();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (data?.error && !data.sources) return <div className="hq-page"><h1>New agent</h1><p className="control-error">{data.error}</p></div>;
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>New agent</h1>
          <p className="lede">Copies an existing agent, wipes everything personal from the copy before it can connect, and gives it its own Telegram bot, owner and memory.</p>
        </div>
      </header>
      {job && (
        <section className={`create-job ${job.state}`} aria-label="Creation progress">
          <b>{job.label} (CT {job.vmid}): {job.state === 'running' ? 'in progress' : job.state === 'done' ? 'created' : 'failed'}</b>
          <ol>{job.steps.map((step, i) => <li key={i}>{step.text}</li>)}</ol>
          {job.state === 'done' && <a href="#/agents">See it on the Agents page</a>}
        </section>
      )}
      <form className="create-form" onSubmit={submit}>
        <fieldset>
          <legend>Who it is</legend>
          <label><span>Display name</span><input required maxLength={40} value={form.label} onChange={set('label')} placeholder="Nadia" /></label>
          <label><span>Short name</span><input aria-label="Short name" required pattern="[a-z][a-z0-9-]{1,24}" value={form.name} onChange={(event) => { setNameTouched(true); set('name')(event); }} /><small>Used for the container: {form.name || 'name'}-ai-agent</small></label>
          <label><span>Owner</span><input required maxLength={40} value={form.owner} onChange={set('owner')} placeholder="Alex Smith" /></label>
          <label><span>Group</span><select value={form.group} onChange={set('group')}><option>Staff</option><option>Management</option><option>Department</option></select></label>
          <label className="wide"><span>What it does</span><textarea aria-label="What it does" maxLength={600} rows={3} value={form.role} onChange={set('role')} placeholder="Helps Nadia with sales follow-ups and order questions." /><small>Written into the agent’s identity. It can be changed later.</small></label>
        </fieldset>
        <fieldset>
          <legend>Telegram</legend>
          <label className="wide"><span>New bot token</span><input aria-label="New bot token" required type="password" autoComplete="off" value={form.bot_token} onChange={set('bot_token')} placeholder="From @BotFather" /><small>Create a new bot with @BotFather first. Never reuse another agent’s token: two agents on one bot break each other.</small></label>
          <label><span>Owner’s Telegram ID</span><input aria-label="Owner’s Telegram ID" inputMode="numeric" value={form.owner_chat_id} onChange={set('owner_chat_id')} placeholder="123456789" /><small>Only this person can message it directly.</small></label>
          <label><span>Owner’s username</span><input value={form.owner_username} onChange={set('owner_username')} placeholder="@nadia" /></label>
        </fieldset>
        <fieldset>
          <legend>Where it runs</legend>
          <label><span>Copy from</span><select aria-label="Copy from" value={form.source} onChange={set('source')}>{(data?.sources ?? []).map((host) => <option key={host} value={host}>{label(host)}</option>)}</select><small>Its setup, skills and company knowledge are copied. Choose a template you trust.</small></label>
          <label><span>Container number</span><input required inputMode="numeric" value={form.vmid} onChange={set('vmid')} /></label>
          <label><span>IP address</span><input aria-label="IP address" required value={form.ip} onChange={set('ip')} list="free-ips" /><datalist id="free-ips">{(data?.ips ?? []).map((ip) => <option key={ip} value={ip} />)}</datalist><small>Suggested from addresses no container uses.</small></label>
        </fieldset>
        {error && <p className="control-error" role="alert">{error}</p>}
        <div className="control-buttons">
          <button type="submit" className="plain-button primary" disabled={busy || job?.state === 'running'}>{busy ? 'Starting…' : 'Create agent'}</button>
          <span className="muted">It reaches Claude through 9router with the settings of the agent it copies; give it its own key on the <a href="#/router">Router</a> page. HXA Connect stays off until it’s issued its own identity.</span>
        </div>
      </form>
      {dialog}
    </div>
  );
}
