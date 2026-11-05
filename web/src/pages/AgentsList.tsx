import { useState } from 'react';
import type { FleetAgent } from '../api/types';
import { Segmented } from '../components/Segmented';
import { fmtRelative } from '../lib/format';
import { AgentSheet, useOpenAgent } from './AgentSheet';
import { RangePicker, StatusDot, count, messages, money, ownerName, useFleet } from './fleet-shared';
import './fleet.css';

type Filter = 'all' | 'active' | 'attention' | 'quiet';
const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };

export function Trend({ values }: { values: number[] }) {
  const top = Math.max(...values, 1);
  const points = values.map((value, index) => `${(index / Math.max(values.length - 1, 1)) * 88},${22 - (value / top) * 18}`).join(' ');
  const quiet = values.every((value) => value === 0);
  return (
    <svg className="trend" viewBox="0 0 88 24" aria-hidden="true">
      <polyline points={points} fill="none" stroke={quiet ? 'var(--line-strong)' : 'var(--muted)'} strokeWidth="1.25" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const matches = (agent: FleetAgent, filter: Filter) =>
  filter === 'all' ||
  (filter === 'active' && ['working', 'recent'].includes(agent.status)) ||
  agent.status === filter;

export function AgentsList() {
  const { range, setRange, data, error } = useFleet();
  const [open, setOpen] = useOpenAgent();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const now = Date.now() / 1000;
  const text = query.trim().toLowerCase();
  const shown = (data?.agents ?? []).filter((agent) => matches(agent, filter) && (!text || [agent.label, agent.owner, agent.host].some((value) => value?.toLowerCase().includes(text))));
  const groups = [...new Set(shown.map((agent) => agent.group))];
  const current = data?.agents.find((agent) => agent.host === open);
  return (
    <div className="hq-page">
      <header className="page-head">
        <div><h1>Agents</h1><p className="lede">Everyone’s agents, and how much they’re used.</p></div>
        <RangePicker value={range} onChange={setRange} />
      </header>
      {error && !data && <p className="muted">Couldn’t load your agents. Check that Agent HQ is running, then refresh.</p>}
      {data && (
        <>
          <div className="tools">
            <input type="search" aria-label="Filter by agent or owner" placeholder="Filter by agent or owner" value={query} onChange={(event) => setQuery(event.target.value)} />
            <Segmented value={filter} onChange={setFilter} options={[
              { value: 'all', label: 'All' },
              { value: 'active', label: 'Active' },
              { value: 'attention', label: 'Needs attention' },
              { value: 'quiet', label: 'Quiet' },
            ]} />
          </div>
          {shown.length ? (
            <div className="table-wrap">
              <table className="fleet-table">
                <thead><tr><th>Agent</th><th>Owner</th><th>Last active</th><th className="r">Messages</th><th className="r">Tasks</th><th className="r">Usage value</th><th className="r">Trend</th></tr></thead>
                <tbody>
                  {groups.map((group) => [
                    <tr className="group" key={group}><td colSpan={7}>{group}</td></tr>,
                    ...shown.filter((agent) => agent.group === group).map((agent) => (
                      <tr key={agent.host} data-selected={open === agent.host} onClick={() => setOpen(agent.host)}>
                        <td><div className="who"><StatusDot status={agent.status} /><button type="button">{agent.label}</button></div></td>
                        <td className="muted">{ownerName(agent)}</td>
                        <td className="muted">{agent.status === 'attention' ? 'Needs attention' : agent.last_seen ? fmtRelative(agent.last_seen, now) : 'No activity yet'}</td>
                        <td className="r num">{count(messages(agent))}</td>
                        <td className="r num">{count(agent.tasks)}</td>
                        <td className="r num">{money(agent.value_usd, agent.unpriced_tokens)}</td>
                        <td className="r"><Trend values={agent.cells} /></td>
                      </tr>
                    )),
                  ])}
                </tbody>
              </table>
            </div>
          ) : <p className="empty">No agents match. Try a different name or filter.</p>}
        </>
      )}
      {current && <AgentSheet key={current.host} agent={current} periodLabel={PERIOD[range]} onClose={() => setOpen(null)} />}
    </div>
  );
}
