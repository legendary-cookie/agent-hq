import { useEffect, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { AlertBanner } from '../components/AlertBanner';
import type { LimitCard, Overview } from '../api/types';
import { useApi } from '../api/client';
import { useRoute } from './router';
import { TokenHelp } from './TokenHelp';
import type { Route } from './router';
import { Office } from '../pages/Office';
import { Dashboard } from '../pages/Dashboard';
import { Agents } from '../pages/Agents';
import { Limits } from '../pages/Limits';
import { Runs } from '../pages/Runs';
import { RunSheet } from '../components/RunSheet';
import type { RunOptions } from '../api/types';
import { Settings } from '../pages/Settings';

const PAGES: Record<Route, () => JSX.Element> = {
  office: Office,
  dashboard: Dashboard,
  agents: Agents,
  limits: Limits,
  settings: Settings,
  runs: Runs,
};
export function App() {
  const [route, navigate] = useRoute();
  const [runOpen, setRunOpen] = useState(false);
  const { data: runOptions } = useApi<RunOptions>('/api/runs/options', { live: false });
  const [forbidden, setForbidden] = useState(false);
  const { data: overview, error } = useApi<Overview>('/api/overview');
  const { data: limits } = useApi<LimitCard[]>('/api/limits');
  useEffect(() => {
    const handler = () => setForbidden(true);
    const openRun = () => setRunOpen(true);
    window.addEventListener('hq-open-run', openRun);
    window.addEventListener('hq-forbidden', handler);
    return () => { window.removeEventListener('hq-forbidden', handler); window.removeEventListener('hq-open-run', openRun); };
  }, []);
  if (forbidden || error?.status === 403) return <TokenHelp />;
  const Page = PAGES[route];
  return (
    <div className="shell">
      <Sidebar route={route} navigate={navigate} onRun={() => setRunOpen(true)} />
      {runOpen && runOptions && <RunSheet options={runOptions} onClose={() => setRunOpen(false)} onStarted={() => navigate('runs')} />}
      <main>
        <AlertBanner alerts={overview?.alerts ?? []} cards={limits ?? []} />
        <Page />
      </main>
    </div>
  );
}
