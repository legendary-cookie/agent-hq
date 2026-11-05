import { it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Goals } from './Goals';
it('goal detail shows steps and diff', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path.endsWith('/diff') ? {diff: '+result', commit:'abc'} : path === '/api/goals' ? [{id:'g', status:'done', text_preview:'Fix login', project:'app', steps:{s1:{status:'done',run_id:'r'}}}] : path === '/api/runs/options' ? {projects:[],tools:{}} : {id:'g',status:'done',branch:'branch',plan_json: JSON.stringify({steps:[{id:'s1',title:'Step',prompt:'work',kind:'code',depends_on:[],tool:null}]}), steps:{s1:{status:'done',run_id:'r'}}} })));
  render(<Goals />);
  expect(await screen.findByText('1/1 done')).toBeInTheDocument();
  fireEvent.click(await screen.findByText('Fix login'));
  expect(await screen.findByText('s1 · Step')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'View run' })).toHaveAttribute('href', '#/runs?run=r'); expect(await screen.findByText(/\+result/)).toBeInTheDocument();
});
