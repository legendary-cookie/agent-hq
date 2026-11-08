import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resetMe, canDo } from '../api/me';
import { Sidebar } from './Sidebar';

function signedInAs(me: object | null) {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => (
    path === '/api/me'
      ? (me ? { ok: true, status: 200, json: async () => me } : { ok: false, status: 404, json: async () => ({}) })
      : { ok: true, status: 200, json: async () => (path === '/api/brand' ? { brand: 'HQ' } : { run_mode: false }) }
  )));
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
}

beforeEach(() => { localStorage.clear(); localStorage.setItem('hq-token', 't'); resetMe(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const links = () => within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('button').map((b) => b.textContent);

it('shows only the pages the role can view, with Office alongside Overview and Insights alongside Usage', async () => {
  signedInAs({ username: 'sam', name: 'Sam', role: 'viewer', role_label: 'Viewer', permissions: ['view.overview', 'view.usage'], admin: false });
  render(<Sidebar route="overview" navigate={() => undefined} />);
  expect(await screen.findByText('sam')).toBeInTheDocument();
  expect(links()).toEqual(['Overview', 'Usage', 'Insights', 'Office']);
  expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Accounts' })).toBeNull();
  expect(screen.getByText('Viewer')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
});

it('an admin sees every page and Accounts', async () => {
  signedInAs({ username: 'boss', name: '', role: 'admin', role_label: 'Admin', permissions: [], admin: true });
  render(<Sidebar route="overview" navigate={() => undefined} />);
  expect(await screen.findByText('boss')).toBeInTheDocument();
  expect(links()).toEqual(['Overview', 'Agents', 'Usage', 'Insights', 'Router', 'Proxmox', 'Updates', 'Memory', 'Activity', 'Office']);
  expect(screen.getByRole('button', { name: 'Accounts' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
});

it('accounts.manage alone shows Accounts but not Settings', async () => {
  signedInAs({ username: 'hr', name: '', role: 'hr', role_label: 'People', permissions: ['accounts.manage', 'view.agents'], admin: false });
  render(<Sidebar route="agents" navigate={() => undefined} />);
  expect(await screen.findByText('hr')).toBeInTheDocument();
  expect(links()).toEqual(['Agents']);
  expect(screen.getByRole('button', { name: 'Accounts' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
});

it('an older server without /api/me hides nothing', async () => {
  signedInAs(null);
  render(<Sidebar route="overview" navigate={() => undefined} />);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(links()).toHaveLength(10);
  expect(canDo(null, 'accounts.manage')).toBe(true);
});
