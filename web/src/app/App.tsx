import { useEffect, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { AlertBanner } from '../components/AlertBanner';
import { Search } from '../components/Search';
import type { LimitCard, Overview as OverviewData } from '../api/types';
import { useApi } from '../api/client';
import { ROUTE_PERMISSION, useMe } from '../api/me';
import { useRoute } from './router';
import { SignIn } from './SignIn';
import type { Route } from './router';
import { AgentsList } from '../pages/AgentsList';
import { Overview } from '../pages/Overview';
import { Usage } from '../pages/Usage';
import { Insights } from '../pages/Insights';
import { Updates } from '../pages/Updates';
import { NewAgent } from '../pages/NewAgent';
import { HqOfficeRoute } from '../pages/HqOffice';
import { Dashboard } from '../pages/Dashboard';
import { Agents as Sessions } from '../pages/Agents';
import { Limits } from '../pages/Limits';
import { Goals } from '../pages/Goals';
import { Runs } from '../pages/Runs';
import { RunSheet } from '../components/RunSheet';
import type { RunOptions } from '../api/types';
import { Settings } from '../pages/Settings';
import { Memory } from '../pages/Memory';
import { Activity } from '../pages/Activity';
import { Proxmox } from '../pages/Proxmox';
import { RouterPage } from '../pages/Router';
import { Accounts } from '../pages/Accounts';
import { MyAccount } from '../pages/MyAccount';

const PAGES: Record<Route, () => JSX.Element> = {
  overview: Overview,
  agents: AgentsList,
  usage: Usage,
  insights: Insights,
  updates: Updates,
  'new-agent': NewAgent,
  sessions: Sessions,
  office: HqOfficeRoute,
  dashboard: Dashboard,
  limits: Limits,
  settings: Settings,
  runs: Runs,
  goals: Goals,
  memory: Memory,
  activity: Activity,
  proxmox: Proxmox,
  router: RouterPage,
  accounts: Accounts,
  account: MyAccount,
};
/** Where someone lands when their role can't see the Overview, in sidebar order. */
const FIRST_PAGES: Route[] = ['agents', 'usage', 'insights', 'router', 'proxmox', 'updates', 'memory', 'activity', 'office', 'settings', 'account'];

export function NoAccess() {
  return (
    <div className="hq-page">
      <h1>No access</h1>
      <p className="muted">You don’t have access to this page. Ask an HQ admin if you need it.</p>
    </div>
  );
}

export function App() {
  const [route, navigate] = useRoute();
  const { loaded, can } = useMe();
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
  const allowed = can(ROUTE_PERMISSION[route]);
  // Signed in to the default page without access to it: open the first page the role can see instead.
  const fallback = loaded && !allowed && route === 'overview' ? FIRST_PAGES.find((target) => can(ROUTE_PERMISSION[target])) : undefined;
  useEffect(() => {
    if (fallback) navigate(fallback);
  }, [fallback]);
  // A 403 that names a permission is the role's limit, not a lost sign-in.
  if (forbidden || (error?.status === 403 && !error.permission)) return <SignIn />;
  const Page = PAGES[route];
  return (
    <div className="shell">
      <Sidebar route={route} navigate={navigate} onRun={() => setRunOpen(true)} onSearch={() => setSearching(true)} />
      {searching && <Search onClose={() => setSearching(false)} />}
      {runOpen && runOptions && <RunSheet options={runOptions} onClose={() => setRunOpen(false)} onStarted={() => navigate('runs')} />}
      <main>
        <AlertBanner alerts={overview?.alerts ?? []} cards={limits ?? []} />
        {!loaded ? null : allowed ? <Page /> : fallback ? null : <NoAccess />}
      </main>
    </div>
  );
}
