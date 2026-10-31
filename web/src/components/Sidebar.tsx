import type { Route } from '../app/router';
export function Sidebar({ route, navigate }: { route: Route; navigate: (r: Route) => void }) {
  return <aside className="sidebar"><div className="brand">Agent HQ</div><nav><button disabled>Office <small>Soon</small></button>{(['dashboard','agents','limits'] as Route[]).map(r => <button key={r} className={route === r ? 'selected' : ''} onClick={() => navigate(r)}>{r[0].toUpperCase()+r.slice(1)}</button>)}<hr/><div className="mini-limits" /></nav><button className={route === 'settings' ? 'selected settings' : 'settings'} onClick={() => navigate('settings')}>Settings</button></aside>;
}
