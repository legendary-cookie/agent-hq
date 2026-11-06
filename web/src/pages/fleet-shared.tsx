import { useRef, useState } from 'react';
import { useApi } from '../api/client';
import type { Fleet, FleetAgent } from '../api/types';
import { Segmented } from '../components/Segmented';

export type Range = 'today' | 'week' | 'month';
export const STATUS: Record<FleetAgent['status'], string> = {
  working: 'Working',
  recent: 'Active recently',
  idle: 'Idle',
  quiet: 'Quiet',
  attention: 'Needs attention',
};
const KEY = 'hq-range';

export const count = (n: number) => n.toLocaleString('en-US');
export function money(n: number, unpriced = 0) {
  if (n === 0 && unpriced > 0) return 'Unpriced';
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: n < 1000 ? 2 : 0, maximumFractionDigits: n < 1000 ? 2 : 0 })}`;
}
export const messages = (agent: FleetAgent) => agent.messages_in + agent.messages_out;
export const actions = (agent: FleetAgent) => agent.cells.reduce((sum, value) => sum + value, 0);
export function percent(part: number, whole: number, digits = 0) {
  if (!whole) return '—';
  return `${((part / whole) * 100).toFixed(digits)}%`;
}
export function perTask(value: number, tasks: number) {
  return tasks ? money(value / tasks) : '—';
}

export function changeText(current: number, previous: number) {
  if (!previous) return null;
  const change = Math.round(((current - previous) / previous) * 100);
  return `${change > 0 ? '+' : ''}${change}%`;
}

/** The chosen period, remembered between pages. The last answer stays up while a new one loads. */
export function useFleet() {
  const [range, setRangeState] = useState<Range>(() => {
    const saved = sessionStorage.getItem(KEY);
    return saved === 'today' || saved === 'month' ? saved : 'week';
  });
  const setRange = (next: Range) => {
    sessionStorage.setItem(KEY, next);
    setRangeState(next);
  };
  const { data, error } = useApi<Fleet>(`/api/fleet?range=${range}`);
  const last = useRef<Fleet | null>(null);
  if (data) last.current = data;
  return { range, setRange, data: last.current, error };
}

export function RangePicker({ value, onChange }: { value: Range; onChange: (range: Range) => void }) {
  return (
    <Segmented
      value={value}
      onChange={onChange}
      options={[
        { value: 'today', label: '24 hours' },
        { value: 'week', label: '7 days' },
        { value: 'month', label: '30 days' },
      ]}
    />
  );
}

export function StatusDot({ status }: { status: FleetAgent['status'] }) {
  return <span className={`dot dot-${status}`} title={STATUS[status]} />;
}

export function ownerName(agent: FleetAgent) {
  return agent.owner ?? '—';
}
