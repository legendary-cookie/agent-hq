import { Inbox } from './Inbox';
import { makeSyntheticSessions, syntheticCount } from '../office/synthetic';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useApi } from '../api/client';
import type { Approval, DelegationRow, Overview, SessionRow, Settings } from '../api/types';
import { Inspector } from '../components/Inspector';
import { Segmented } from '../components/Segmented';
import { StatusPills } from '../components/StatusPills';
import { harnessLabel } from '../office/colors';
import { PixiStage } from '../office/PixiStage';
import { buildScene } from '../office/scene';
import type { Scene } from '../office/scene';
import type { Skin, SkinId } from '../office/skin';
import { parseSkin, SKINS } from '../office/skins';
import { replaySessions, replayStart, replayTime } from '../office/timeline';
import type { TimelineEvent } from '../office/timeline';
import { resetLayout } from '../office/layout-editor';
import './office.css';

export function Office(props: { skinFactory?: (id: SkinId) => Promise<Skin> } = {}) {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => { const update = () => setHash(location.hash); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  return hash.startsWith('#/inbox') ? <Inbox /> : <OfficeView {...props} />;
}
function OfficeView({ skinFactory }: { skinFactory?: (id: SkinId) => Promise<Skin> } = {}) {
  const { data: sessions } = useApi<SessionRow[]>('/api/sessions');
  const { data: delegations } = useApi<DelegationRow[]>('/api/delegations?limit=20');
  const { data: overview } = useApi<Overview>('/api/overview');
  const { data: options } = useApi<{ run_mode: boolean; run_key_ok?: boolean }>('/api/runs/options', { live: false });
  const { data: approvals } = useApi<Approval[]>(options?.run_mode && options.run_key_ok ? '/api/approvals?state=waiting' : null);
  const { data: settings } = useApi<Settings>('/api/settings', { live: false });
  const [skin, setSkin] = useState<SkinId>(() => {
    try {
      return parseSkin(localStorage.getItem('hq-skin'), 'isometric');
    } catch {
      return 'isometric';
    }
  });
  const chosen = useRef(false);
  const [harness, setHarness] = useState('');
  const [account, setAccount] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [following, setFollowing] = useState<string | null>(null);
  const [replayAt, setReplayAt] = useState<number | null>(null);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [replayEnd] = useState(() => Date.now() / 1000);
  const { data: timeline } = useApi<{ events: TimelineEvent[]; truncated: boolean }>(replayAt === null ? null : `/api/timeline?from=${replayEnd - 86400}&to=${replayEnd}`, { live: false });
  useEffect(() => { const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setFollowing(null); }; window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key); }, []);
  // Once the timeline arrives, move a just-opened replay to its first event.
  useEffect(() => { if (timeline && replayAt === replayEnd - 86400) setReplayAt(replayStart(timeline.events, replayEnd - 86400)); }, [timeline]);
  useEffect(() => { if (!playing || replayAt === null) return; const timer = window.setInterval(() => setReplayAt(value => value === null ? null : replayTime(value, 0.1, speed, replayEnd)), 100); return () => window.clearInterval(timer); }, [playing, replayAt === null, speed, replayEnd]);
  const [tickerId, setTickerId] = useState<string | null>(null);
  const memory = useRef(new Map<string, string>());
  const previous = useRef<Scene | undefined>(undefined);
  useEffect(() => {
    if (!settings || chosen.current) return;
    try {
      if (localStorage.getItem('hq-skin')) return;
    } catch {
      /* storage unavailable */
    }
    setSkin(parseSkin(settings.default_skin, 'isometric'));
  }, [settings]);
  // Dev-only performance check: `?synthetic=60` replaces live sessions with fakes.
  const synthetic = import.meta.env.DEV ? syntheticCount(location.search) : 0;
  const allSessions = useMemo(
    () => { const base = synthetic ? makeSyntheticSessions(synthetic, Date.now() / 1000) : (sessions ?? []); return replayAt === null ? base : replaySessions(base, timeline?.events ?? [], replayAt); },
    [sessions, synthetic, replayAt, timeline],
  );
  const fullScene = useMemo(() => {
    const loaded = sessions !== null && delegations !== null;
    const next = buildScene(
      allSessions,
      delegations ?? [],
      memory.current,
      replayAt ?? Date.now() / 1000,
      loaded ? previous.current : undefined,
    );
    if (loaded) previous.current = next;
    return next;
  }, [allSessions, sessions, delegations, replayAt]);
  const scene = useMemo(() => {
    const visible = new Set(
      allSessions
        .filter(
          (s) => (!harness || s.harness === harness) && (!account || s.account_id === account),
        )
        .map((s) => s.id),
    );
    return {
      ...fullScene,
      npcs: fullScene.npcs.filter((npc) => visible.has(npc.sessionId)),
      walks: fullScene.walks.filter((walk) => visible.has(walk.npcId)),
    };
  }, [allSessions, fullScene, harness, account]);
  const latest = [...(delegations ?? [])].sort((a, b) => b.ts - a.ts)[0];
  useEffect(() => {
    if (!latest || Date.now() / 1000 - latest.ts > 60 || latest.ts > Date.now() / 1000) return;
    setTickerId(latest.id);
    const timer = window.setTimeout(() => setTickerId(null), 6000);
    return () => window.clearTimeout(timer);
  }, [latest?.id, latest?.ts]);
  const showTicker = latest && tickerId === latest.id;
  const selectSkin = (value: SkinId) => {
    chosen.current = true;
    setSkin(value);
    setEditing(false);
    try {
      localStorage.setItem('hq-skin', value);
    } catch {
      /* storage unavailable */
    }
  };
  return (
    <div className="office-layout">
      <div className="office-page">
        <header className="office-header">
          <h1>Office</h1>
          <Segmented
            value={skin}
            onChange={selectSkin}
            options={(Object.keys(SKINS) as SkinId[]).map((id) => ({
              value: id,
              label: SKINS[id].label,
            }))}
          />
        </header>
        {overview && <StatusPills overview={overview} />}
        <div className="office-filters">
          <label>
            Harness{' '}
            <select value={harness} onChange={(e) => setHarness(e.target.value)}>
              <option value="">All harnesses</option>
              {[...new Set((sessions ?? []).map((s) => s.harness).filter((v): v is string => !!v))]
                .sort()
                .map((v) => (
                  <option key={v}>{v}</option>
                ))}
            </select>
          </label>
          <label>
            Account{' '}
            <select value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">All accounts</option>
              {[
                ...new Set(
                  (sessions ?? []).map((s) => s.account_id).filter((v): v is string => !!v),
                ),
              ]
                .sort()
                .map((v) => (
                  <option key={v}>{v}</option>
                ))}
            </select>
          </label>
        </div>
        <div className="office-filters">
          <button type="button" onClick={() => { setReplayAt(replayEnd - 86400); setPlaying(false); }}>Replay</button>
          {replayAt !== null && <>
            <input aria-label="Timeline" type="range" min={replayEnd - 86400} max={replayEnd} step="1" value={replayAt} onChange={event => setReplayAt(Number(event.target.value))} />
            <time>{new Date(replayAt * 1000).toLocaleString()}</time>
            <button type="button" onClick={() => setPlaying(!playing)}>{playing ? 'Pause' : 'Play'}</button>
            <select aria-label="Replay speed" value={speed} onChange={event => setSpeed(Number(event.target.value))}><option value={1}>1×</option><option value={10}>10×</option><option value={60}>60×</option></select>
            <button type="button" onClick={() => { setReplayAt(null); setPlaying(false); }}>Live</button>
            {timeline?.truncated && <span>Timeline truncated</span>}
          </>}
          {skin !== 'tower' && <button type="button" aria-pressed={editing} onClick={() => { setEditing(!editing); window.dispatchEvent(new Event('hq-layout-edit')); }}>{editing ? 'Done editing' : 'Edit layout'}</button>}
          {editing && <span>Drag desks or zone labels; double-click a zone label to rename it.</span>}
          <button type="button" onClick={() => { resetLayout(skin); window.dispatchEvent(new Event('hq-layout-reset')); }}>Reset layout</button>
        </div>
        <div className="office-stage">
          {sessions && (
            <PixiStage
              skin={skin}
              scene={scene}
              selectedId={selected}
              followId={following}
              onPick={(id) => { const session = allSessions.find((item) => item.id === id); const approval = approvals?.find((item) => item.run_id === session?.launched_run_id); if (approval) location.hash = `/inbox?approval=${encodeURIComponent(approval.id)}`; else setSelected(id); }}
              skinFactory={skinFactory}
            />
          )}
          {!scene.npcs.length && <div className="office-empty">{replayAt === null ? 'No agents in the last 24 hours' : 'No agents at this moment'}</div>}
          {showTicker && (
            <div className="office-ticker">
              {new Date(latest.ts * 1000).toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
                hourCycle: 'h23',
              })}{' '}
              · delegated {latest.task_kind} to {latest.provider} · {latest.model}
            </div>
          )}
        </div>
        <ul className="office-accessible" aria-label="Office agents">
          {scene.npcs.map((npc) => (
            <li key={npc.id}>
              <button type="button" onClick={() => { const item = approvals?.find((entry) => entry.run_id === npc.launchedRunId); if (item) location.hash = `/inbox?approval=${encodeURIComponent(item.id)}`; else setSelected(npc.sessionId); }}>
                {harnessLabel(npc.harness)} ·{' '}
                {approvals?.some((item) => item.run_id === npc.launchedRunId) ? 'Needs you' : npc.state === 'active' ? 'Active (imported)' : npc.state} · {npc.title}{npc.launchedRunId && ' · launched by HQ'}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {selected && <button type="button" onClick={() => setFollowing(following === selected ? null : selected)}>{following === selected ? 'Stop following' : 'Follow'}</button>}
      <Inspector sessionId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
