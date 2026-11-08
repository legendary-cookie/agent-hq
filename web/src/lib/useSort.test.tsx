import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { SortHeader, compareValues, firstDir, sortRows, useSort } from './useSort';

type Row = { name: string; pct: number | null };
const ROWS: Row[] = [{ name: 'beta', pct: 5 }, { name: 'Alpha', pct: null }, { name: 'gamma', pct: 40 }, { name: 'agent 10', pct: 0.5 }, { name: 'agent 9', pct: 5 }];

beforeEach(() => localStorage.clear());
afterEach(cleanup);

it('sorts numbers, puts empty values last both ways, and keeps ties in order', () => {
  expect(sortRows(ROWS, (r) => r.pct, 'desc').map((r) => r.name)).toEqual(['gamma', 'beta', 'agent 9', 'agent 10', 'Alpha']);
  expect(sortRows(ROWS, (r) => r.pct, 'asc').map((r) => r.name)).toEqual(['agent 10', 'beta', 'agent 9', 'gamma', 'Alpha']);
});

it('sorts text without caring about case, and numbers inside text by value', () => {
  expect(sortRows(ROWS, (r) => r.name, 'asc').map((r) => r.name)).toEqual(['agent 9', 'agent 10', 'Alpha', 'beta', 'gamma']);
  expect(compareValues('', 'a', 'asc')).toBe(1);
  expect(compareValues(undefined, null, 'desc')).toBe(0);
});

it('starts numbers high to low and text A to Z', () => {
  expect(firstDir(ROWS, (r) => r.pct)).toBe('desc');
  expect(firstDir(ROWS, (r) => r.name)).toBe('asc');
});

function Table({ storageKey = 'test' }: { storageKey?: string }) {
  const sorter = useSort(ROWS, { columns: { name: (r) => r.name, pct: (r) => r.pct }, storageKey });
  return (
    <div role="table" aria-label="T">
      <div role="row"><SortHeader sorter={sorter} col="name">Name</SortHeader><SortHeader sorter={sorter} col="pct">Plan use</SortHeader></div>
      {sorter.rows.map((r) => <div role="row" key={r.name}><span role="cell">{r.name}</span></div>)}
    </div>
  );
}
const names = () => screen.getAllByRole('cell').map((cell) => cell.textContent);

it('keeps the default order until a header is clicked, then toggles and shows it', () => {
  render(<Table />);
  expect(names()).toEqual(ROWS.map((r) => r.name));
  const header = screen.getByRole('columnheader', { name: /Plan use/ });
  expect(header).toHaveAttribute('aria-sort', 'none');
  fireEvent.click(screen.getByRole('button', { name: /Plan use/ }));
  expect(header).toHaveAttribute('aria-sort', 'descending');
  expect(header).toHaveTextContent('▼');
  expect(names()).toEqual(['gamma', 'beta', 'agent 9', 'agent 10', 'Alpha']);
  fireEvent.click(screen.getByRole('button', { name: /Plan use/ }));
  expect(header).toHaveAttribute('aria-sort', 'ascending');
  expect(header).toHaveTextContent('▲');
  expect(names()[4]).toBe('Alpha');
  fireEvent.click(screen.getByRole('button', { name: /Name/ }));
  expect(screen.getByRole('columnheader', { name: /Name/ })).toHaveAttribute('aria-sort', 'ascending');
  expect(header).toHaveAttribute('aria-sort', 'none');
});

it('remembers the choice per table', () => {
  render(<Table />);
  fireEvent.click(screen.getByRole('button', { name: /Name/ }));
  fireEvent.click(screen.getByRole('button', { name: /Name/ }));
  expect(JSON.parse(localStorage.getItem('hq-sort:test')!)).toEqual({ key: 'name', dir: 'desc' });
  cleanup();
  render(<Table />);
  expect(names()[0]).toBe('gamma');
  cleanup();
  render(<Table storageKey="other" />);
  expect(names()).toEqual(ROWS.map((r) => r.name));
});

it('ignores a saved choice for a column that no longer exists', () => {
  localStorage.setItem('hq-sort:test', JSON.stringify({ key: 'gone', dir: 'asc' }));
  render(<Table />);
  expect(names()).toEqual(ROWS.map((r) => r.name));
});
