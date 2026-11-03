import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LimitBar } from './LimitBar';
import { KpiTile } from './KpiTile';
import { selectSidebarLimits } from './Sidebar';
import { AlertBanner } from './AlertBanner';
import { Inspector } from './Inspector';
import type { LimitCard, Overview } from '../api/types';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const windowData = (percent: number | null, source = 'measured') => ({
  window: '5h',
  scope: '*',
  used_percent: percent,
  resets_at: null,
  source,
  stale: false,
  age_s: null,
  forecast_s: null,
  burn_tokens_per_h: 0,
});

it('LimitBar colour thresholds', () => {
  const { rerender, container } = render(<LimitBar percent={79} source="measured" stale={false} />);
  expect(container.querySelector('.limit-fill')).toHaveClass('limit-green');
  rerender(<LimitBar percent={80} source="measured" stale={false} />);
  expect(container.querySelector('.limit-fill')).toHaveClass('limit-orange');
  rerender(<LimitBar percent={95} source="measured" stale={false} />);
  expect(container.querySelector('.limit-fill')).toHaveClass('limit-red');
  rerender(<LimitBar percent={null} source="blocked" stale={false} />);
  expect(container.querySelector('.limit-fill')).toHaveClass('limit-red');
  rerender(<LimitBar percent={null} source="unknown" stale={false} />);
  expect(container.querySelector('.limit-fill')).toBeNull();
});

it('KpiTile shows dash for null', () => {
  render(<KpiTile label="Success rate" value="—" />);
  expect(screen.getByText('—')).toBeInTheDocument();
});

it('selectSidebarLimits orders blocked first', () => {
  const cards: LimitCard[] = [
    {
      account_id: 'a',
      label: 'A',
      windows: [windowData(99), windowData(null), windowData(20, 'blocked')],
    },
  ];
  expect(selectSidebarLimits(cards).map((item) => item.source)).toEqual(['blocked', 'measured']);
});

it('AlertBanner shows most severe and hides when empty', () => {
  const alerts: Overview['alerts'] = [
    { account_id: 'codex', window: '5h', scope: '*', level: 'warn', used_percent: 82 },
    { account_id: 'other', window: 'week', scope: 'model', level: 'blocked', used_percent: 100 },
  ];
  const { rerender } = render(<AlertBanner alerts={alerts} />);
  expect(screen.getByText(/other.*model.*blocked/i)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '+1 more' })).toHaveAttribute('href', '#/limits');
  fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
  expect(screen.queryByText(/other.*blocked/i)).not.toBeInTheDocument();
  rerender(<AlertBanner alerts={[]} />);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('Inspector renders timeline and subagents', async () => {
  const session = {
    id: 'parent',
    harness: 'Codex',
    model: 'model',
    account_id: 'a',
    project: 'project',
    started_at: 100,
    ended_at: 200,
    parent_id: null,
    status: 'working',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session,
        events: [{ id: 1, ts: 150, kind: 'tool_call', tool: 'npm test', preview: null }],
        children: [{ ...session, id: 'child', title: 'Child agent' }],
        value: {
          tokens: 1200,
          value_usd: 0.62,
          unpriced_tokens: 0,
          effective_cost_usd: null,
          estimated: false,
        },
        account_limit: null,
      }),
    }),
  );
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Inspector sessionId="parent" onClose={() => {}} />);
  await waitFor(() => expect(screen.getAllByText('npm test').length).toBeGreaterThan(0));
  expect(screen.getByText('Timeline')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Child agent/ }));
  await waitFor(() =>
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/sessions/child?limit=50&order=desc',
      expect.anything(),
    ),
  );
});

it('Inspector shows latest events first', async () => {
  const session = {
    id: 's',
    harness: 'Codex',
    model: null,
    account_id: null,
    project: null,
    started_at: null,
    ended_at: null,
    parent_id: 'p',
    status: 'working',
  };
  const fetcher = vi.fn().mockImplementation(async (path: string) => ({
    ok: true,
    json: async () => ({
      session: path.includes('/p?') ? { ...session, id: 'p', title: 'Parent' } : session,
      events: [
        { id: 2, ts: 2, kind: 'tool_call', tool: 'newest', preview: null },
        { id: 1, ts: 1, kind: 'tool_call', tool: 'oldest', preview: null },
      ],
      children: [],
      value: {
        tokens: 0,
        value_usd: 0,
        unpriced_tokens: 0,
        effective_cost_usd: null,
        estimated: false,
      },
      account_limit: null,
    }),
  }));
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Inspector sessionId="s" onClose={() => {}} />);
  await waitFor(() => expect(screen.getAllByText('newest').length).toBe(2));
  expect(fetcher).toHaveBeenCalledWith('/api/sessions/s?limit=50&order=desc', expect.anything());
  expect(fetcher).toHaveBeenCalledWith('/api/sessions/p?limit=1', expect.anything());
  const timeline = screen.getByText('Timeline').nextElementSibling!;
  expect(timeline.textContent!.indexOf('newest')).toBeLessThan(
    timeline.textContent!.indexOf('oldest'),
  );
});

it('AlertBanner reads all models for star scope', () => {
  render(
    <AlertBanner
      alerts={[{ account_id: 'a', window: '5h', scope: '*', level: 'blocked', used_percent: null }]}
    />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent('a · all models blocked');
  expect(
    selectSidebarLimits([
      { account_id: 'a', label: 'A', windows: [windowData(null, 'blocked')] },
    ])[0].window,
  ).toBe('all models');
});

it('Inspector shows imported note', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          session: { id: 'i', imported: 1, status: 'active' },
          events: [],
          children: [],
          value: {
            tokens: 0,
            value_usd: 0,
            unpriced_tokens: 0,
            effective_cost_usd: null,
            estimated: false,
          },
          account_limit: null,
        }),
      }),
  );
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Inspector sessionId="i" onClose={() => {}} />);
  expect(
    await screen.findByText('Imported from llm-usage-metrics; no live timeline.'),
  ).toBeInTheDocument();
});

it('inspector shows host badge', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    session: { id: 'remote', host: 'box' }, events: [], children: [],
    value: { tokens: 0, value_usd: 0, unpriced_tokens: 0, effective_cost_usd: null, estimated: false }, account_limit: null,
  }) }));
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  render(<Inspector sessionId="remote" onClose={() => {}} />);
  expect(await screen.findByText('box')).toBeInTheDocument();
});
