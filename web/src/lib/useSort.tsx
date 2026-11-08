/** Sortable tables: click a column header to sort by it, click again to flip. Numbers start high to low,
 *  text A to Z. Empty values always go last. The choice is remembered per table. */
import { useState } from 'react';
import type { ReactNode } from 'react';

export type SortDir = 'asc' | 'desc';
export type SortValue = number | string | null | undefined;
export type SortState = { key: string; dir: SortDir } | null;
export type SortColumns<T> = Record<string, (row: T) => SortValue>;

const PREFIX = 'hq-sort:';

function load(storageKey: string | undefined, columns: Record<string, unknown>, initial: SortState): SortState {
  if (!storageKey) return initial;
  try {
    const saved = JSON.parse(localStorage.getItem(PREFIX + storageKey) ?? 'null');
    if (saved && typeof saved.key === 'string' && saved.key in columns && (saved.dir === 'asc' || saved.dir === 'desc')) return saved;
  } catch { /* a broken saved value falls back to the default */ }
  return initial;
}

const empty = (v: SortValue) => v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v));

/** Compare two values, empty ones last whichever way the column is sorted. */
export function compareValues(a: SortValue, b: SortValue, dir: SortDir) {
  const ae = empty(a), be = empty(b);
  if (ae || be) return ae && be ? 0 : ae ? 1 : -1;
  const order = typeof a === 'number' && typeof b === 'number'
    ? a - b
    : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
  return dir === 'asc' ? order : -order;
}

/** Sort rows by a column. Stable, so rows that tie keep their order. */
export function sortRows<T>(rows: readonly T[], value: (row: T) => SortValue, dir: SortDir): T[] {
  return [...rows].sort((a, b) => compareValues(value(a), value(b), dir));
}

/** Numbers start high to low, text A to Z. */
export function firstDir<T>(rows: readonly T[], value: (row: T) => SortValue): SortDir {
  const sample = rows.map(value).find((v) => !empty(v));
  return typeof sample === 'number' ? 'desc' : 'asc';
}

export interface Sorter<T> {
  rows: T[];
  sort: SortState;
  toggle: (key: string) => void;
  /** For the header cell: aria-sort, and whether it's sorted. */
  ariaSort: (key: string) => 'ascending' | 'descending' | 'none';
}

/** rows: the table's rows in their default order. initial: the default sort, or null to keep that order. */
export function useSort<T>(rows: readonly T[], { columns, initial = null, storageKey }: { columns: SortColumns<T>; initial?: SortState; storageKey?: string }): Sorter<T> {
  const [sort, setSort] = useState<SortState>(() => load(storageKey, columns, initial));
  const active = sort && columns[sort.key] ? sort : null;
  const value = active ? columns[active.key] : null;
  // Tables here are small, so sorting on every render is cheap and never stale.
  const sorted = active && value ? sortRows(rows, value, active.dir) : [...rows];
  const toggle = (key: string) => {
    if (!columns[key]) return;
    const next: SortState = active?.key === key ? { key, dir: active.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: firstDir(rows, columns[key]) };
    setSort(next);
    if (storageKey) {
      try { localStorage.setItem(PREFIX + storageKey, JSON.stringify(next)); } catch { /* storage full or blocked: the sort still works */ }
    }
  };
  const ariaSort = (key: string) => (active?.key === key ? (active.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  return { rows: sorted, sort: active, toggle, ariaSort };
}

/** A sortable column header: a real <th> or a mini-table columnheader span, with a button inside. */
export function SortHeader<T>({ sorter, col, children, as = 'span', className }: { sorter: Sorter<T>; col: string; children: ReactNode; as?: 'th' | 'span'; className?: string }) {
  const state = sorter.ariaSort(col);
  const button = (
    <button type="button" className="sort-button" data-sorted={state !== 'none' || undefined} onClick={() => sorter.toggle(col)}>
      {children}
      <span className="sort-arrow" aria-hidden="true">{state === 'ascending' ? '▲' : state === 'descending' ? '▼' : ''}</span>
    </button>
  );
  if (as === 'th') return <th className={className} aria-sort={state}>{button}</th>;
  return <span role="columnheader" className={className} aria-sort={state}>{button}</span>;
}
