import { continuousBuckets } from '../lib/series';
import { useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useApi } from '../api/client';
import type { KpiBlock, Kpis, Overview, SeriesRow, SessionRow, Tokens } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { Inspector } from '../components/Inspector';
import { KpiTile } from '../components/KpiTile';
import { Pill } from '../components/Pill';
import { Segmented } from '../components/Segmented';
import { fmtDelta, fmtPct, fmtTokens, fmtUsd } from '../lib/format';
import './dashboard.css';

type Range = 'today' | 'week' | 'month';
type Group = 'harness' | 'provider' | 'model' | 'account';
const totalTokens = (tokens: Tokens) =>
  tokens.input + tokens.output + tokens.cache_read + tokens.cache_write;
const dateLabel = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const valueLabel = (block: KpiBlock) =>
  fmtUsd(block.value.total_usd, {
    unpricedTokens: block.value.unpriced_tokens,
    estimated: block.value.estimated,
  });

export function Dashboard() {
  const [range, setRange] = useState<Range>('today');
  const [group, setGroup] = useState<Group>('harness');
  const [selected, setSelected] = useState<string | null>(null);
  const { data: overview } = useApi<Overview>('/api/overview');
  const { data: kpis, error } = useApi<Kpis>(`/api/kpis?range=${range}`);
  const { data: series } = useApi<SeriesRow[]>(
    `/api/series?range=${range}&bucket=${range === 'today' ? 'hour' : 'day'}&by=${group}`,
  );
  const { data: sessions } = useApi<SessionRow[]>('/api/sessions');
  const unpriced = !!series?.length && series.every((row) => row.value_usd === null);
  const keys = [...new Set((series ?? []).map((row) => row.key))];
  const dates = continuousBuckets(
    (series ?? []).map((row) => row.t),
    range === 'today' ? 'hour' : 'day',
  );
  const chart = dates.map((t) => {
    const rows = (series ?? []).filter((row) => row.t === t);
    const values: Record<string, number | string> = {
      ...Object.fromEntries(keys.map((key) => [key, 0])),
      date:
        range === 'today'
          ? `${String(new Date(t * 1000).getHours()).padStart(2, '0')}:00`
          : dateLabel(t),
      tasks: rows.reduce((sum, row) => sum + row.tasks, 0),
      errors: rows.reduce((sum, row) => sum + row.errors, 0),
    };
    rows.forEach((row) => {
      values[row.key] =
        (Number(values[row.key]) || 0) + (unpriced ? row.tokens : (row.value_usd ?? 0));
    });
    return values;
  });
  const colors = ['var(--accent)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)'];
  const tooltip = {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 10,
    color: 'var(--text)',
  };
  const tiles = kpis
    ? ([
        [
          'Active agents',
          String(kpis.activity.active_agents),
          kpis.activity.active_agents,
          kpis.previous.activity.active_agents,
        ],
        ['Tasks', String(kpis.activity.tasks), kpis.activity.tasks, kpis.previous.activity.tasks],
        [
          'Success rate',
          fmtPct(kpis.quality.success_rate),
          kpis.quality.success_rate,
          kpis.previous.quality.success_rate,
        ],
        [
          'Tool calls',
          fmtTokens(kpis.activity.tool_calls),
          kpis.activity.tool_calls,
          kpis.previous.activity.tool_calls,
        ],
        [
          'Agent hours',
          kpis.activity.agent_hours.toFixed(1),
          kpis.activity.agent_hours,
          kpis.previous.activity.agent_hours,
        ],
        [
          'Tokens',
          fmtTokens(totalTokens(kpis.tokens)),
          totalTokens(kpis.tokens),
          totalTokens(kpis.previous.tokens),
        ],
        [
          'API value',
          valueLabel(kpis),
          kpis.value.unpriced_tokens && !kpis.value.total_usd ? null : kpis.value.total_usd,
          kpis.previous.value.unpriced_tokens && !kpis.previous.value.total_usd
            ? null
            : kpis.previous.value.total_usd,
        ],
        [
          'Cache hit',
          fmtPct(kpis.tokens.cache_hit_ratio),
          kpis.tokens.cache_hit_ratio,
          kpis.previous.tokens.cache_hit_ratio,
        ],
      ] as const)
    : [];
  return (
    <div className="dashboard-layout">
      <div className="dashboard-page">
        <div className="dashboard-header">
          <h1>Dashboard</h1>
          <div className="dashboard-controls">
            <Segmented
              value={range}
              onChange={setRange}
              options={[
                { value: 'today', label: 'Today' },
                { value: 'week', label: 'Week' },
                { value: 'month', label: 'Month' },
              ]}
            />
            <Segmented
              value={group}
              onChange={setGroup}
              options={[
                { value: 'harness', label: 'Harness' },
                { value: 'provider', label: 'Provider' },
                { value: 'model', label: 'Model' },
                { value: 'account', label: 'Account' },
              ]}
            />
          </div>
        </div>
        {overview && (
          <div className="dashboard-pills">
            <Pill color="var(--working)">{overview.status_counts.working ?? 0} working</Pill>
            <Pill color="var(--waiting)">{overview.status_counts.waiting ?? 0} needs you</Pill>
            <Pill color="var(--idle)">{overview.status_counts.idle ?? 0} idle</Pill>
            {overview.best_now?.[0] && (
              <Pill>
                Best now: {overview.best_now[0].provider} · {overview.best_now[0].model}
              </Pill>
            )}
            <Pill>
              Today · {fmtTokens(totalTokens(overview.tokens))} tokens ·{' '}
              {overview.value.total_usd === 0 && overview.value.unpriced_tokens > 0
                ? 'value unpriced'
                : fmtUsd(overview.value.total_usd, { estimated: overview.value.estimated })}
            </Pill>
          </div>
        )}
        {error && !kpis && <p>Unable to load dashboard.</p>}
        {kpis && (
          <>
            <div className="dashboard-tiles">
              {tiles.map(([label, value, current, previous]) => (
                <KpiTile
                  key={label}
                  label={label}
                  value={value}
                  delta={fmtDelta(current, previous)}
                  hint={
                    label === 'API value' && kpis.value.unpriced_tokens > 0
                      ? `${fmtTokens(kpis.value.unpriced_tokens)} tokens unpriced`
                      : undefined
                  }
                />
              ))}
            </div>
            <div className="dashboard-charts">
              <section className="dashboard-card dashboard-wide">
                <h2>{unpriced ? 'Tokens' : 'API value'}</h2>
                {chart.length ? (
                  <div className="dashboard-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={chart}>
                        <CartesianGrid stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="date" stroke="var(--muted)" tick={{ fontSize: 11 }} />
                        <YAxis
                          stroke="var(--muted)"
                          tick={{ fontSize: 11 }}
                          tickFormatter={unpriced ? fmtTokens : (n: number) => fmtUsd(n)}
                          width={65}
                        />
                        <Tooltip
                          contentStyle={tooltip}
                          formatter={(n: number) => (unpriced ? fmtTokens(n) : fmtUsd(n))}
                        />
                        {keys.map((key, index) => (
                          <Area
                            key={key}
                            type="monotone"
                            dataKey={key}
                            stackId="value"
                            stroke={colors[index % colors.length]}
                            fill={colors[index % colors.length]}
                            fillOpacity={0.35}
                          />
                        ))}
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No chart data" />
                )}
              </section>
              <section className="dashboard-card">
                <h2>Tokens by model</h2>
                {kpis.top_models.length ? (
                  <div className="dashboard-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={kpis.top_models.slice(0, 8)} layout="vertical">
                        <XAxis
                          type="number"
                          stroke="var(--muted)"
                          tickFormatter={fmtTokens}
                          tick={{ fontSize: 11 }}
                        />
                        <YAxis
                          type="category"
                          dataKey="model"
                          stroke="var(--muted)"
                          width={150}
                          tickFormatter={(name: string) =>
                            name.length > 22 ? `${name.slice(0, 21)}…` : name
                          }
                          tick={{ fontSize: 11 }}
                        />
                        <Tooltip contentStyle={tooltip} formatter={(n: number) => fmtTokens(n)} />
                        <Bar dataKey="tokens" fill="var(--accent)" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No model data" />
                )}
              </section>
              <section className="dashboard-card">
                <h2>Tasks and errors</h2>
                {chart.length ? (
                  <div className="dashboard-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chart}>
                        <CartesianGrid stroke="var(--border)" vertical={false} />
                        <XAxis dataKey="date" stroke="var(--muted)" tick={{ fontSize: 11 }} />
                        <YAxis stroke="var(--muted)" tick={{ fontSize: 11 }} width={35} />
                        <Tooltip contentStyle={tooltip} />
                        <Bar dataKey="tasks" fill="var(--accent)" />
                        <Bar dataKey="errors" fill="var(--error)" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState title="No activity data" />
                )}
              </section>
            </div>
            <div className="dashboard-tables">
              <section className="dashboard-card">
                <h2>Top sessions</h2>
                {kpis.top_sessions.length ? (
                  <table>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Harness</th>
                        <th className="dashboard-number">Tokens</th>
                        <th className="dashboard-number">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {kpis.top_sessions.map((item) => {
                        const session = sessions?.find((row) => row.id === item.session_id);
                        return (
                          <tr key={item.session_id} onClick={() => setSelected(item.session_id)}>
                            <td>
                              <button type="button" onClick={() => setSelected(item.session_id)}>
                                {session?.title || item.session_id.slice(-12)}
                              </button>
                            </td>
                            <td>{session?.harness ?? '—'}</td>
                            <td className="dashboard-number">{fmtTokens(item.tokens)}</td>
                            <td className="dashboard-number">
                              {item.value_usd === null ? 'unpriced' : fmtUsd(item.value_usd)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState title="No sessions" />
                )}
              </section>
              <section className="dashboard-card">
                <h2>Plan ROI</h2>
                {Object.keys(kpis.plan_roi).length ? (
                  <table>
                    <thead>
                      <tr>
                        <th>Account</th>
                        <th className="dashboard-number">Price</th>
                        <th className="dashboard-number">Value</th>
                        <th className="dashboard-number">ROI ×</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(kpis.plan_roi).map(([account, roi]) => (
                        <tr key={account}>
                          <td>{account}</td>
                          <td className="dashboard-number">{fmtUsd(roi.monthly_price_usd)}</td>
                          <td className="dashboard-number">{fmtUsd(roi.value_usd)}</td>
                          <td className="dashboard-number">
                            {roi.roi == null || !Number.isFinite(roi.roi)
                              ? '—'
                              : `${roi.roi.toFixed(2)}×`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState title="No plan data" />
                )}
              </section>
              <section className="dashboard-card">
                <h2>Routing</h2>
                <h3>Delegations per provider</h3>
                {Object.keys(kpis.routing.delegations_per_provider).length ? (
                  Object.entries(kpis.routing.delegations_per_provider).map(([name, count]) => (
                    <div className="dashboard-routing" key={name}>
                      <span>{name}</span>
                      <span>{fmtTokens(count)}</span>
                    </div>
                  ))
                ) : (
                  <p className="dashboard-note">No delegations</p>
                )}
                <h3>Blocked hours per model</h3>
                {Object.keys(kpis.routing.hours_blocked).length ? (
                  Object.entries(kpis.routing.hours_blocked).map(([name, hours]) => (
                    <div className="dashboard-routing" key={name}>
                      <span>{name}</span>
                      <span>{hours.toFixed(1)} h</span>
                    </div>
                  ))
                ) : (
                  <p className="dashboard-note">No blocked hours</p>
                )}
              </section>
            </div>
          </>
        )}
      </div>
      <Inspector sessionId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
