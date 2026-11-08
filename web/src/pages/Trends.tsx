/** Graphs over time for Overview, Usage and Insights, each one switchable off and remembered. */
import { useState } from 'react';
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import './trends.css';

export type GraphPage = 'overview' | 'usage' | 'insights' | 'proxmox' | 'router';
const KEY = 'hq-graphs';

function readGraphs(): Record<string, boolean> {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch {
    return {};
  }
}

/** Whether graphs show on a page. On unless switched off; the choice stays on this browser. */
export function useGraphs(page: GraphPage) {
  const [on, setOn] = useState(() => readGraphs()[page] !== false);
  const set = (next: boolean) => {
    try { localStorage.setItem(KEY, JSON.stringify({ ...readGraphs(), [page]: next })); } catch { /* Storage is optional. */ }
    setOn(next);
  };
  return [on, set] as const;
}

export function GraphsToggle({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button type="button" className="graphs-toggle" aria-pressed={on} onClick={() => onChange(!on)} title={on ? 'Hide the graphs on this page' : 'Show graphs on this page'}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 13h12M3.5 10.5l3-3.5 2.5 2 4-5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      Graphs {on ? 'on' : 'off'}
    </button>
  );
}

export type TimeWindow = { start: number; bucket_s: number; count: number };
export type Serie = {
  key: string;
  label: string;
  values: (number | null)[];
  /** A CSS colour; theme variables work. */
  color: string;
  kind?: 'line' | 'bar';
  dashed?: boolean;
  /** Put it on its own scale on the right, for a measure much bigger or smaller than the rest. */
  right?: boolean;
  stack?: string;
};

export function cellLabel(win: TimeWindow, index: number, short = false) {
  const date = new Date((win.start + index * win.bucket_s) * 1000);
  const day = date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const time = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (win.bucket_s <= 3600) return time;
  if (win.bucket_s >= 86400) return day;
  return short ? day : `${day}, ${time}`;
}

/** Rows for the chart: one per cell, with each series' value under its key. */
export function chartRows(win: TimeWindow, series: Serie[]) {
  return Array.from({ length: win.count }, (_, index) => {
    const row: Record<string, number | string | null> = { at: cellLabel(win, index), tick: cellLabel(win, index, true) };
    for (const serie of series) row[serie.key] = serie.values[index] ?? null;
    return row;
  });
}

export function TrendChart({ title, note, win, series, format = (n) => n.toLocaleString('en-US'), formatRight, height = 200 }: {
  title: string;
  note?: string;
  win: TimeWindow;
  series: Serie[];
  format?: (n: number) => string;
  formatRight?: (n: number) => string;
  height?: number;
}) {
  const rows = chartRows(win, series);
  const right = series.some((serie) => serie.right);
  const empty = series.every((serie) => serie.values.every((value) => !value));
  const byKey = new Map(series.map((serie) => [serie.key, serie]));
  return (
    <figure className="hq-trend" aria-label={title}>
      <figcaption className="hq-trend-head">
        <h2>{title}</h2>
        <span className="hq-trend-legend" aria-hidden="true">
          {series.map((serie) => <span key={serie.key}><i style={{ background: serie.color, opacity: serie.dashed ? 0.6 : 1 }} />{serie.label}</span>)}
        </span>
      </figcaption>
      {empty ? <p className="muted hq-trend-empty">Nothing recorded in this period.</p> : (
        <div className="hq-trend-plot" style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 6, right: right ? 0 : 6, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="tick" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={28} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
              <YAxis yAxisId="left" tickLine={false} axisLine={false} width={48} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickFormatter={(n: number) => format(n)} allowDecimals={false} />
              {right && <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} width={48} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickFormatter={(n: number) => (formatRight ?? format)(n)} allowDecimals={false} />}
              <Tooltip
                cursor={{ fill: 'var(--hover)' }}
                contentStyle={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, fontSize: 12 }}
                labelStyle={{ color: 'var(--muted)' }}
                labelFormatter={(_, payload) => String(payload?.[0]?.payload?.at ?? '')}
                formatter={(value: number, key: string) => {
                  const serie = byKey.get(key);
                  return [serie?.right ? (formatRight ?? format)(value) : format(value), serie?.label ?? key];
                }}
              />
              {series.map((serie) => serie.kind === 'bar'
                ? <Bar key={serie.key} dataKey={serie.key} yAxisId={serie.right ? 'right' : 'left'} fill={serie.color} stackId={serie.stack} radius={serie.stack ? 0 : [2, 2, 0, 0]} isAnimationActive={false} maxBarSize={18} />
                : <Line key={serie.key} dataKey={serie.key} yAxisId={serie.right ? 'right' : 'left'} stroke={serie.color} strokeWidth={1.75} strokeDasharray={serie.dashed ? '4 3' : undefined} dot={false} connectNulls={false} isAnimationActive={false} type="linear" />)}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {note && <p className="note">{note}</p>}
    </figure>
  );
}
