/** Everything done to the fleet: control actions, alerts, automatic restarts, edits and login moves. */
import { useState } from 'react';
import { useApi } from '../api/client';
import { Segmented } from '../components/Segmented';
import './activity.css';

export type Entry = { at: number; action: string; host?: string; [key: string]: unknown };
type Kind = 'all' | 'control' | 'alerts' | 'changes' | 'problems';

const s = (value: unknown) => (value === undefined || value === null ? '' : String(value));

/** One plain sentence per entry, and which tab it belongs to. */
export function describe(entry: Entry): { text: string; kind: Exclude<Kind, 'all'>; bad: boolean } {
  const a = entry.action;
  const bad = a.endsWith('_failed');
  if (a.startsWith('power_') && !bad) return { text: `${({ power_start: 'Started', power_stop: 'Stopped', power_shutdown: 'Shut down', power_reboot: 'Restarted' } as Record<string, string>)[a] ?? a} the container`, kind: 'control', bad };
  const known: Record<string, [string, Exclude<Kind, 'all'>]> = {
    restart_claude: ['Restarted the Claude session', 'control'],
    message_sent: [`Sent a message (${s(entry.chars)} characters)`, 'control'],
    terminal_open: [`Opened a ${entry.mode === 'root' ? 'root shell' : entry.mode === 'control' ? 'controlling terminal' : 'watching terminal'}`, 'control'],
    terminal_closed: [`Closed the ${entry.mode === 'root' ? 'root shell' : 'terminal'}${entry.typed_bytes ? `, ${s(entry.typed_bytes)} bytes typed` : ''}`, 'control'],
    elevated: ['Confirmed the HQ password', 'control'],
    auto_restart_claude: ['HQ restarted Claude because its session had stopped', 'control'],
    alert_sent: [`Alert sent: ${s(entry.text)}`, 'alerts'],
    alert_test: ['Test alert sent', 'alerts'],
    alert_settings_changed: [`Changed alert settings${entry.token_changed ? ', including the bot token' : ''}`, 'changes'],
    settings_changed: [`Changed ${s(entry.section)} settings${entry.service ? `: ${s(entry.service)} ${s(entry.service_action)}` : ''}`, 'changes'],
    memory_edited: [`${entry.created ? 'Added' : 'Edited'} memory file ${s(entry.path)}`, 'changes'],
    skill_edited: [`${entry.created ? 'Added' : 'Edited'} skill file ${s(entry.path)}`, 'changes'],
    update_started: [`Started ${s(entry.kind)} update${entry.version ? ` to ${s(entry.version)}` : ''}`, 'changes'],
    update_done: [`Finished ${s(entry.kind)} update${entry.snapshot ? `, snapshot ${s(entry.snapshot)} kept` : ''}`, 'changes'],
    rollback: [`Rolled back to snapshot ${s(entry.snapshot)}`, 'changes'],
    snapshot_deleted: [`Deleted snapshot ${s(entry.snapshot)}`, 'changes'],
    create_started: [`Started creating this agent from ${s(entry.source)}`, 'changes'],
    create_done: ['Finished creating this agent', 'changes'],
    mcp_call: [`AI agent key “${s(entry.key)}” used ${s(entry.tool)}${entry.error ? ` (refused: ${s(entry.error)})` : ''}`, 'control'],
    agent_key_created: [`Made an AI agent key “${s(entry.name)}”${Array.isArray(entry.scopes) && entry.scopes.length ? ` that can also ${(entry.scopes as string[]).join(' and ')}` : ', read only'}`, 'changes'],
    agent_key_revoked: ['Revoked an AI agent key', 'changes'],
    router_key_assigned: ['Assigned a 9router key', 'changes'],
    router_key_create: ['Created a 9router key', 'changes'],
    router_key_pause: ['Paused a 9router key', 'changes'],
    router_key_resume: ['Resumed a 9router key', 'changes'],
    router_key_delete: ['Deleted a 9router key', 'changes'],
    router_key_reveal: ['Copied a 9router key', 'changes'],
    signed_in: ['Signed in', 'changes'],
    password_changed: ['Changed their password', 'changes'],
    account_create_user: ['Added a user', 'changes'],
    account_update_user: ['Changed a user', 'changes'],
    account_set_password: ['Reset a user’s password', 'changes'],
    account_delete_user: ['Deleted a user', 'changes'],
    account_create_role: ['Added a role', 'changes'],
    account_update_role: ['Changed a role', 'changes'],
    account_delete_role: ['Deleted a role', 'changes'],
    login_assign: [`Moved to login ${s(entry.login)}${entry.from ? ` from ${s(entry.from)}` : ''}`, 'changes'],
    login_sign_in_started: [`Started a new sign-in for login ${s(entry.login)}`, 'changes'],
  };
  if (known[a]) return { text: known[a][0], kind: known[a][1], bad };
  if (bad) return { text: `${a.replace(/_failed$/, '').replace(/_/g, ' ')} failed${entry.error ? `: ${s(entry.error)}` : ''}`, kind: 'problems', bad };
  return { text: a.replace(/_/g, ' '), kind: 'changes', bad };
}

export function Activity() {
  const { data, error } = useApi<{ entries: Entry[] }>('/api/activity?limit=1000');
  const { data: fleet } = useApi<{ agents: { host: string; label: string }[] }>('/api/fleet?range=today', { live: false });
  const [kind, setKind] = useState<Kind>('all');
  const [host, setHost] = useState('');
  const [text, setText] = useState('');
  const names = new Map((fleet?.agents ?? []).map((agent) => [agent.host, agent.label]));
  const name = (h?: string) => (h ? (h.startsWith('login:') ? `Login ${h.slice(6)}` : names.get(h) ?? h) : 'HQ');
  const needle = text.trim().toLowerCase();
  const rows = (data?.entries ?? []).map((entry) => ({ entry, ...describe(entry) })).filter((row) =>
    (kind === 'all' || row.kind === kind || (kind === 'problems' && row.bad)) && (!host || row.entry.host === host)
    && (!needle || `${name(row.entry.host)} ${row.text}`.toLowerCase().includes(needle)));
  const hosts = [...new Set((data?.entries ?? []).map((entry) => entry.host).filter((h): h is string => !!h))].sort((a, b) => name(a).localeCompare(name(b)));
  let lastDay = '';
  return (
    <div className="hq-page">
      <header className="page-head">
        <div><h1>Activity</h1><p className="lede">Everything done to your agents from HQ, by you or by HQ itself, newest first.</p></div>
      </header>
      <div className="tools">
        <input type="search" aria-label="Search activity" placeholder="Search" value={text} onChange={(event) => setText(event.target.value)} />
        <select aria-label="Agent" value={host} onChange={(event) => setHost(event.target.value)}>
          <option value="">All agents</option>
          {hosts.map((h) => <option key={h} value={h}>{name(h)}</option>)}
        </select>
        <Segmented value={kind} onChange={setKind} options={[
          { value: 'all', label: 'All' }, { value: 'control', label: 'Control' }, { value: 'changes', label: 'Changes' },
          { value: 'alerts', label: 'Alerts' }, { value: 'problems', label: 'Problems' },
        ]} />
      </div>
      {error && !data && <p className="muted">Couldn’t load the activity. Check that HQ is running, then refresh.</p>}
      {data && (rows.length ? (
        <ol className="activity">
          {rows.map(({ entry, text: line, bad }, index) => {
            const when = new Date(entry.at * 1000);
            const day = when.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
            const head = day !== lastDay ? (lastDay = day) : null;
            return [
              head && <li key={`d${index}`} className="activity-day">{head}</li>,
              <li key={index} className="activity-row" data-bad={bad || undefined}>
                <time className="num muted" dateTime={when.toISOString()}>{when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</time>
                <span className="activity-who">{name(entry.host)}</span>
                <span className="activity-what">{line}{typeof entry.user === 'string' && <span className="muted"> · by {entry.user}</span>}</span>
              </li>,
            ];
          })}
        </ol>
      ) : <p className="empty">Nothing matches.</p>)}
      <p className="note">Kept on the HQ container in control-log.jsonl and login-changes.jsonl. Message text and passwords are never recorded, only that something was sent.</p>
    </div>
  );
}
