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
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  expect(JSON.parse(fetcher.mock.calls[3][1].body).sources).toEqual(settings.sources);
  expect(await screen.findByText('Saved')).toBeInTheDocument();
});

it('Settings shows 400 message', async () => {
  setup(true);
  await screen.findByDisplayValue('Codex');
  fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  expect(await screen.findByText('Invalid settings')).toBeInTheDocument();
});
