import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import cards from '../test/fixtures/limits-cards.json';
import { Limits } from './Limits';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(fail = false) {
  const fetcher = vi.fn().mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return { ok: !fail, status: 400, json: async () => (fail ? { error: 'Invalid limit' } : {}) };
    }
    return { ok: true, json: async () => cards };
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal(
    'EventSource',
    class {
      addEventListener() {}
      close() {}
    },
  );
  render(<Limits />);
  return fetcher;
}

it('Limits unknown window', async () => {
  setup();
  expect(await screen.findByText('Unknown')).toBeInTheDocument();
  expect(screen.getByText('No limit data yet')).toBeInTheDocument();
  expect(document.querySelector('.limit-fill')).toBeNull();
  expect(document.body.textContent).not.toContain('NaN');
});

it('Limits manual entry posts and refreshes', async () => {
  const fetcher = setup();
  await screen.findByText('Unknown');
  fireEvent.click(screen.getByText('Set manually'));
  fireEvent.change(screen.getByLabelText('% used'), { target: { value: '82' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    account_id: 'a',
    window: '5h',
    used_percent: 82,
    resets_at: null,
  });
});

it('Limits shows server validation error', async () => {
  setup(true);
  await screen.findByText('Unknown');
  fireEvent.click(screen.getByText('Set manually'));
  fireEvent.change(screen.getByLabelText('% used'), { target: { value: '82' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save limit' }));
  expect(await screen.findByText('Invalid limit')).toBeInTheDocument();
});
