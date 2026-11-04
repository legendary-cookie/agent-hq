import { it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Goals } from './Goals';
it('goal detail shows steps and diff', async () => {
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => ({ ok: true, json: async () => path.endsWith('/diff') ? {diff: '+result', commit:'abc'} : path === '/api/goals' ? [{id:'g', status:'done', text_preview:'Goal'}] : path === '/api/runs/options' ? {projects:[],tools:{}} : {id:'g',status:'done',branch:'branch',plan_json: JSON.stringify({steps:[{id:'s1',title:'Step',prompt:'work',kind:'code',depends_on:[],tool:null}]}), steps:{s1:{status:'done',run_id:'r'}}} })));
  render(<Goals />); fireEvent.click(await screen.findByText('Goal')); expect(await screen.findByText(/Step · done/)).toBeInTheDocument(); expect(await screen.findByText(/\+result/)).toBeInTheDocument();
});
