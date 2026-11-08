import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PickBox, pickState, togglePicks } from './PickBox';

afterEach(cleanup);

it('knows when a whole group, part of it or none is picked', () => {
  expect(pickState(['a', 'b'], new Set(['a', 'b']))).toEqual({ all: true, some: false });
  expect(pickState(['a', 'b'], new Set(['a']))).toEqual({ all: false, some: true });
  expect(pickState(['a', 'b'], new Set(['c']))).toEqual({ all: false, some: false });
  expect(pickState([], new Set())).toEqual({ all: false, some: false });
});

it('adds the rest of a group, or removes it when all are picked, leaving other picks alone', () => {
  expect([...togglePicks(['a', 'b'], new Set(['a', 'x']))].sort()).toEqual(['a', 'b', 'x']);
  expect([...togglePicks(['a', 'b'], new Set(['a', 'b', 'x']))]).toEqual(['x']);
});

it('shows half-ticked for part of a group and picks the whole group on click', () => {
  const change = vi.fn();
  render(<PickBox hosts={['a', 'b']} picked={new Set(['a'])} onChange={change} label="Select every Department agent" />);
  const box = screen.getByRole('checkbox', { name: 'Select every Department agent' }) as HTMLInputElement;
  expect(box.indeterminate).toBe(true);
  fireEvent.click(box);
  expect([...change.mock.calls[0][0]].sort()).toEqual(['a', 'b']);
});
