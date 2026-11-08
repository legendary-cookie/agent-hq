import type { Route } from '../app/router';
import { useEffect, useRef, useState } from 'react';
import type { Approval } from '../api/types';
import type { LimitCard } from '../api/types';
import { useApi } from '../api/client';
import { ROUTE_PERMISSION, useMe } from '../api/me';
import { signOut } from '../app/SignIn';
import { isDark, toggleTheme } from '../app/theme';

export function selectSidebarLimits(cards: LimitCard[], n = 4) {
  return cards
    .flatMap((card) =>
      card.windows
        .filter(
          (window) =>
            window.window !== null &&
            (window.source === 'blocked' ||
              (window.source !== 'unknown' &&
                window.used_percent !== null &&
                Number.isFinite(window.used_percent))),
        )
        .map((window) => ({
          label: card.label,
          window:
            window.source === 'blocked'
              ? window.scope && window.scope !== '*'
                ? window.scope
                : 'all models'
              : ((
                  { '5h': '5 hours', week: 'Week', day: 'Day', month: 'Month' } as Record<
                    string,
                    string
                  >
                )[window.window!] ?? window.window!),
          percent: window.used_percent,
          source: window.source,
          stale: window.stale,
        })),
    )
    .sort(
      (a, b) =>
        Number(b.source === 'blocked') - Number(a.source === 'blocked') ||
        (b.percent ?? 0) - (a.percent ?? 0),
    )
    .slice(0, n);
}

const MAIN: [Route, string][] = [['overview', 'Overview'], ['agents', 'Agents'], ['usage', 'Usage'], ['insights', 'Insights'], ['router', 'Router'], ['proxmox', 'Proxmox'], ['updates', 'Updates'], ['memory', 'Memory'], ['activity', 'Activity'], ['office', 'Office']];

export function Sidebar({ route, navigate, onRun, onSearch }: { route: Route; navigate: (r: Route) => void; onRun?: () => void; onSearch?: () => void }) {
  const { me, can } = useMe();
  const allowed = (target: Route) => can(ROUTE_PERMISSION[target]);
  const { data: options } = useApi<{ run_mode: boolean; run_key_ok?: boolean }>('/api/runs/options', { live: false });
  const { data: approvals } = useApi<Approval[]>(options?.run_mode && options.run_key_ok ? '/api/approvals?state=waiting' : null);
  const { data: branding } = useApi<{ brand: string | null; chat_url?: string | null }>('/api/brand', { live: false });
  const brand = branding?.brand || 'Agent HQ';
  const [notifications, setNotifications] = useState(false);
  const [dark, setDark] = useState(() => isDark());
  // On a phone the links fold away behind a Menu button.
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [route]);
  const seen = useRef(new Set<string>());
  useEffect(() => { document.title = brand; }, [brand]);
  useEffect(() => { const enable = () => setNotifications(true); window.addEventListener('hq-notifications-enabled', enable); return () => window.removeEventListener('hq-notifications-enabled', enable); }, []);
  useEffect(() => {
    if (!approvals) return;
    for (const item of approvals) {
      if (notifications && !seen.current.has(item.id) && !location.hash.startsWith('#/inbox') && typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification(`Run ${item.run_id}`, { body: item.tool_name });
      seen.current.add(item.id);
    }
  }, [approvals, notifications]);
  const inbox = location.hash.startsWith('#/inbox');
  const link = (target: Route, label: string) => (
    <button key={target} type="button" className={`side-link${route === target && !inbox ? ' selected' : ''}`} aria-current={route === target && !inbox ? 'page' : undefined} onClick={() => navigate(target)}>
      {label}
    </button>
  );
  return (
    <aside className="sidebar" data-open={menu || undefined}>
      <div className="brand"><span className="brand-mark" aria-hidden="true" />{brand}</div>
      <button type="button" className="side-menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>{menu ? 'Close' : 'Menu'}</button>
      <button type="button" className="side-find" onClick={onSearch}><span>Search</span><kbd>Ctrl K</kbd></button>
      <nav aria-label="Main">{MAIN.filter(([target]) => allowed(target)).map(([target, label]) => link(target, label))}</nav>
      {branding?.chat_url && <nav aria-label="Workspace"><a className="side-link" href={branding.chat_url}>Chat workspace ↗</a></nav>}
      {options?.run_mode && (
        <nav aria-label="Work">
          <div className="nav-heading">Work</div>
          <button type="button" className="side-link" onClick={onRun}>New run</button>
          {link('runs', 'Runs')}
          {link('goals', 'Goals')}
          <button type="button" className={`side-link${inbox ? ' selected' : ''}`} onClick={() => { location.hash = '/inbox'; }}>
            Inbox {approvals?.length ? <span className="nav-count">{approvals.length}</span> : null}
          </button>
        </nav>
      )}
      <div className="side-foot">
        {allowed('accounts') && link('accounts', 'Accounts')}
        {allowed('settings') && link('settings', 'Settings')}
        <button type="button" className="side-link" onClick={() => { toggleTheme(); setDark(isDark()); }}>{dark ? 'Switch to light' : 'Switch to dark'}</button>
        {me && (
          <button type="button" className={`side-link side-me${route === 'account' ? ' selected' : ''}`} aria-current={route === 'account' ? 'page' : undefined}
            title="My account: change your password" onClick={() => navigate('account')}>
            <span className="side-me-name">{me.username}</span><span className="side-me-role">{me.role_label}</span>
          </button>
        )}
        <button type="button" className="side-link" onClick={signOut}>Sign out</button>
      </div>
    </aside>
  );
}
