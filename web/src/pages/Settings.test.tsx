import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import settings from '../test/fixtures/settings-data.json';
import kpis from '../test/fixtures/settings-kpis.json';
import health from '../test/fixtures/settings-health.json';
import { Settings } from './Settings';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(fail = false) {
  const fetcher = vi.fn().mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      return {
        ok: !fail,
        status: 400,
        json: async () => (fail ? { error: 'Invalid settings' } : {}),
      };
    }
    const data = path === '/api/settings' ? settings : path === '/api/health' ? health : kpis;
    return { ok: true, json: async () => data };
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Settings />);
  return fetcher;
}

it('Settings lists unpriced models first', async () => {
  setup();
  await screen.findByText('unpriced-model');
  const unpriced = screen.getByText('unpriced-model');
  const priced = screen.getByText('priced');
  expect(unpriced.compareDocumentPosition(priced) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('Settings save sends unchanged sources', async () => {
  const fetcher = setup();
  await screen.findByDisplayValue('Codex');
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  // The settings calls, plus the alerts and agent keys panels' reads (/api/watch, /api/control, /api/agent-keys).
  await waitFor(() => expect(fetcher.mock.calls.filter((call) => !['/api/watch', '/api/control', '/api/agent-keys'].includes(String(call[0])))).toHaveLength(5));
  expect(JSON.parse(fetcher.mock.calls.find((call) => call[1]?.method === 'PUT')![1].body).sources).toEqual(settings.sources);
  expect(await screen.findByText('Saved')).toBeInTheDocument();
});

it('Settings shows 400 message', async () => {
  setup(true);
  await screen.findByDisplayValue('Codex');
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  expect(await screen.findByText('Invalid settings')).toBeInTheDocument();
});

it('Settings shows untested label and price source', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockImplementation(async (path: string) => ({
        ok: true,
        json: async () =>
          path === '/api/settings'
            ? { ...settings, price_sources: { priced: 'yours', 'unpriced-model': 'litellm' } }
            : path === '/api/health'
              ? { ...health, codex: { ...health.codex, tested: 'upstream' } }
              : kpis,
      })),
  );
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Settings />);
  expect(await screen.findByText(/untested on this PC/)).toBeInTheDocument();
  expect(screen.getByText(/LiteLLM/)).toBeInTheDocument();
});

it('Settings install importer posts', async () => {
  const fetcher = setup();
  fireEvent.click(await screen.findByRole('button', { name: 'Install long-tail importer' }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      '/api/importer/install',
      expect.objectContaining({ method: 'POST' }),
    ),
  );
});

it('run mode card is read-only and links config files', async () => {
  const options = { run_mode: true, tools: {}, projects: ['example'], profiles: [{ id: 'profile', tool: 'codex' }], defaults: {}, max_concurrent: 3 };
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path === '/api/runs/options' ? options : path === '/api/settings' ? settings : path === '/api/health' ? health : kpis })));
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  render(<Settings />);
  expect(await screen.findByText(/Turn run mode on and list projects/)).toHaveTextContent('config/run.json');
  expect(screen.getByText('Projects: example')).toBeInTheDocument();
  expect(screen.getByText('Profiles: profile (codex)')).toBeInTheDocument();
});

it('settings hosts card shows error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path === '/api/settings' ? settings : path === '/api/health' ? { ...health, hosts: { box: { state: 'partial', last_pass: 100, bytes: 42, catching_up: true, error: 'unreachable', notes: [] } } } : kpis })));
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  render(<Settings />);
  expect(await screen.findByText('unreachable')).toBeInTheDocument();
  expect(screen.getByText(/catching up/i)).toBeInTheDocument();
  expect(screen.getByText(/Hosts are listed in config\/hosts.json/)).toBeInTheDocument();
});
