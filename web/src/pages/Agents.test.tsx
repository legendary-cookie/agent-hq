import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Agents } from './Agents';
import sessions from '../test/fixtures/agents-sessions.json';

const state = vi.hoisted(() => ({ sessions: [] as unknown[] }));
vi.mock('../api/client', () => ({
  useApi: () => ({ data: state.sessions, loading: false, error: null }),
}));
vi.mock('../components/Inspector', () => ({ Inspector: () => null }));
afterEach(cleanup);

it('Agents filters by status', () => {
  state.sessions = sessions;
  render(<Agents />);
  fireEvent.click(screen.getByRole('button', { name: 'Working' }));
  expect(screen.getByText('Parent')).toBeInTheDocument();
  expect(screen.queryByText('Child')).not.toBeInTheDocument();
});

it('Agents empty state', () => {
  state.sessions = [];
  render(<Agents />);
  expect(screen.getByText(/no sessions/i)).toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('Agents nests children under parent', () => {
  state.sessions = sessions;
  render(<Agents />);
  const rows = within(screen.getByRole('table')).getAllByRole('row');
  expect(rows[1]).toHaveTextContent('Parent');
  expect(rows[2]).toHaveTextContent('Child');
  expect(within(rows[2]).getByText('Child').closest('td')).toHaveClass('agents-child');
});

it('Agents title has no stray dot', () => {
  state.sessions = sessions;
  render(<Agents />);
  const row = screen.getByText('Parent').closest('tr')!;
  const dot = row.querySelector('.status-dot')!;
  expect(dot).toBeInTheDocument();
  expect(dot).toHaveClass('agents-status-dot');
  expect(within(row).getByRole('button', { name: 'Parent' })).toHaveTextContent(/^Parent$/);
});
