import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Dashboard } from './Dashboard';
import kpis from '../test/fixtures/dashboard-kpis.json';
import overview from '../test/fixtures/dashboard-overview.json';
import series from '../test/fixtures/dashboard-series.json';
import sessions from '../test/fixtures/dashboard-sessions.json';

vi.mock('../api/client', () => ({
  useApi: (path: string) => ({
    data: path.startsWith('/api/kpis')
      ? kpis
      : path.startsWith('/api/series')
        ? series
        : path === '/api/overview'
          ? overview
          : sessions,
    loading: false,
    error: null,
  }),
}));
vi.mock('../components/Inspector', () => ({ Inspector: () => null }));
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
afterEach(cleanup);

it('Dashboard renders tiles, falls back to tokens chart when unpriced, ROI dash', () => {
  render(<Dashboard />);
  expect(screen.getByText('Active agents')).toBeInTheDocument();
  expect(screen.getByText('Success rate')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Tokens' })).toBeInTheDocument();
  expect(screen.getByText('account-a').closest('tr')).toHaveTextContent('—');
});
