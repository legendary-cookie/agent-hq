import { useEffect, useState } from 'react';
export type Route = 'overview' | 'agents' | 'usage' | 'insights' | 'sessions' | 'office' | 'office-3d' | 'dashboard' | 'limits' | 'runs' | 'settings' | 'goals';
const ROUTES: string[] = ['overview', 'agents', 'usage', 'insights', 'sessions', 'office', 'office-3d', 'dashboard', 'limits', 'runs', 'settings', 'goals'];
export function parseRoute(hash: string): Route {
  const route = hash.replace(/^#\/?/, '').split('?')[0];
  if (route === 'inbox') return 'office'; // The Office page shows the Inbox on this address.
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
