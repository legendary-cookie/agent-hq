import { useEffect, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { AlertBanner } from '../components/AlertBanner';
import type { LimitCard, Overview } from '../api/types';
import { useApi } from '../api/client';
import { useRoute } from './router';
import { TokenHelp } from './TokenHelp';
export function App() {
  const [route, navigate] = useRoute();
  const [forbidden, setForbidden] = useState(false);
  const { data: overview, error } = useApi<Overview>('/api/overview');
  const { data: limits } = useApi<LimitCard[]>('/api/limits');
  useEffect(() => {
    const handler = () => setForbidden(true);
    window.addEventListener('hq-forbidden', handler);
    return () => window.removeEventListener('hq-forbidden', handler);
  }, []);
  if (forbidden || error?.status === 403) return <TokenHelp />;
  return (
    <div className="shell">
      <Sidebar route={route} navigate={navigate} />
      <main>
        <AlertBanner alerts={overview?.alerts ?? []} cards={limits ?? []} />
        <h1>{route[0].toUpperCase() + route.slice(1)}</h1>
      </main>
    </div>
  );
}
