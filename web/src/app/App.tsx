import { useEffect, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { AlertBanner } from '../components/AlertBanner';
import { Search } from '../components/Search';
import type { LimitCard, Overview as OverviewData } from '../api/types';
import { useApi } from '../api/client';
import { useRoute } from './router';
import { SignIn } from './SignIn';
import type { Route } from './router';
import { AgentsList } from '../pages/AgentsList';
import { Overview } from '../pages/Overview';
import { Usage } from '../pages/Usage';
import { Insights } from '../pages/Insights';
import { Office } from '../pages/Office';
import { HqOffice } from '../pages/HqOffice';
import { Dashboard } from '../pages/Dashboard';
import { Agents as Sessions } from '../pages/Agents';
import { Limits } from '../pages/Limits';
import { Goals } from '../pages/Goals';
import { Runs } from '../pages/Runs';
import { RunSheet } from '../components/RunSheet';
import type { RunOptions } from '../api/types';
import { Settings } from '../pages/Settings';

const PAGES: Record<Route, () => JSX.Element> = {
  overview: Overview,
  agents: AgentsList,
  usage: Usage,
  insights: Insights,
  sessions: Sessions,
  office: Office,
  'office-3d': HqOffice,
  dashboard: Dashboard,
  limits: Limits,
  settings: Settings,
  runs: Runs,
  goals: Goals,
};
export function App() {
  const [route, navigate] = useRoute();
  const [runOpen, setRunOpen] = useState(false);
  const { data: runOptions } = useApi<RunOptions>('/api/runs/options', { live: false });
  const [forbidden, setForbidden] = useState(false);
  const [searching, setSearching] = useState(false);
  // Only the alerts, because the full overview is slow on a large database.
  const { data: overview, error } = useApi<Pick<OverviewData, 'alerts'>>('/api/alerts');
  const { data: limits } = useApi<LimitCard[]>('/api/limits');
  useEffect(() => {
    const handler = () => setForbidden(true);
    const openRun = () => setRunOpen(true);
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearching((open) => !open);
      }
    };
    window.addEventListener('hq-open-run', openRun);
    window.addEventListener('hq-forbidden', handler);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('hq-forbidden', handler); window.removeEventListener('hq-open-run', openRun); window.removeEventListener('keydown', onKey); };
  }, []);
  if (forbidden || error?.status === 403) return <SignIn />;
  const Page = PAGES[route];
  return (
    <div className="shell">
      <Sidebar route={route} navigate={navigate} onRun={() => setRunOpen(true)} onSearch={() => setSearching(true)} />
      {searching && <Search onClose={() => setSearching(false)} />}
      {runOpen && runOptions && <RunSheet options={runOptions} onClose={() => setRunOpen(false)} onStarted={() => navigate('runs')} />}
      <main>
        <AlertBanner alerts={overview?.alerts ?? []} cards={limits ?? []} />
        <Page />
      </main>
    </div>
  );
}
