import { useRef, useState } from 'react';
import type { Fleet, FleetAgent } from '../api/types';
import { UsageFigure } from '../components/UsageFigure';
import { fmtRelative } from '../lib/format';
import { accountLines, unattributedLine, usageLabel, usageView } from '../lib/usage';
import { SortHeader, useSort } from '../lib/useSort';
import { AgentSheet, useOpenAgent } from './AgentSheet';
import { RangePicker, StatusDot, changeText, count, messages, money, useFleet } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import { HealthWarnings } from './Watch';
import './fleet.css';

const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };

const plural = (n: number, one: string, many = `${one}s`) => `${count(n)} ${n === 1 ? one : many}`;

export function verdict(totals: Fleet['totals']) {
  const first = totals.working ? `${plural(totals.working, 'agent')} working.` : 'No agents working.';
  const second = totals.attention
    ? `${count(totals.attention)} ${totals.attention === 1 ? 'needs' : 'need'} attention.`
    : 'Nothing needs attention.';
  return [first, second];
}

function cellTime(fleet: Fleet, index: number) {
  const date = new Date((fleet.window.start + index * fleet.window.bucket_s) * 1000);
  const day = date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const time = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (fleet.window.bucket_s === 3600) return time;
  return fleet.window.bucket_s === 86400 ? day : `${day}, ${time}`;
}

function axis(fleet: Fleet) {
  const marks = fleet.window.bucket_s === 21600 ? 7 : 5;
  const labels: string[] = [];
  for (let mark = 0; mark < marks - 1; mark += 1) {
    const index = Math.round((mark * fleet.window.count) / (marks - 1));
    const label = cellTime(fleet, index);
    labels.push(fleet.window.bucket_s === 21600 ? label.split(',')[0] : label);
  }
  return [...labels, 'Now'];
}

function level(value: number, peak: number) {
  return value < 1 ? 0 : Math.max(1, Math.min(4, Math.ceil(Math.sqrt(value / peak) * 4)));
}

/** The Overview's usage tile: plan use this week when 9router has plan data, otherwise value at API prices. */
export function UsageTile({ fleet, now }: { fleet: Fleet; now: number }) {
  const { plan, totals } = fleet;
  const view = usageView({ plan, planPct: plan?.used_pct, apiPaid: plan?.api_cost_week, value: totals.value_usd, unpriced: totals.unpriced_tokens });
  const atApi = `${money(totals.value_usd, totals.unpriced_tokens)} at API prices`;
  const detail = view.mode === 'plan' ? `of this week’s limits · ${atApi}`
    : view.mode === 'mixed' ? `of this week’s limits · ${money(plan?.api_cost_week ?? 0)} paid via API · ${atApi}`
    : view.mode === 'api' ? `paid via API this week · ${atApi}`
    : 'at API prices';
  const before = unattributedLine(plan);
  const lines = view.mode === 'plan' || view.mode === 'mixed' ? accountLines(plan, now) : [];
  return (
    <div className="stat" title={view.title}>
      <div className="k">{usageLabel(plan, 'Usage value')}</div>
      <div className="v num">{view.main}</div>
      <div className="d num">{detail}</div>
      {before && <div className="d num">{before.label}: {before.value}</div>}
      {lines.length > 0 && <ul className="plan-accounts num" aria-label="Plan accounts">{lines.map((line) => <li key={line} title={line}>{line}</li>)}</ul>}
    </div>
  );
}

function FleetGrid({ fleet, selected, onSelect, animate }: { fleet: Fleet; selected: string | null; onSelect: (host: string) => void; animate: boolean }) {
  const plan = fleet.plan;
  const usage = (agent: FleetAgent) => usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens });
  const sorter = useSort(fleet.agents, {
    columns: { name: (agent) => agent.label, messages: (agent) => messages(agent), usage: (agent) => usage(agent).sort },
    storageKey: 'overview-activity',
  });
  const before = unattributedLine(plan);
  const peak = Math.max(1, ...fleet.agents.flatMap((agent) => agent.cells));
  const totals = Array.from({ length: fleet.window.count }, (_, index) => fleet.agents.reduce((sum, agent) => sum + agent.cells[index], 0));
  const topTotal = Math.max(1, ...totals);
  const [tip, setTip] = useState<{ text: string; left: number; top: number } | null>(null);
  const onOver = (event: React.MouseEvent) => {
    const cell = (event.target as HTMLElement).closest<HTMLElement>('[data-tip]');
    if (!cell) return setTip(null);
    const box = cell.getBoundingClientRect();
    setTip({ text: cell.dataset.tip!, left: Math.max(110, Math.min(innerWidth - 110, box.left + box.width / 2)), top: box.top - 8 });
  };
  const groups = [...new Set(fleet.agents.map((agent) => agent.group))];
  const row = (agent: FleetAgent) => (
    <div className="f-row f-agent" key={agent.host} data-selected={selected === agent.host} onClick={() => onSelect(agent.host)}>
      <div className="f-name">
        <StatusDot status={agent.status} />
        <button type="button">{agent.label}</button>
        {agent.owner && agent.owner !== agent.label && <small>{agent.owner.split(' ')[0]}</small>}
      </div>
      <div className="f-strip" role="img" aria-label={`${agent.label}: ${plural(agent.cells.reduce((sum, value) => sum + value, 0), 'action')}`}>
        {agent.cells.map((value, index) => {
          const missing = index >= agent.cells.length - agent.gap;
          return <span key={index} className={`cell ${missing ? 'cell-gap' : `cell-${level(value, peak)}`}`} style={{ '--i': index } as React.CSSProperties} data-tip={`${agent.label} · ${cellTime(fleet, index)} · ${missing ? 'no data' : plural(value, 'action')}`} />;
        })}
      </div>
      <div className="f-n num">{count(messages(agent))}</div>
      <div className="f-n num muted"><UsageFigure view={usage(agent)} compact /></div>
    </div>
  );
  return (
    <div className={`fleet${animate ? ' fleet-enter' : ''}`} onMouseOver={onOver} onMouseLeave={() => setTip(null)}>
      <div className="f-row f-head"><SortHeader sorter={sorter} col="name">Agent</SortHeader><span /><SortHeader sorter={sorter} col="messages" className="f-n">Messages</SortHeader><SortHeader sorter={sorter} col="usage" className="f-n">{usageLabel(plan)}</SortHeader></div>
      <div className="f-row f-total">
        <div className="f-name">All agents</div>
        <div className="f-strip f-bars" role="img" aria-label={`All agents: ${plural(totals.reduce((sum, value) => sum + value, 0), 'action')}`}>
          {totals.map((value, index) => <i key={index} style={{ height: `${Math.max(4, (value / topTotal) * 100)}%` }} data-tip={`${cellTime(fleet, index)} · ${plural(value, 'action')}`} />)}
        </div>
        <div className="f-n num">{count(fleet.totals.messages)}</div>
        <div className="f-n num"><UsageFigure plan={plan} planPct={plan?.used_pct} apiPaid={plan?.api_cost_week} value={fleet.totals.value_usd} unpriced={fleet.totals.unpriced_tokens} compact /></div>
      </div>
      <div className="f-row f-axis"><span /><div className="num">{axis(fleet).map((label) => <span key={label}>{label}</span>)}</div></div>
      {groups.map((group) => (
        <div key={group}>
          <div className="f-group">{group}</div>
          {sorter.rows.filter((agent) => agent.group === group).map(row)}
        </div>
      ))}
      {before && <div className="f-row f-unattributed" title="Plan use this week that 9router can’t put on any agent: accounts used through the old login pool before they came to 9router."><div className="f-name">{before.label}</div><span /><span /><div className="f-n num">{before.value}</div></div>}
      {tip && <div className="f-tip num" style={{ left: tip.left, top: tip.top }}>{tip.text}</div>}
    </div>
  );
}

export function Overview() {
  const { range, setRange, data, error } = useFleet();
  const [open, setOpen] = useOpenAgent();
  const firstPaint = useRef(true);
  const now = Date.now() / 1000;
  const animate = firstPaint.current && !!data;
  if (data) firstPaint.current = false;
  const today = new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
  const current = data?.agents.find((agent) => agent.host === open);
  const totals = data?.totals;
  const words = totals ? verdict(totals) : null;
  const change = totals ? changeText(totals.messages, totals.previous_messages) : null;
  const [graphs, setGraphs] = useGraphs('overview');
  return (
    <div className="hq-page">
      <header className="page-head page-head-large">
        <div>
          <div className="dateline">
            {today}
            {!!totals?.catching_up && <span className="chip num">Still copying history from {plural(totals.catching_up, 'agent')}</span>}
          </div>
          <h1 className="verdict">{words ? <>{words[0]} <span>{words[1]}</span></> : 'Loading…'}</h1>
        </div>
        <div className="head-tools"><GraphsToggle on={graphs} onChange={setGraphs} /><RangePicker value={range} onChange={setRange} /></div>
      </header>
      {error && !data && <p className="muted">Couldn’t load your agents. Check that Agent HQ is running, then refresh.</p>}
      {data && totals && (
        <>
          <section className="stats" aria-label="Summary">
            <div className="stat"><div className="k">Active in the last hour</div><div className="v num">{count(totals.active)}</div><div className="d">of {plural(totals.agents, 'agent')}</div></div>
            <div className="stat"><div className="k">Messages</div><div className="v num">{count(totals.messages)}</div><div className="d num">{change ? `${change} vs the previous ${PERIOD[range]}` : `in the last ${PERIOD[range]}`}</div>{totals.agent_messages > 0 && <div className="d num">{count(totals.agent_messages)} more between agents</div>}</div>
            <div className="stat"><div className="k">Tasks</div><div className="v num">{count(totals.tasks)}</div><div className="d">in the last {PERIOD[range]}</div></div>
            <UsageTile fleet={data} now={now} />
          </section>
          <HealthWarnings />
          {graphs && data.series && (
            <div className="trends">
              <TrendChart title="Messages over time" win={data.window} series={[
                { key: 'messages', label: 'With people', values: data.series.messages, color: 'var(--text)' },
                { key: 'agent_messages', label: 'Between agents', values: data.series.agent_messages, color: 'var(--muted)', dashed: true },
              ]} />
              <TrendChart title="Work over time" win={data.window} series={[
                { key: 'actions', label: 'Actions', values: data.series.actions, color: 'var(--working)', kind: 'bar' },
                { key: 'errors', label: 'Errors', values: data.series.errors, color: 'var(--error)', right: true },
              ]} />
            </div>
          )}
          {data.agents.filter((agent) => agent.status === 'attention').map((agent) => (
            <button type="button" className="attention" key={agent.host} onClick={() => setOpen(agent.host)}>
              <StatusDot status="attention" />
              <strong>{agent.label}</strong>
              <span className="muted">couldn’t be reached{agent.last_seen ? `. Last active ${fmtRelative(agent.last_seen, now)}` : ''}.</span>
              <span className="go muted">View</span>
            </button>
          ))}
          {data.agents.length ? (
            <section>
              <div className="section-head">
                <div><h2>Activity</h2><p className="section-note">Messages with people and between agents, plus each agent’s own replies and tool use.</p></div>
                <div className="legend" aria-hidden="true"><span>Less</span><i className="cell cell-0" /><i className="cell cell-1" /><i className="cell cell-2" /><i className="cell cell-3" /><i className="cell cell-4" /><span>More</span></div>
              </div>
              <FleetGrid fleet={data} selected={open} onSelect={setOpen} animate={animate} />
            </section>
          ) : <p className="empty">No agents connected yet. Add them to hosts.json to see them here.</p>}
        </>
      )}
      {current && <AgentSheet key={current.host} agent={current} plan={data?.plan} periodLabel={PERIOD[range]} onClose={() => setOpen(null)} />}
    </div>
  );
}
