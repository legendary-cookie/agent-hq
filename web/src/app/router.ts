import { useEffect, useState } from 'react';
export type Route = 'office' | 'dashboard' | 'agents' | 'limits' | 'settings';
export function parseRoute(hash: string): Route {
  const route = hash.replace(/^#\/?/, '');
  return (['office', 'dashboard', 'agents', 'limits', 'settings'] as string[]).includes(route)
    ? (route as Route)
    : 'office';
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
