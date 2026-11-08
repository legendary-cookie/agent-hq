import { useEffect, useState } from 'react';
import { useApi } from '../api/client';
import { useMe } from '../api/me';
import type { FleetAgent } from '../api/types';
import { Segmented } from '../components/Segmented';
import { PickBox } from '../components/PickBox';
import { fmtRelative } from '../lib/format';
import { AgentSheet, useOpenAgent } from './AgentSheet';
import { BulkBar } from './BulkActions';
import { UsageFigure } from '../components/UsageFigure';
import { unattributedLine, usageLabel, usageView } from '../lib/usage';
import { SortHeader, useSort } from '../lib/useSort';
import { RangePicker, StatusDot, count, messages, ownerName, useFleet } from './fleet-shared';
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
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const { can } = useMe();
  // Agents with an update running right now, so they show it here too.
  const { data: updates } = useApi<{ jobs?: { host: string; state: string }[] }>('/api/updates');
  const updating = new Set((updates?.jobs ?? []).filter((job) => job.state === 'running').map((job) => job.host));
  // A new filter starts a new selection, so an action never reaches agents that are no longer shown.
  useEffect(() => setPicked(new Set()), [filter, query]);
  const now = Date.now() / 1000;
  const text = query.trim().toLowerCase();
  const shown = (data?.agents ?? []).filter((agent) => matches(agent, filter) && (!text || [agent.label, agent.owner, agent.host].some((value) => value?.toLowerCase().includes(text))));
  const groups = [...new Set(shown.map((agent) => agent.group))];
  const plan = data?.plan;
  const usage = (agent: FleetAgent) => usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens });
  const sorter = useSort(shown, {
    columns: {
      agent: (agent) => agent.label, owner: (agent) => agent.owner, last: (agent) => agent.last_seen,
      messages: (agent) => messages(agent), tasks: (agent) => agent.tasks, usage: (agent) => usage(agent).sort,
    },
    storageKey: 'agents-list',
  });
  const before = unattributedLine(plan);
  const current = data?.agents.find((agent) => agent.host === open);
  const toggle = (host: string) => setPicked((old) => { const next = new Set(old); if (next.has(host)) next.delete(host); else next.add(host); return next; });
  const allShown = shown.length > 0 && shown.every((agent) => picked.has(agent.host));
  const pickAll = () => setPicked(allShown ? new Set() : new Set(shown.map((agent) => agent.host)));
  const chosen = (data?.agents ?? []).filter((agent) => picked.has(agent.host));
  return (
    <div className="hq-page">
      <header className="page-head">
        <div><h1>Agents</h1><p className="lede">Everyone’s agents, and how much they’re used.</p></div>
        {can('agents.create') && <a className="new-agent-link" href="#/new-agent">New agent</a>}
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
          {chosen.length > 0 && <BulkBar agents={chosen} onClear={() => setPicked(new Set())} />}
          {shown.length ? (
            <div className="table-wrap">
              <table className="fleet-table">
                <thead><tr><th className="pick"><input type="checkbox" aria-label="Select every agent shown" checked={allShown} onChange={pickAll} /></th><SortHeader as="th" sorter={sorter} col="agent">Agent</SortHeader><SortHeader as="th" sorter={sorter} col="owner">Owner</SortHeader><SortHeader as="th" sorter={sorter} col="last">Last active</SortHeader><SortHeader as="th" sorter={sorter} col="messages" className="r">Messages</SortHeader><SortHeader as="th" sorter={sorter} col="tasks" className="r">Tasks</SortHeader><SortHeader as="th" sorter={sorter} col="usage" className="r">{usageLabel(plan, 'Usage value')}</SortHeader><th className="r">Trend</th></tr></thead>
                <tbody>
                  {groups.map((group) => [
                    <tr className="group" key={group}>
                      <td className="pick"><PickBox hosts={shown.filter((agent) => agent.group === group).map((agent) => agent.host)} picked={picked} onChange={setPicked} label={`Select every ${group} agent`} /></td>
                      <td colSpan={7}>{group}</td>
                    </tr>,
                    ...sorter.rows.filter((agent) => agent.group === group).map((agent) => (
                      <tr key={agent.host} data-selected={open === agent.host} onClick={() => setOpen(agent.host)}>
                        <td className="pick" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`Select ${agent.label}`} checked={picked.has(agent.host)} onChange={() => toggle(agent.host)} /></td>
                        <td><div className="who"><StatusDot status={agent.status} /><button type="button">{agent.label}</button>{updating.has(agent.host) && <span className="updating" role="status"><i className="spin" aria-hidden />Updating…</span>}</div></td>
                        <td className="muted">{ownerName(agent)}</td>
                        <td className="muted">{agent.status === 'attention' ? 'Needs attention' : agent.last_seen ? fmtRelative(agent.last_seen, now) : 'No activity yet'}</td>
                        <td className="r num">{count(messages(agent))}</td>
                        <td className="r num">{count(agent.tasks)}</td>
                        <td className="r num"><UsageFigure view={usage(agent)} compact /></td>
                        <td className="r"><Trend values={agent.cells} /></td>
                      </tr>
                    )),
                  ])}
                  {before && (
                    <tr className="unattributed" title="Plan use this week that 9router can’t put on any agent: accounts used through the old login pool before they came to 9router.">
                      <td className="pick" /><td colSpan={5}>{before.label}</td><td className="r num">{before.value}</td><td />
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : <p className="empty">No agents match. Try a different name or filter.</p>}
        </>
      )}
      {current && <AgentSheet key={current.host} agent={current} plan={plan} periodLabel={PERIOD[range]} onClose={() => setOpen(null)} />}
    </div>
  );
}
