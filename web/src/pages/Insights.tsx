import { useApi } from '../api/client';
import type { Fleet, FleetAgent } from '../api/types';
import { fmtDuration, fmtRelative } from '../lib/format';
import { RangePicker, count, messages, money, percent, useFleet } from './fleet-shared';
import type { Range } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';
import './insights.css';

const PERIOD = { today: '24 hours', week: '7 days', month: '30 days' };
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

type Reply = { replies: number; median_s: number | null; p90_s: number | null };
export interface InsightData {
  replies: { all: Reply; by_channel: Record<string, Reply> };
  tasks: { count: number; median_s: number | null; p90_s: number | null; outcomes: Record<string, number> };
  reliability: { limit_messages: number; api_errors: number; tool_errors: number; last_limit: Record<string, number> };
  tools: { total: number; top: { tool: string; count: number }[] };
  week: number[][];
  agents: { host: string; reply: Reply; limit_messages: number; api_errors: number; tool_errors: number }[];
  generated_at: number;
  /** Per cell of the period, for the graphs. Absent from older servers. */
  window?: { start: number; end: number; bucket_s: number; count: number };
  trend?: { replies: number[]; reply_median_s: (number | null)[]; limit_messages: number[]; api_errors: number[]; tool_errors: number[]; tasks: number[] };
}

/** Short, distinct labels for a time axis: seconds up to 2 minutes, then minutes, then hours. */
const axisDuration = (n: number) => (n < 120 ? `${Math.round(n)} s` : n < 7200 ? `${Math.round(n / 60)} min` : `${(n / 3600).toFixed(1)} h`);
const channelName = (channel: string) => ({ telegram: 'Telegram', whatsapp: 'WhatsApp', other: 'Other' } as Record<string, string>)[channel] ?? channel;
// Tool names from MCP servers are long; the last part is the readable one.
const toolName = (tool: string) => (tool.startsWith('mcp__') ? tool.split('__').slice(1).join(' · ') : tool);

/** Totals for each group or owner, from the same period as the rest of the page. */
export function rollUp(agents: FleetAgent[], key: (agent: FleetAgent) => string | null) {
  const out = new Map<string, { name: string; agents: number; value: number; messages: number; between: number; tasks: number }>();
  for (const agent of agents) {
    const name = key(agent);
    if (!name) continue;
    const row = out.get(name) ?? { name, agents: 0, value: 0, messages: 0, between: 0, tasks: 0 };
    row.agents += 1;
    row.value += agent.value_usd;
    row.messages += messages(agent);
    row.between += agent.agent_messages;
    row.tasks += agent.tasks;
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

function GroupTable({ title, rows, total }: { title: string; rows: ReturnType<typeof rollUp>; total: number }) {
  return (
    <section className="side-section">
      <div className="section-head"><h2>{title}</h2></div>
      <div className="mini-table" role="table" aria-label={title}>
        <div className="mini-row mini-headrow" role="row"><span role="columnheader">Name</span><span role="columnheader">Agents</span><span role="columnheader">Messages</span><span role="columnheader">Value</span><span role="columnheader">Share</span></div>
        {rows.map((row) => (
          <div className="mini-row" role="row" key={row.name}>
            <span role="cell">{row.name}</span>
            <span role="cell" className="num">{row.agents}</span>
            <span role="cell" className="num">{count(row.messages)}</span>
            <span role="cell" className="num">{money(row.value)}</span>
            <span role="cell" className="num muted">{percent(row.value, total)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function Insights() {
  const { range, setRange, data: fleet } = useFleet();
  const { data, error } = useApi<InsightData>(`/api/insights?range=${range}`, { live: false });
  return <InsightsView range={range} setRange={setRange} fleet={fleet} data={data} failed={!!error && !data} />;
}

export function InsightsView({ range, setRange, fleet, data, failed }: { range: Range; setRange: (range: Range) => void; fleet: Fleet | null; data: InsightData | null; failed: boolean }) {
  const labels = new Map((fleet?.agents ?? []).map((agent) => [agent.host, agent.label]));
  const name = (host: string) => labels.get(host) ?? host;
  const now = Date.now() / 1000;
  const slowest = [...(data?.agents ?? [])].filter((agent) => agent.reply.replies >= 3).sort((a, b) => (b.reply.median_s ?? 0) - (a.reply.median_s ?? 0)).slice(0, 8);
  const troubled = [...(data?.agents ?? [])].filter((agent) => agent.limit_messages + agent.api_errors + agent.tool_errors > 0)
    .sort((a, b) => b.limit_messages - a.limit_messages || b.api_errors + b.tool_errors - (a.api_errors + a.tool_errors)).slice(0, 8);
  const topTool = Math.max(1, ...(data?.tools.top ?? []).map((tool) => tool.count));
  const total = fleet?.totals.value_usd ?? 0;
  const [graphs, setGraphs] = useGraphs('insights');
  const win = data?.window && data.window.count ? data.window : null;
  return (
    <div className="hq-page">
      <header className="page-head">
        <div>
          <h1>Insights</h1>
          <p className="lede">How quickly agents answer people, how reliably they work, and where the work goes. Last {PERIOD[range]}.</p>
        </div>
        <div className="head-tools"><GraphsToggle on={graphs} onChange={setGraphs} /><RangePicker value={range} onChange={setRange} /></div>
      </header>
      {failed && <p className="muted">Couldn’t load insights. Check that Agent HQ is running, then refresh.</p>}
      {data && (
        <>
          <section className="stats stats-grid" aria-label="Insight measures">
            <div className="stat"><div className="k">Typical reply to people</div><div className="v num">{fmtDuration(data.replies.all.median_s)}</div><div className="d num">median of {count(data.replies.all.replies)} replies</div></div>
            <div className="stat"><div className="k">Slowest replies</div><div className="v num">{fmtDuration(data.replies.all.p90_s)}</div><div className="d">1 in 10 replies took longer</div></div>
            <div className="stat"><div className="k">Typical task</div><div className="v num">{fmtDuration(data.tasks.median_s)}</div><div className="d num">{count(data.tasks.count)} tasks, longest 10% over {fmtDuration(data.tasks.p90_s)}</div></div>
            <div className="stat"><div className="k">Limit messages</div><div className="v num">{count(data.reliability.limit_messages)}</div><div className="d">replies saying a usage or rate limit was hit</div></div>
            <div className="stat"><div className="k">API errors</div><div className="v num">{count(data.reliability.api_errors)}</div><div className="d">failed or overloaded model calls</div></div>
            <div className="stat"><div className="k">Failed tool calls</div><div className="v num">{count(data.reliability.tool_errors)}</div><div className="d num">{percent(data.reliability.tool_errors, data.tools.total, 1)} of {count(data.tools.total)} tool calls</div></div>
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
                <div className="list-row" key={agent.host}>
                  <span className="list-name">{name(agent.host)}</span>
                  <span className="num muted">{fmtDuration(agent.reply.median_s)} typical · {count(agent.reply.replies)} replies</span>
                </div>
              )) : <p className="muted">Not enough replies in this period.</p>}
              <p className="note">Worked out from message times: a reply is paired with the oldest message still waiting on the same agent and channel. Messages in group chats often need no reply, so unanswered messages aren’t counted against anyone.</p>
              <div className="section-head insights-gap"><h2>Needs a look</h2></div>
              {troubled.length ? troubled.map((agent) => (
                <div className="list-row" key={agent.host}>
                  <span className="list-name">{name(agent.host)}</span>
                  <span className="num muted">
                    {[agent.limit_messages && `${count(agent.limit_messages)} limit`, agent.api_errors && `${count(agent.api_errors)} API errors`, agent.tool_errors && `${count(agent.tool_errors)} failed tools`].filter(Boolean).join(' · ')}
                    {data.reliability.last_limit[agent.host] ? ` · last limit ${fmtRelative(data.reliability.last_limit[agent.host], now)}` : ''}
                  </span>
                </div>
              )) : <p className="muted">No limits or errors in this period.</p>}
            </section>
            <section>
              <div className="section-head"><h2>Reply speed by channel</h2></div>
              {Object.keys(data.replies.by_channel).length ? Object.entries(data.replies.by_channel).map(([channel, reply]) => (
                <div className="list-row" key={channel}>
                  <span className="list-name">{channelName(channel)}</span>
                  <span className="num muted">{fmtDuration(reply.median_s)} typical · {count(reply.replies)}</span>
                </div>
              )) : <p className="muted">No replies to people in this period.</p>}
              <div className="section-head insights-gap"><h2>Most used tools</h2></div>
              {data.tools.top.length ? data.tools.top.map((tool) => (
                <div className="bar-row" key={tool.tool}>
                  <span className="bar-name" title={tool.tool}>{toolName(tool.tool)}</span>
                  <span className="bar"><i style={{ width: `${(tool.count / topTool) * 100}%` }} /></span>
                  <span className="num bar-value">{percent(tool.count, data.tools.total)}</span>
                </div>
              )) : <p className="muted">No tool calls in this period.</p>}
              {fleet && <GroupTable title="By group" rows={rollUp(fleet.agents, (agent) => agent.group)} total={total} />}
              {fleet && <GroupTable title="By owner" rows={rollUp(fleet.agents, (agent) => agent.owner ?? (agent.group === 'Department' ? 'Departments' : 'No owner listed'))} total={total} />}
              <p className="note">Value is what the tokens would cost at API prices, not your bill.</p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
