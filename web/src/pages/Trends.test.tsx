import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it } from 'vitest';
import { GraphsToggle, TrendChart, chartRows, useGraphs } from './Trends';
import type { GraphPage } from './Trends';

beforeEach(() => localStorage.clear());

function Page({ page }: { page: GraphPage }) {
  const [on, set] = useGraphs(page);
  return <><GraphsToggle on={on} onChange={set} />{on && <p>graphs here</p>}</>;
}

it('shows graphs until switched off, and remembers that per page', () => {
  const { unmount } = render(<Page page="usage" />);
  expect(screen.getByText('graphs here')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /graphs on/i }));
  expect(screen.queryByText('graphs here')).toBeNull();
  unmount();
  render(<Page page="usage" />);
  expect(screen.getByRole('button', { name: /graphs off/i })).toHaveAttribute('aria-pressed', 'false');
  expect(JSON.parse(localStorage.getItem('hq-graphs') ?? '{}')).toEqual({ usage: false });
});

it('a broken saved choice just means graphs are on', () => {
  localStorage.setItem('hq-graphs', '{oops');
  render(<Page page="overview" />);
  expect(screen.getByText('graphs here')).toBeInTheDocument();
});

it('builds one row per cell, with missing values as gaps', () => {
  const rows = chartRows({ start: 0, bucket_s: 3600, count: 3 }, [{ key: 'a', label: 'A', values: [1, null], color: 'red' }]);
  expect(rows.map((row) => row.a)).toEqual([1, null, null]);
});

it('says so when there is nothing to draw', () => {
  render(<TrendChart title="Quiet" win={{ start: 0, bucket_s: 3600, count: 2 }} series={[{ key: 'a', label: 'A', values: [0, 0], color: 'red' }]} />);
  expect(screen.getByText('Nothing recorded in this period.')).toBeInTheDocument();
});
