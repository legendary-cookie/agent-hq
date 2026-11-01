import { useEffect, useMemo, useRef, useState } from 'react';
import { useApi } from '../api/client';
import type { DelegationRow, Overview, SessionRow, Settings } from '../api/types';
import { Inspector } from '../components/Inspector';
import { Segmented } from '../components/Segmented';
import { StatusPills } from '../components/StatusPills';
import { PixiStage } from '../office/PixiStage';
import { buildScene } from '../office/scene';
import type { Scene } from '../office/scene';
import type { Skin, SkinId } from '../office/skin';
import { parseSkin, SKINS } from '../office/skins';
import './office.css';

export function Office({ skinFactory }: { skinFactory?: (id: SkinId) => Promise<Skin> } = {}) {
  const { data: sessions } = useApi<SessionRow[]>('/api/sessions');
  const { data: delegations } = useApi<DelegationRow[]>('/api/delegations?limit=20');
  const { data: overview } = useApi<Overview>('/api/overview');
  const { data: settings } = useApi<Settings>('/api/settings');
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
  const filtered = useMemo(
    () =>
      (sessions ?? []).filter(
        (s) => (!harness || s.harness === harness) && (!account || s.account_id === account),
      ),
    [sessions, harness, account],
  );
  const scene = useMemo(() => {
    const next = buildScene(
      filtered,
      delegations ?? [],
      memory.current,
      Date.now() / 1000,
      previous.current,
    );
    previous.current = next;
    return next;
  }, [filtered, delegations]);
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
        <div className="office-stage">
          {sessions && (
            <PixiStage
              skin={skin}
              scene={scene}
              selectedId={selected}
              onPick={setSelected}
              skinFactory={skinFactory}
            />
          )}
          {!scene.npcs.length && <div className="office-empty">No agents in the last 24 hours</div>}
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
              <button type="button" onClick={() => setSelected(npc.sessionId)}>
                {npc.harness} · {npc.state} · {npc.title}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <Inspector sessionId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
