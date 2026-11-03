import type { Route } from '../app/router';
import type { LimitCard } from '../api/types';
import { useApi } from '../api/client';
import { LimitBar } from './LimitBar';

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

export function Sidebar({ route, navigate, onRun }: { route: Route; navigate: (r: Route) => void; onRun?: () => void }) {
  const { data } = useApi<LimitCard[]>('/api/limits');
  const limits = selectSidebarLimits(data ?? []);
  return (
    <aside className="sidebar">
      <div className="brand">Agent HQ</div>
      <button onClick={onRun}>Run</button>
      <nav>
        {(['office', 'dashboard', 'agents', 'limits', 'runs'] as Route[]).map((r) => (
          <button key={r} className={route === r ? 'selected' : ''} onClick={() => navigate(r)}>
            {r[0].toUpperCase() + r.slice(1)}
          </button>
        ))}
        <hr />
        <div className="mini-limits">
          {limits.map((limit, index) => (
            <div key={`${limit.label}-${limit.window}-${index}`}>
              <div>
                {limit.label} · {limit.window}
              </div>
              <LimitBar percent={limit.percent} source={limit.source} stale={limit.stale} />
            </div>
          ))}
        </div>
      </nav>
      <button
        className={route === 'settings' ? 'selected settings' : 'settings'}
        onClick={() => navigate('settings')}
      >
        Settings
      </button>
    </aside>
  );
}
