import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useApi } from '../api/client';
import type { Fleet, FleetAgent, FleetPlan } from '../api/types';
import { UsageFigure } from '../components/UsageFigure';
import { fmtDuration, fmtRelative, fmtTokens } from '../lib/format';
import { PLAN_NOTE, unattributedLine, usageLabel, usageMode, usageView } from '../lib/usage';
import { SortHeader, useSort } from '../lib/useSort';
import type { SortValue } from '../lib/useSort';
import { openAgent } from './AgentSheet';
import { RangePicker, count, messages, percent, useFleet } from './fleet-shared';
import type { Range } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';
import './insights.css';

const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

type Reply = { replies: number; median_s: number | null; p90_s: number | null };
type InsightAgent = { host: string; reply: Reply; limit_messages: number; api_errors: number; tool_errors: number };
export interface InsightData {
  replies: { all: Reply; by_channel: Record<string, Reply> };
  tasks: { count: number; median_s: number | null; p90_s: number | null; outcomes: Record<string, number> };
  reliability: { limit_messages: number; api_errors: number; tool_errors: number; last_limit: Record<string, number> };
  tools: { total: number; top: { tool: string; count: number }[] };
  week: number[][];
  agents: InsightAgent[];
  generated_at: number;
  /** Per cell of the period, for the graphs. Absent from older servers. */
  window?: { start: number; end: number; bucket_s: number; count: number };
  trend?: { replies: number[]; reply_median_s: (number | null)[]; limit_messages: number[]; api_errors: number[]; tool_errors: number[]; tasks: number[] };
}

/** What the detail panel shows. */
type Measure = 'replies' | 'tasks' | 'limit_messages' | 'api_errors' | 'tool_errors';
export type Detail =
  | { kind: 'agent'; host: string }
  | { kind: 'measure'; measure: Measure }
  | { kind: 'channel'; channel: string }
  | { kind: 'tool'; tool: string }
  | { kind: 'group'; by: 'group' | 'owner'; name: string };

/** Short, distinct labels for a time axis: seconds up to 2 minutes, then minutes, then hours. */
const axisDuration = (n: number) => (n < 120 ? `${Math.round(n)} s` : n < 7200 ? `${Math.round(n / 60)} min` : `${(n / 3600).toFixed(1)} h`);
const channelName = (channel: string) => ({ telegram: 'Telegram', whatsapp: 'WhatsApp', other: 'Other' } as Record<string, string>)[channel] ?? channel;
// Tool names from MCP servers are long; the last part is the readable one.
const toolName = (tool: string) => (tool.startsWith('mcp__') ? tool.split('__').slice(1).join(' · ') : tool);
const ownerOf = (agent: FleetAgent) => agent.owner ?? (agent.group === 'Department' ? 'Departments' : 'No owner listed');
const MEASURE: Record<Measure, { title: string; what: string }> = {
  replies: { title: 'Replies to people', what: 'How quickly agents answer people, worked out from message times.' },
  tasks: { title: 'Tasks', what: 'A task runs from a person’s message to the agent’s last action before the next one.' },
  limit_messages: { title: 'Limit messages', what: 'Replies saying a usage or rate limit was hit.' },
  api_errors: { title: 'API errors', what: 'Failed or overloaded model calls.' },
  tool_errors: { title: 'Failed tool calls', what: 'Tool calls that ended in an error.' },
};

/** Totals for each group or owner, from the same period as the rest of the page. */
export function rollUp(agents: FleetAgent[], key: (agent: FleetAgent) => string | null) {
  const out = new Map<string, { name: string; agents: number; value: number; messages: number; between: number; tasks: number; plan: number | null; paid: number }>();
  for (const agent of agents) {
    const name = key(agent);
    if (!name) continue;
    const row = out.get(name) ?? { name, agents: 0, value: 0, messages: 0, between: 0, tasks: 0, plan: null, paid: 0 };
    row.agents += 1;
    row.value += agent.value_usd;
    row.messages += messages(agent);
    row.between += agent.agent_messages;
    row.tasks += agent.tasks;
    if (agent.plan_pct != null) row.plan = (row.plan ?? 0) + agent.plan_pct;
    row.paid += agent.api_paid ?? 0;
    out.set(name, row);
  }
  return [...out.values()].sort((a, b) => b.value - a.value || b.messages - a.messages);
}

function Heat({ week }: { week: number[][] }) {
  const peak = Math.max(1, ...week.flat());
  let best = { day: 0, hour: 0, n: -1 };
  week.forEach((hours, day) => hours.forEach((n, hour) => { if (n > best.n) best = { day, hour, n }; }));
  return (
    <div className="heat" role="img" aria-label={best.n > 0 ? `Busiest hour: ${DAYS[best.day]} ${best.hour}:00` : 'No activity'}>
      <div className="heat-hours" aria-hidden="true"><span />{[0, 6, 12, 18].map((hour) => <span key={hour} style={{ gridColumn: hour + 2 }}>{String(hour).padStart(2, '0')}</span>)}</div>
      {week.map((hours, day) => (
        <div className="heat-row" key={DAYS[day]}>
          <span className="heat-day">{DAYS[day]}</span>
          {hours.map((n, hour) => <i key={hour} title={`${DAYS[day]} ${String(hour).padStart(2, '0')}:00 · ${count(n)} actions`} style={{ opacity: n ? .12 + .88 * Math.sqrt(n / peak) : .05 }} />)}
        </div>
      ))}
      {best.n > 0 && <p className="note">Busiest: {DAYS[best.day]} around {String(best.hour).padStart(2, '0')}:00, your time.</p>}
    </div>
  );
}

export type Column<T> = { key: string; label: ReactNode; value: (row: T) => SortValue; render?: (row: T) => ReactNode; className?: string };

/** A sortable mini table. Rows open something when onOpen is given. */
export function SortTable<T>({ label, rows, columns, rowKey, storageKey, onOpen, openLabel, footer, empty = 'Nothing in this period.' }: {
  label: string; rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; storageKey: string;
  onOpen?: (row: T) => void; openLabel?: (row: T) => string; footer?: ReactNode; empty?: string;
}) {
  const sorter = useSort(rows, { columns: Object.fromEntries(columns.map((c) => [c.key, c.value])), storageKey });
  const template = { gridTemplateColumns: `minmax(0, 1.6fr) repeat(${columns.length - 1}, minmax(0, 1fr))` };
  return (
    <div className="mini-table" role="table" aria-label={label}>
      <div className="mini-row mini-headrow" role="row" style={template}>
        {columns.map((c) => <SortHeader key={c.key} sorter={sorter} col={c.key}>{c.label}</SortHeader>)}
      </div>
      {sorter.rows.length ? sorter.rows.map((row) => (
        <div className="mini-row" role="row" key={rowKey(row)} style={template} data-open={onOpen ? true : undefined}>
          {columns.map((c, index) => (
            <span role="cell" key={c.key} className={c.className ?? (index ? 'num' : undefined)}>
              {index === 0 && onOpen
                ? <button type="button" className="row-open" aria-label={openLabel?.(row)} onClick={() => onOpen(row)}>{c.render ? c.render(row) : String(c.value(row) ?? '—')}</button>
                : c.render ? c.render(row) : String(c.value(row) ?? '—')}
            </span>
          ))}
        </div>
      )) : <div className="mini-row" role="row"><span role="cell" className="muted">{empty}</span></div>}
      {footer}
    </div>
  );
}

function GroupTable({ title, by, rows, plan, onOpen }: { title: string; by: 'group' | 'owner'; rows: ReturnType<typeof rollUp>; plan?: FleetPlan; onOpen: (detail: Detail) => void }) {
  const view = (row: ReturnType<typeof rollUp>[number]) => usageView({ plan, planPct: row.plan, apiPaid: row.paid, value: row.value });
  const whole = rows.reduce((sum, row) => sum + (view(row).sort ?? 0), 0);
  const before = unattributedLine(plan);
  return (
    <section className="side-section">
      <div className="section-head"><h2>{title}</h2></div>
      <SortTable label={title} rows={rows} rowKey={(row) => row.name} storageKey={`insights-${by}`}
        onOpen={(row) => onOpen({ kind: 'group', by, name: row.name })} openLabel={(row) => `${row.name} details`}
        columns={[
          { key: 'name', label: 'Name', value: (row) => row.name },
          { key: 'agents', label: 'Agents', value: (row) => row.agents },
          { key: 'messages', label: 'Messages', value: (row) => row.messages, render: (row) => count(row.messages) },
          { key: 'usage', label: usageLabel(plan), value: (row) => view(row).sort, render: (row) => <UsageFigure view={view(row)} compact /> },
          { key: 'share', label: 'Share', value: (row) => (whole ? (view(row).sort ?? 0) / whole : null), render: (row) => <span className="muted">{percent(view(row).sort ?? 0, whole)}</span> },
        ]}
        footer={before && <div className="mini-row muted" role="row" title="Plan use this week that 9router can’t put on any agent: accounts used through the old login pool before they came to 9router."><span role="cell">{before.label}</span><span role="cell" /><span role="cell" /><span role="cell" className="num">{before.value}</span><span role="cell" /></div>} />
    </section>
  );
}

/** The side panel with everything the API returns for one item on the page. Esc or the close button shuts it. */
function DetailPanel({ detail, data, fleet, range, onOpen, onClose }: { detail: Detail; data: InsightData; fleet: Fleet | null; range: Range; onOpen: (detail: Detail) => void; onClose: () => void }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);
  const plan = fleet?.plan;
  const agentOf = (host: string) => fleet?.agents.find((agent) => agent.host === host);
  const name = (host: string) => agentOf(host)?.label ?? host;
  const now = Date.now() / 1000;
  const win = data.window && data.window.count ? data.window : null;
  const period = `last ${PERIOD[range]}`;
  const openAgentRow = (row: { host: string }) => onOpen({ kind: 'agent', host: row.host });
  const agentLabel = (row: { host: string }) => `${name(row.host)} details`;
  let title = '';
  let body: ReactNode = null;

  if (detail.kind === 'agent') {
    const found = data.agents.find((agent) => agent.host === detail.host);
    const agent = agentOf(detail.host);
    const usage = agent ? usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens }) : null;
    const last = data.reliability.last_limit[detail.host];
    title = name(detail.host);
    body = (
      <>
        <div className="muted">{agent ? `${agent.owner ? `${agent.owner} · ` : ''}${agent.group}` : detail.host} · {period}</div>
        <div className="sheet-pairs">
          <div><b className="num">{fmtDuration(found?.reply.median_s ?? null)}</b><small>Typical reply, {count(found?.reply.replies ?? 0)} replies</small></div>
          <div><b className="num">{fmtDuration(found?.reply.p90_s ?? null)}</b><small>Slowest 10% took longer</small></div>
          <div><b className="num">{count(found?.limit_messages ?? 0)}</b><small>Limit messages{last ? `, last ${fmtRelative(last, now)}` : ''}</small></div>
          <div><b className="num">{count(found?.api_errors ?? 0)}</b><small>API errors</small></div>
          <div><b className="num">{count(found?.tool_errors ?? 0)}</b><small>Failed tool calls</small></div>
          {agent && <div><b className="num">{count(messages(agent))}</b><small>Messages with people</small></div>}
          {agent && <div><b className="num">{count(agent.tasks)}</b><small>Tasks</small></div>}
          {agent && <div><b className="num">{fmtTokens(agent.tokens)}</b><small>Tokens</small></div>}
          {agent && usage && <div title={usage.title}><b className="num">{usage.main}</b><small>{usage.mode === 'plan' || usage.mode === 'mixed' ? 'Of the plans’ weekly limits, this week' : usage.mode === 'api' ? 'Paid via API, this week' : 'Usage value'}</small>{usage.secondary && usage.mode !== 'api' && <small className="num sheet-sub">{usage.secondary}</small>}</div>}
        </div>
        {agent ? <button type="button" className="plain-button insight-open" onClick={() => openAgent(detail.host)}>Open agent</button> : <p className="muted">This agent isn’t in HQ’s agent list.</p>}
      </>
    );
  } else if (detail.kind === 'measure') {
    const m = detail.measure;
    title = MEASURE[m].title;
    if (m === 'replies') {
      const all = data.replies.all;
      body = (
        <>
          <p className="muted">{MEASURE[m].what} {period}.</p>
          <div className="sheet-pairs">
            <div><b className="num">{fmtDuration(all.median_s)}</b><small>Typical reply</small></div>
            <div><b className="num">{fmtDuration(all.p90_s)}</b><small>Slowest 10% took longer</small></div>
            <div><b className="num">{count(all.replies)}</b><small>Replies</small></div>
          </div>
          {win && data.trend && <TrendChart title="Reply speed over time" height={140} win={win} format={axisDuration} formatRight={(n) => n.toLocaleString('en-US')} series={[
            { key: 'replies', label: 'Replies', values: data.trend.replies, color: 'var(--line-strong)', kind: 'bar', right: true },
            { key: 'reply_median_s', label: 'Typical reply', values: data.trend.reply_median_s, color: 'var(--text)' },
          ]} />}
          <h3>By channel</h3>
          <SortTable label="Replies by channel" storageKey="insights-detail-channels" rowKey={(row) => row[0]} rows={Object.entries(data.replies.by_channel)}
            onOpen={(row) => onOpen({ kind: 'channel', channel: row[0] })} openLabel={(row) => `${channelName(row[0])} details`}
            columns={[
              { key: 'channel', label: 'Channel', value: (row) => channelName(row[0]) },
              { key: 'replies', label: 'Replies', value: (row) => row[1].replies, render: (row) => count(row[1].replies) },
              { key: 'median', label: 'Typical', value: (row) => row[1].median_s, render: (row) => fmtDuration(row[1].median_s) },
              { key: 'p90', label: 'Slowest 10%', value: (row) => row[1].p90_s, render: (row) => fmtDuration(row[1].p90_s) },
            ]} />
          <h3>By agent</h3>
          <SortTable label="Replies by agent" storageKey="insights-detail-reply-agents" rowKey={(row) => row.host} rows={data.agents.filter((agent) => agent.reply.replies > 0)}
            onOpen={openAgentRow} openLabel={agentLabel}
            columns={[
              { key: 'agent', label: 'Agent', value: (row) => name(row.host) },
              { key: 'replies', label: 'Replies', value: (row) => row.reply.replies, render: (row) => count(row.reply.replies) },
              { key: 'median', label: 'Typical', value: (row) => row.reply.median_s, render: (row) => fmtDuration(row.reply.median_s) },
              { key: 'p90', label: 'Slowest 10%', value: (row) => row.reply.p90_s, render: (row) => fmtDuration(row.reply.p90_s) },
            ]} />
        </>
      );
    } else if (m === 'tasks') {
      const outcomes = Object.entries(data.tasks.outcomes);
      body = (
        <>
          <p className="muted">{MEASURE[m].what} {period}.</p>
          <div className="sheet-pairs">
            <div><b className="num">{count(data.tasks.count)}</b><small>Tasks</small></div>
            <div><b className="num">{fmtDuration(data.tasks.median_s)}</b><small>Typical task</small></div>
            <div><b className="num">{fmtDuration(data.tasks.p90_s)}</b><small>Longest 10% took over</small></div>
          </div>
          {win && data.trend && <TrendChart title="Tasks over time" height={140} win={win} series={[{ key: 'tasks', label: 'Tasks', values: data.trend.tasks, color: 'var(--working)', kind: 'bar' }]} />}
          <h3>Outcomes</h3>
          <SortTable label="Task outcomes" storageKey="insights-detail-outcomes" rowKey={(row) => row[0]} rows={outcomes}
            columns={[
              { key: 'outcome', label: 'Outcome', value: (row) => row[0] },
              { key: 'count', label: 'Tasks', value: (row) => row[1], render: (row) => count(row[1]) },
              { key: 'share', label: 'Share', value: (row) => row[1], render: (row) => percent(row[1], data.tasks.count) },
            ]} />
          <p className="note">HQ doesn’t split tasks by agent here yet.</p>
        </>
      );
    } else {
      const total = data.reliability[m];
      const rows = data.agents.filter((agent) => agent[m] > 0);
      const last = data.reliability.last_limit;
      body = (
        <>
          <p className="muted">{MEASURE[m].what} {period}.</p>
          <div className="sheet-pairs">
            <div><b className="num">{count(total)}</b><small>In all</small></div>
            <div><b className="num">{count(rows.length)}</b><small>{rows.length === 1 ? 'Agent' : 'Agents'} with any</small></div>
            {m === 'tool_errors' && <div><b className="num">{percent(total, data.tools.total, 1)}</b><small>of {count(data.tools.total)} tool calls</small></div>}
          </div>
          {win && data.trend && <TrendChart title={`${MEASURE[m].title} over time`} height={140} win={win} series={[{ key: m, label: MEASURE[m].title, values: data.trend[m], color: m === 'limit_messages' ? 'var(--waiting)' : m === 'api_errors' ? 'var(--error)' : 'var(--muted)', kind: 'bar' }]} />}
          <h3>By agent</h3>
          <SortTable label={`${MEASURE[m].title} by agent`} storageKey={`insights-detail-${m}`} rowKey={(row) => row.host} rows={rows} empty="None in this period."
            onOpen={openAgentRow} openLabel={agentLabel}
            columns={[
              { key: 'agent', label: 'Agent', value: (row) => name(row.host) },
              { key: 'count', label: 'Count', value: (row) => row[m], render: (row) => count(row[m]) },
              { key: 'share', label: 'Share', value: (row) => row[m], render: (row) => percent(row[m], total) },
              ...(m === 'limit_messages' ? [{ key: 'last', label: 'Last', value: (row: InsightAgent) => last[row.host] ?? null, render: (row: InsightAgent) => (last[row.host] ? fmtRelative(last[row.host], now) : '—') }] : []),
            ]} />
        </>
      );
    }
  } else if (detail.kind === 'channel') {
    const reply = data.replies.by_channel[detail.channel];
    title = channelName(detail.channel);
    body = (
      <>
        <p className="muted">Replies to people on {channelName(detail.channel)}, {period}.</p>
        <div className="sheet-pairs">
          <div><b className="num">{fmtDuration(reply?.median_s ?? null)}</b><small>Typical reply</small></div>
          <div><b className="num">{fmtDuration(reply?.p90_s ?? null)}</b><small>Slowest 10% took longer</small></div>
          <div><b className="num">{count(reply?.replies ?? 0)}</b><small>Replies</small></div>
          <div><b className="num">{percent(reply?.replies ?? 0, data.replies.all.replies)}</b><small>Of all replies</small></div>
        </div>
        <p className="note">HQ doesn’t split a channel’s replies by agent yet.</p>
      </>
    );
  } else if (detail.kind === 'tool') {
    const index = data.tools.top.findIndex((tool) => tool.tool === detail.tool);
    const tool = data.tools.top[index];
    title = toolName(detail.tool);
    body = (
      <>
        <p className="muted"><code>{detail.tool}</code> · {period}</p>
        <div className="sheet-pairs">
          <div><b className="num">{count(tool?.count ?? 0)}</b><small>Calls</small></div>
          <div><b className="num">{percent(tool?.count ?? 0, data.tools.total, 1)}</b><small>Of {count(data.tools.total)} tool calls</small></div>
          <div><b className="num">{index >= 0 ? `#${index + 1}` : '—'}</b><small>Of the {data.tools.top.length} most used</small></div>
        </div>
        <p className="note">HQ only has this tool’s total for the period. It doesn’t yet split tool calls by agent or over time, or count failures per tool.</p>
      </>
    );
  } else {
    const agents = (fleet?.agents ?? []).filter((agent) => (detail.by === 'group' ? agent.group : ownerOf(agent)) === detail.name);
    const extra = new Map(data.agents.map((agent) => [agent.host, agent]));
    const usage = (agent: FleetAgent) => usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens });
    title = detail.name;
    body = (
      <>
        <p className="muted">{agents.length} {agents.length === 1 ? 'agent' : 'agents'} · {detail.by === 'group' ? 'group' : 'owner'} · {period}</p>
        <SortTable label={`${detail.name} agents`} storageKey={`insights-detail-${detail.by}`} rowKey={(row) => row.host} rows={agents}
          onOpen={openAgentRow} openLabel={agentLabel}
          columns={[
            { key: 'agent', label: 'Agent', value: (row) => row.label },
            { key: 'messages', label: 'Messages', value: (row) => messages(row), render: (row) => count(messages(row)) },
            { key: 'tasks', label: 'Tasks', value: (row) => row.tasks, render: (row) => count(row.tasks) },
            { key: 'reply', label: 'Typical reply', value: (row) => extra.get(row.host)?.reply.median_s ?? null, render: (row) => fmtDuration(extra.get(row.host)?.reply.median_s ?? null) },
            { key: 'problems', label: 'Problems', value: (row) => { const e = extra.get(row.host); return e ? e.limit_messages + e.api_errors + e.tool_errors : 0; }, render: (row) => { const e = extra.get(row.host); return count(e ? e.limit_messages + e.api_errors + e.tool_errors : 0); } },
            { key: 'usage', label: usageLabel(plan), value: (row) => usage(row).sort, render: (row) => <UsageFigure view={usage(row)} compact /> },
          ]} />
        <p className="note">Problems are limit messages, API errors and failed tool calls together. {usageMode(plan) === 'plan' || usageMode(plan) === 'mixed' ? PLAN_NOTE : ''}</p>
      </>
    );
  }

  return (
    <aside className="agent-sheet insight-sheet" role="dialog" aria-label={`${title} details`}>
      <div className="sheet-head">
        <h2>{title}</h2>
        <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>×</button>
      </div>
      {body}
    </aside>
  );
}

export function Insights() {
  const { range, setRange, data: fleet } = useFleet();
  const { data, error } = useApi<InsightData>(`/api/insights?range=${range}`, { live: false });
  return <InsightsView range={range} setRange={setRange} fleet={fleet} data={data} failed={!!error && !data} />;
}

/** A stat tile that opens its detail. */
function StatButton({ label, children, onClick }: { label: string; children: ReactNode; onClick: () => void }) {
  return <button type="button" className="stat stat-button" aria-label={`${label}: details`} onClick={onClick}>{children}</button>;
}

export function InsightsView({ range, setRange, fleet, data, failed }: { range: Range; setRange: (range: Range) => void; fleet: Fleet | null; data: InsightData | null; failed: boolean }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const labels = new Map((fleet?.agents ?? []).map((agent) => [agent.host, agent.label]));
  const name = (host: string) => labels.get(host) ?? host;
  const now = Date.now() / 1000;
  const slowest = [...(data?.agents ?? [])].filter((agent) => agent.reply.replies >= 3).sort((a, b) => (b.reply.median_s ?? 0) - (a.reply.median_s ?? 0)).slice(0, 8);
  const troubled = [...(data?.agents ?? [])].filter((agent) => agent.limit_messages + agent.api_errors + agent.tool_errors > 0)
    .sort((a, b) => b.limit_messages - a.limit_messages || b.api_errors + b.tool_errors - (a.api_errors + a.tool_errors)).slice(0, 8);
  const topTool = Math.max(1, ...(data?.tools.top ?? []).map((tool) => tool.count));
  const plan = fleet?.plan;
  const planned = usageMode(plan) === 'plan' || usageMode(plan) === 'mixed';
  const [graphs, setGraphs] = useGraphs('insights');
  const win = data?.window && data.window.count ? data.window : null;
  const measure = (m: Measure) => () => setDetail({ kind: 'measure', measure: m });
  const agentUsage = (host: string) => {
    const agent = fleet?.agents.find((a) => a.host === host);
    return agent ? usageView({ plan, planPct: agent.plan_pct, apiPaid: agent.api_paid, value: agent.value_usd, unpriced: agent.unpriced_tokens }) : null;
  };
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>Insights</h1>
          <p className="lede">How quickly agents answer people, how reliably they work, and where the work goes. Last {PERIOD[range]}. Click anything for the details.</p>
        </div>
        <div className="head-tools"><GraphsToggle on={graphs} onChange={setGraphs} /><RangePicker value={range} onChange={setRange} /></div>
      </header>
      {failed && <p className="muted">Couldn’t load insights. Check that Agent HQ is running, then refresh.</p>}
      {data && (
        <>
          <section className="stats stats-grid" aria-label="Insight measures">
            <StatButton label="Typical reply to people" onClick={measure('replies')}><div className="k">Typical reply to people</div><div className="v num">{fmtDuration(data.replies.all.median_s)}</div><div className="d num">median of {count(data.replies.all.replies)} replies</div></StatButton>
            <StatButton label="Slowest replies" onClick={measure('replies')}><div className="k">Slowest replies</div><div className="v num">{fmtDuration(data.replies.all.p90_s)}</div><div className="d">1 in 10 replies took longer</div></StatButton>
            <StatButton label="Typical task" onClick={measure('tasks')}><div className="k">Typical task</div><div className="v num">{fmtDuration(data.tasks.median_s)}</div><div className="d num">{count(data.tasks.count)} tasks, longest 10% over {fmtDuration(data.tasks.p90_s)}</div></StatButton>
            <StatButton label="Limit messages" onClick={measure('limit_messages')}><div className="k">Limit messages</div><div className="v num">{count(data.reliability.limit_messages)}</div><div className="d">replies saying a usage or rate limit was hit</div></StatButton>
            <StatButton label="API errors" onClick={measure('api_errors')}><div className="k">API errors</div><div className="v num">{count(data.reliability.api_errors)}</div><div className="d">failed or overloaded model calls</div></StatButton>
            <StatButton label="Failed tool calls" onClick={measure('tool_errors')}><div className="k">Failed tool calls</div><div className="v num">{count(data.reliability.tool_errors)}</div><div className="d num">{percent(data.reliability.tool_errors, data.tools.total, 1)} of {count(data.tools.total)} tool calls</div></StatButton>
          </section>
          {graphs && win && data.trend && (
            <div className="trends">
              <TrendChart title="Reply speed over time" win={win} format={axisDuration} formatRight={(n) => n.toLocaleString('en-US')} series={[
                { key: 'replies', label: 'Replies', values: data.trend.replies, color: 'var(--line-strong)', kind: 'bar', right: true },
                { key: 'reply_median_s', label: 'Typical reply', values: data.trend.reply_median_s, color: 'var(--text)' },
              ]} note="Gaps in the line are periods with no replies to people." />
              <TrendChart title="Problems over time" win={win} series={[
                { key: 'limit_messages', label: 'Limit messages', values: data.trend.limit_messages, color: 'var(--waiting)', kind: 'bar', stack: 'p' },
                { key: 'api_errors', label: 'API errors', values: data.trend.api_errors, color: 'var(--error)', kind: 'bar', stack: 'p' },
                { key: 'tool_errors', label: 'Failed tools', values: data.trend.tool_errors, color: 'var(--muted)', kind: 'bar', stack: 'p' },
                { key: 'tasks', label: 'Tasks', values: data.trend.tasks, color: 'var(--working)', right: true },
              ]} />
            </div>
          )}
          <div className="columns">
            <section>
              <div className="section-head"><h2>When agents are busy</h2></div>
              <Heat week={data.week} />
              <div className="section-head insights-gap"><h2>Slowest to reply</h2></div>
              {slowest.length ? slowest.map((agent) => (
                <button type="button" className="list-row list-open" key={agent.host} onClick={() => setDetail({ kind: 'agent', host: agent.host })}>
                  <span className="list-name">{name(agent.host)}</span>
                  <span className="num muted">{fmtDuration(agent.reply.median_s)} typical · {count(agent.reply.replies)} replies</span>
                </button>
              )) : <p className="muted">Not enough replies in this period.</p>}
              <p className="note">Worked out from message times: a reply is paired with the oldest message still waiting on the same agent and channel. Messages in group chats often need no reply, so unanswered messages aren’t counted against anyone.</p>
              <div className="section-head insights-gap"><h2>Needs a look</h2></div>
              {troubled.length ? troubled.map((agent) => (
                <button type="button" className="list-row list-open" key={agent.host} onClick={() => setDetail({ kind: 'agent', host: agent.host })}>
                  <span className="list-name">{name(agent.host)}</span>
                  <span className="num muted">
                    {[agent.limit_messages && `${count(agent.limit_messages)} limit`, agent.api_errors && `${count(agent.api_errors)} API errors`, agent.tool_errors && `${count(agent.tool_errors)} failed tools`].filter(Boolean).join(' · ')}
                    {data.reliability.last_limit[agent.host] ? ` · last limit ${fmtRelative(data.reliability.last_limit[agent.host], now)}` : ''}
                  </span>
                </button>
              )) : <p className="muted">No limits or errors in this period.</p>}
            </section>
            <section>
              <div className="section-head"><h2>Reply speed by channel</h2></div>
              {Object.keys(data.replies.by_channel).length ? Object.entries(data.replies.by_channel).map(([channel, reply]) => (
                <button type="button" className="list-row list-open" key={channel} onClick={() => setDetail({ kind: 'channel', channel })}>
                  <span className="list-name">{channelName(channel)}</span>
                  <span className="num muted">{fmtDuration(reply.median_s)} typical · {count(reply.replies)}</span>
                </button>
              )) : <p className="muted">No replies to people in this period.</p>}
              <div className="section-head insights-gap"><h2>Most used tools</h2></div>
              {data.tools.top.length ? data.tools.top.map((tool) => (
                <button type="button" className="bar-row bar-open" key={tool.tool} title={tool.tool} onClick={() => setDetail({ kind: 'tool', tool: tool.tool })}>
                  <span className="bar-name">{toolName(tool.tool)}</span>
                  <span className="bar"><i style={{ width: `${(tool.count / topTool) * 100}%` }} /></span>
                  <span className="num bar-value">{percent(tool.count, data.tools.total)}</span>
                </button>
              )) : <p className="muted">No tool calls in this period.</p>}
              {fleet && <GroupTable title="By group" by="group" rows={rollUp(fleet.agents, (agent) => agent.group)} plan={plan} onOpen={setDetail} />}
              {fleet && <GroupTable title="By owner" by="owner" rows={rollUp(fleet.agents, ownerOf)} plan={plan} onOpen={setDetail} />}
              <p className="note">{planned ? 'Plan use is the share of the Claude plans’ weekly limits this week, whatever period is picked. Dollar figures are at API prices, not your bill.' : 'Value is what the tokens would cost at API prices, not your bill.'}</p>
            </section>
          </div>
          {data.agents.length > 0 && (
            <section className="insights-gap" aria-label="Every agent">
              <div className="section-head"><h2>Every agent</h2></div>
              <SortTable label="Every agent" storageKey="insights-agents" rowKey={(row) => row.host} rows={data.agents}
                onOpen={(row) => setDetail({ kind: 'agent', host: row.host })} openLabel={(row) => `${name(row.host)} details`}
                columns={[
                  { key: 'agent', label: 'Agent', value: (row) => name(row.host) },
                  { key: 'replies', label: 'Replies', value: (row) => row.reply.replies, render: (row) => count(row.reply.replies) },
                  { key: 'median', label: 'Typical reply', value: (row) => row.reply.median_s, render: (row) => fmtDuration(row.reply.median_s) },
                  { key: 'p90', label: 'Slowest 10%', value: (row) => row.reply.p90_s, render: (row) => fmtDuration(row.reply.p90_s) },
                  { key: 'limits', label: 'Limit msgs', value: (row) => row.limit_messages, render: (row) => count(row.limit_messages) },
                  { key: 'api', label: 'API errors', value: (row) => row.api_errors, render: (row) => count(row.api_errors) },
                  { key: 'tools', label: 'Failed tools', value: (row) => row.tool_errors, render: (row) => count(row.tool_errors) },
                  ...(fleet ? [{ key: 'usage', label: usageLabel(plan), value: (row: InsightAgent) => agentUsage(row.host)?.sort ?? null, render: (row: InsightAgent) => { const v = agentUsage(row.host); return v ? <UsageFigure view={v} compact /> : '—'; } }] : []),
                ]} />
              <p className="note">Agents with replies or problems in this period.</p>
            </section>
          )}
          {detail && <DetailPanel key={JSON.stringify(detail)} detail={detail} data={data} fleet={fleet} range={range} onOpen={setDetail} onClose={() => setDetail(null)} />}
        </>
      )}
    </div>
  );
}
