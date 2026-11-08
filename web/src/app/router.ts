import { useEffect, useState } from 'react';
export type Route = 'overview' | 'agents' | 'usage' | 'insights' | 'updates' | 'new-agent' | 'sessions' | 'office' | 'dashboard' | 'limits' | 'runs' | 'settings' | 'goals' | 'memory' | 'activity' | 'proxmox' | 'router' | 'accounts' | 'account';
const ROUTES: string[] = ['overview', 'agents', 'usage', 'insights', 'updates', 'new-agent', 'sessions', 'office', 'dashboard', 'limits', 'runs', 'settings', 'goals', 'memory', 'activity', 'proxmox', 'router', 'accounts', 'account'];
export function parseRoute(hash: string): Route {
  const route = hash.replace(/^#\/?/, '').split('?')[0];
  if (route === 'inbox') return 'office'; // The Office page shows the Inbox on this address.
  if (route === 'pool') return 'router'; // The login pool was retired; agents get their model access from 9router.
  return ROUTES.includes(route) ? (route as Route) : 'overview';
}
export function useRoute(): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(location.hash));
  useEffect(() => {
    const update = () => setRoute(parseRoute(location.hash));
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  return [
    route,
    (r) => {
      location.hash = `/${r}`;
    },
  ];
}
