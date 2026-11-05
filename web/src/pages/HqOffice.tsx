import { useEffect, useMemo, useRef, useState } from 'react';
import '@fontsource/fredoka/400.css';
import '@fontsource/fredoka/500.css';
import '@fontsource/fredoka/600.css';
import { useApi } from '../api/client';
import type { Fleet } from '../api/types';
import { fmtRelative } from '../lib/format';
import type { OfficeEngine, Pick3D } from '../hq-office/engine3d';
import { CHARACTERS, DEFAULTS, STATUS_TEXT, WORK, canSeatByDepartment, computeRooms, displayName, feedText, homeRoom, loadSettings, place, saveSettings, topRoomName, words } from '../hq-office/model';
import type { OfficeData, Placed, Settings } from '../hq-office/model';
import { THEMES } from '../hq-office/themes';
import { AgentSheet } from './AgentSheet';
import './fleet.css';
import './hq-office.css';

const REFRESH_MS = 15000;
const FLAT_STYLES = [['cute', 'Cute'], ['studio', 'Studio'], ['night', 'Night shift'], ['blueprint', 'Blueprint']] as const;
const CAMERAS = [['Corner', { az: .62, el: .82 }], ['Front', { az: 0, el: .62 }], ['Top', { az: 0, el: 1.5 }], ['Other side', { az: -.62, el: .82 }]] as const;

function Seg<T extends string>({ label, options, value, onChange }: { label: string; options: readonly (readonly [T, string])[]; value: T; onChange: (value: T) => void }) {
  return (
    <div className="hqo-seg" role="group" aria-label={label}>
      {options.map(([key, text]) => <button type="button" key={key} aria-pressed={value === key} onClick={() => onChange(key)}>{text}</button>)}
    </div>
  );
}

export function HqOffice() {
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const { data, error, reload } = useApi<OfficeData>('/api/office');
  const latest = useRef<OfficeData | null>(null);
  if (data) latest.current = data;
  const office = latest.current;
  const [tab, setTab] = useState<'now' | 'look' | 'item'>('now');
  const [picked, setPicked] = useState<Exclude<Pick3D, null> | null>(null);
  const [details, setDetails] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [hint, setHint] = useState(true);
  const [noGl, setNoGl] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const canvas3d = useRef<HTMLCanvasElement>(null);
  const canvasFlat = useRef<HTMLCanvasElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const engine = useRef<OfficeEngine | null>(null);
  const seen = useRef<Set<number> | null>(null);
  const toastTimer = useRef(0);
  const placed = useMemo(() => (office ? place(office, settings) : []), [office, settings]);
  const placedRef = useRef(placed);
  placedRef.current = placed;
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const view = noGl ? 'flat' : settings.view;
  const { data: fleet } = useApi<Fleet>(details ? '/api/fleet?range=today' : null, { live: false });

  const change = (patch: Partial<Settings>) => setSettings((old) => { const next = { ...old, ...patch }; saveSettings(next); return next; });
  const say = (text: string) => { setToast(text); window.clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), 2600); };

  // New answers keep arriving when the page's data changes, and the clock moves things on regardless.
  useEffect(() => { const timer = window.setInterval(reload, REFRESH_MS); return () => window.clearInterval(timer); }, [reload]);
  useEffect(() => { const timer = window.setTimeout(() => setHint(false), 12000); return () => window.clearTimeout(timer); }, []);

  // The drawing code is loaded when needed, so other pages don't pay for three.js.
  useEffect(() => {
    let cancelled = false;
    const events = {
      onPick: (item: Pick3D) => { setPicked(item); if (item) setTab('item'); },
      onMove: (host: string, room: string) => {
        const old = settingsRef.current;
        change({ agentRoom: { ...old.agentRoom, [host]: room } });
      },
      onToast: say,
    };
    const start = async () => {
      if (!stage.current) return;
      try {
        if (view === '3d') {
          const { createOffice3D } = await import('../hq-office/engine3d');
          if (cancelled || !canvas3d.current || !overlay.current || !stage.current) return;
          engine.current = createOffice3D(canvas3d.current, overlay.current, stage.current, settingsRef.current, events);
        } else {
          const { createOfficeFlat } = await import('../hq-office/engineFlat');
          if (cancelled || !canvasFlat.current || !stage.current) return;
          engine.current = createOfficeFlat(canvasFlat.current, stage.current, settingsRef.current, events);
        }
        if (placedRef.current.length) engine.current.update(placedRef.current);
      } catch (problem) {
        console.error(problem);
        if (view === '3d' && !cancelled) { setNoGl(true); say('3D isn’t available in this browser, so this is the flat view'); }
      }
    };
    void start();
    return () => { cancelled = true; engine.current?.dispose(); engine.current = null; };
  }, [view]);

  useEffect(() => { engine.current?.configure(settings); }, [settings]);
  useEffect(() => { if (placed.length) engine.current?.update(placed); }, [placed]);
  useEffect(() => { engine.current?.select(picked?.type === 'agent' ? picked.host : null); }, [picked]);

  // Only exchanges that really happened fly, once each, spread out so a burst stays readable.
  useEffect(() => {
    if (!office) return;
    if (!seen.current) { seen.current = new Set(office.exchanges.map((item) => item.id)); return; }
    const fresh = office.exchanges.filter((item) => !seen.current!.has(item.id)).slice(-12);
    const timers = fresh.map((item, i) => {
      seen.current!.add(item.id);
      return window.setTimeout(() => engine.current?.fly(item.from, item.to), i * (REFRESH_MS / Math.max(fresh.length, 1)));
    });
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [office]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setPicked(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const names = useMemo(() => new Map(placed.map((a) => [a.host, displayName(a, settings)])), [placed, settings]);
  const nameOf = (host: string) => names.get(host) ?? host;
  const counts = { working: 0, meeting: 0, break: 0, archive: 0, quiet: 0, attention: 0, idle: 0 };
  for (const a of placed) counts[a.shown] += 1;
  const now = office?.now ?? Date.now() / 1000;
  const fleetAgent = details ? fleet?.agents.find((a) => a.host === details) : undefined;

  return (
    <div className="hqo hq-page">
      <header className="hqo-head">
        <h1>3D office</h1>
        <div className="hqo-tools">
          {!noGl && <Seg label="View" options={[['3d', '3D'], ['flat', 'Flat']] as const} value={settings.view} onChange={(value) => change({ view: value })} />}
          <Seg label="Detail" options={[['full', 'Full'], ['privacy', 'Privacy']] as const} value={settings.detail} onChange={(value) => change({ detail: value })} />
          {view === '3d' && <Seg label="Time of day" options={[['auto', 'Auto'], ['day', 'Day'], ['golden', 'Sunset'], ['night', 'Night']] as const} value={settings.time} onChange={(value) => change({ time: value })} />}
        </div>
      </header>
      <div className="hqo-body">
        <div className="hqo-stage" ref={stage}>
          <canvas ref={canvas3d} className="hqo-gl" key={`gl-${view}`} hidden={view !== '3d'} role="img" aria-label="3D office showing each agent at a desk, in a meeting, on a break or in the archives" />
          <div ref={overlay} className="hqo-overlay" hidden={view !== '3d'} />
          <canvas ref={canvasFlat} className="hqo-flat" key={`flat-${view}`} hidden={view !== 'flat'} role="img" aria-label="Floor plan showing each agent at a desk, in a meeting or on a break" />
          {view === '3d' && hint && <div className="hqo-hint">Drag to turn. Scroll to zoom. Drag an agent to another room.</div>}
          {view === '3d' && (
            <div className="hqo-camera" role="group" aria-label="Camera">
              {CAMERAS.map(([label, position]) => <button type="button" key={label} onClick={() => engine.current?.camera?.(position)}>{label}</button>)}
              <button type="button" aria-label="Zoom in" onClick={() => engine.current?.camera?.('in')}>+</button>
              <button type="button" aria-label="Zoom out" onClick={() => engine.current?.camera?.('out')}>−</button>
            </div>
          )}
          {!office && <div className="hqo-empty">{error ? 'Couldn’t load the office. Trying again shortly.' : 'Opening the office…'}</div>}
          {office && !office.agents.length && <div className="hqo-empty">No agents connected yet</div>}
          {toast && <div className="hqo-toast" role="status">{toast}</div>}
        </div>
        <aside className="hqo-side">
          <div className="hqo-tabs" role="tablist">
            {([['now', 'Activity'], ['look', 'Customize'], ['item', 'Selected']] as const).map(([key, text]) => (
              <button type="button" role="tab" key={key} aria-selected={tab === key} onClick={() => setTab(key)}>{text}</button>
            ))}
          </div>
          {tab === 'now' && (
            <div className="hqo-tab">
              <div className="hqo-tally" aria-label="Summary">
                <div><b>{counts.working}</b><small>Working</small></div>
                <div><b>{counts.meeting}</b><small>In a meeting</small></div>
                <div><b>{counts.break}</b><small>On a break</small></div>
                <div><b>{counts.archive}</b><small>In archives</small></div>
                <div><b>{counts.quiet}</b><small>Quiet</small></div>
                <div><b>{counts.attention}</b><small>Need help</small></div>
              </div>
              <h2>Happening now</h2>
              {office?.feed.length ? (
                <ul className="hqo-feed">
                  {office.feed.map((entry, i) => (
                    <li key={`${entry.at}-${i}`}>
                      <time>{new Date(entry.at * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</time>
                      <span>{feedText(entry, nameOf, settings)}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="hqo-note">Nothing in the last 6 hours.</p>}
              <p className="hqo-note">Agents report every 1 to 5 minutes, so this runs slightly behind. Who is talking to whom is worked out from message times.</p>
            </div>
          )}
          {tab === 'look' && <Customize settings={settings} office={office} view={view} change={change} onReset={() => { const fresh = { ...DEFAULTS }; saveSettings(fresh); setSettings(fresh); setPicked(null); say('Back to the default office'); }} />}
          {tab === 'item' && <Selected picked={picked} placed={placed} settings={settings} office={office} now={now} names={names} change={change} onDetails={setDetails} onHidden={() => { setPicked(null); setTab('look'); }} />}
        </aside>
      </div>
      {details && fleetAgent && <AgentSheet agent={fleetAgent} periodLabel="24 hours" onClose={() => setDetails(null)} />}
    </div>
  );
}

function Customize({ settings, office, view, change, onReset }: { settings: Settings; office: OfficeData | null; view: string; change: (patch: Partial<Settings>) => void; onReset: () => void }) {
  const hiddenRooms = Object.keys(settings.hidden).filter((id) => settings.hidden[id]);
  const toggle = (label: string, key: 'names' | 'roomLabels' | 'walk' | 'naps') => (
    <label className="hqo-row"><span>{label}</span><input type="checkbox" checked={settings[key]} onChange={(event) => change({ [key]: event.target.checked })} /></label>
  );
  return (
    <div className="hqo-tab">
      <h2>Style</h2>
      {view === '3d' ? (
        <div className="hqo-themes">
          {Object.entries(THEMES).map(([key, t]) => (
            <button type="button" className="hqo-theme" key={key} aria-pressed={settings.theme === key} onClick={() => change({ theme: key })}>
              <span className="hqo-swatches">{[t.floors[0], t.floors[4], t.wood, t.bodies[0], t.bodies[1]].map((color, i) => <i key={i} style={{ background: color }} />)}</span>
              {t.label}
            </button>
          ))}
        </div>
      ) : <Seg label="Style" options={FLAT_STYLES} value={settings.flatStyle as typeof FLAT_STYLES[number][0]} onChange={(value) => change({ flatStyle: value })} />}
      {view === '3d' && <>
        <h2>Characters</h2>
        <Seg label="Characters" options={Object.entries(CHARACTERS) as [string, string][]} value={settings.character} onChange={(value) => change({ character: value })} />
        <h2>Walls</h2>
        <Seg label="Walls" options={[['low', 'Low'], ['mid', 'Medium'], ['high', 'High']] as const} value={settings.wallHeight} onChange={(value) => change({ wallHeight: value })} />
      </>}
      {office && canSeatByDepartment(office.agents) && <>
        <h2>Where staff sit</h2>
        <Seg label="Where staff sit" options={[['staff', 'Staff room'], ['departments', 'With their department']] as const} value={settings.seating} onChange={(value) => change({ seating: value })} />
      </>}
      {view === '3d' && <p className="hqo-note">To move one agent, drag it to another room.</p>}
      <h2>Speech bubbles</h2>
      <Seg label="Speech bubbles" options={[['busy', 'Busy agents'], ['all', 'Everyone'], ['off', 'Off']] as const} value={settings.bubbles} onChange={(value) => change({ bubbles: value })} />
      <h2>Show</h2>
      {toggle('Agent names', 'names')}
      {toggle('Room names', 'roomLabels')}
      {toggle('Agents walk around', 'walk')}
      {toggle('Idle agents nap', 'naps')}
      {hiddenRooms.length > 0 && <h2>Hidden rooms</h2>}
      {hiddenRooms.map((id) => (
        <div className="hqo-row" key={id}><span>{topRoomName(id)}</span><button type="button" className="hqo-plain" onClick={() => { const hidden = { ...settings.hidden }; delete hidden[id]; change({ hidden }); }}>Show</button></div>
      ))}
      <h2>Start over</h2>
      <button type="button" className="hqo-plain" onClick={onReset}>Reset everything to default</button>
      <p className="hqo-note">Your choices are remembered in this browser. They change how the office looks, not the agents.</p>
    </div>
  );
}

function ColorRow({ label, value, onChange, onClear }: { label: string; value: string; onChange: (value: string) => void; onClear: () => void }) {
  return (
    <div className="hqo-row">
      <span>{label}</span>
      <div className="hqo-tools">
        <button type="button" className="hqo-link" onClick={onClear}>Use default</button>
        <input type="color" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} />
      </div>
    </div>
  );
}

function omit(record: Record<string, string>, key: string) { const copy = { ...record }; delete copy[key]; return copy; }

function Selected({ picked, placed, settings, office, now, names, change, onDetails, onHidden }: { picked: Exclude<Pick3D, null> | null; placed: Placed[]; settings: Settings; office: OfficeData | null; now: number; names: Map<string, string>; change: (patch: Partial<Settings>) => void; onDetails: (host: string) => void; onHidden: () => void }) {
  const rooms = useMemo(() => (office ? computeRooms(office.agents, settings) : {}), [office, settings]);
  if (!picked) return <div className="hqo-tab"><p className="hqo-note">Click any agent or room in the office to see and change it here.</p></div>;
  if (picked.type === 'agent') {
    const a = placed.find((item) => item.host === picked.host);
    if (!a) return <div className="hqo-tab"><p className="hqo-note">This agent is no longer listed.</p></div>;
    const secret = settings.detail === 'privacy';
    const theme = THEMES[settings.theme] ?? THEMES.toybox;
    const seatable = Object.values(rooms).filter((room) => room.id === 'mgmt' || room.id === 'staff' || room.dept);
    return (
      <div className="hqo-tab" key={a.host}>
        <h3>{displayName(a, settings)}</h3>
        <div className="muted">{settings.names ? [a.owner && a.owner !== a.label ? `${a.owner}’s agent` : null, a.group === 'Department' ? 'Department agent' : a.group].filter(Boolean).join(' · ') : 'Name hidden'}</div>
        <div className="hqo-status"><span className={`hqo-dot ${['working', 'meeting', 'archive'].includes(a.shown) ? 'on' : a.shown === 'attention' ? 'bad' : ''}`} />{STATUS_TEXT[a.shown]}{a.last_seen ? <span className="muted">· {fmtRelative(a.last_seen, now)}</span> : null}</div>
        <div className="hqo-now"><small>Right now</small><span>{words(a, settings, names)}</span></div>
        {a.earlier.length > 0 && <>
          <h2>Earlier</h2>
          {a.earlier.map((item) => (
            <div className="hqo-row hqo-earlier" key={`${item.kind}-${item.at}`}><span>{secret || !item.title ? WORK[item.kind] : item.title}</span><span className="muted">{fmtRelative(item.at, now)}</span></div>
          ))}
        </>}
        <button type="button" className="hqo-plain hqo-wide" onClick={() => onDetails(a.host)}>Open usage and sessions</button>
        <h2>Change how it looks here</h2>
        <label className="hqo-field"><span>Display name</span><input type="text" maxLength={22} placeholder={a.label} value={settings.agentNames[a.host] ?? ''} onChange={(event) => change({ agentNames: { ...settings.agentNames, [a.host]: event.target.value } })} /></label>
        <label className="hqo-field"><span>Sits in</span>
          <select value={homeRoom(a, settings)} onChange={(event) => change({ agentRoom: { ...settings.agentRoom, [a.host]: event.target.value } })}>
            {seatable.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}
          </select>
        </label>
        <label className="hqo-field"><span>Look</span>
          <select value={settings.agentChars[a.host] ?? ''} onChange={(event) => change({ agentChars: event.target.value ? { ...settings.agentChars, [a.host]: event.target.value } : omit(settings.agentChars, a.host) })}>
            <option value="">Same as everyone</option>
            {Object.entries(CHARACTERS).filter(([key]) => key !== 'mix').map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <ColorRow label="Colour" value={settings.agentColors[a.host] || theme.bodies[a.index % theme.bodies.length]} onChange={(value) => change({ agentColors: { ...settings.agentColors, [a.host]: value } })} onClear={() => change({ agentColors: omit(settings.agentColors, a.host) })} />
      </div>
    );
  }
  const room = rooms[picked.id];
  if (!room) return <div className="hqo-tab"><p className="hqo-note">This room is hidden.</p></div>;
  const theme = THEMES[settings.theme] ?? THEMES.toybox;
  const here = placed.filter((a) => (room.id === 'meet' && a.where === 'meeting') || (room.id === 'lounge' && a.where === 'lounge') || (room.id === 'archives' && a.where === 'archive') || (a.where === 'desk' && room.desks.some((desk) => desk.owner === a.host)));
  return (
    <div className="hqo-tab" key={room.id}>
      <h3>{room.name}</h3>
      <p className="hqo-note">{room.id === 'archives' ? 'Agents come here while they use a Google Drive tool. No Drive files are read or shown here.' : room.id === 'systems' ? 'A decorative room. Its lights don’t reflect any real system.' : `${here.length} ${here.length === 1 ? 'agent' : 'agents'} here now.`}</p>
      <h2>Change this room</h2>
      <label className="hqo-field"><span>Name</span><input type="text" maxLength={26} placeholder={room.base} value={settings.roomNames[room.id] ?? ''} onChange={(event) => change({ roomNames: { ...settings.roomNames, [room.id]: event.target.value } })} /></label>
      <ColorRow label="Floor colour" value={settings.roomColors[room.id] || theme.floors[room.tint % theme.floors.length]} onChange={(value) => change({ roomColors: { ...settings.roomColors, [room.id]: value } })} onClear={() => change({ roomColors: omit(settings.roomColors, room.id) })} />
      {room.top && room.id !== 'mgmt' && <button type="button" className="hqo-plain hqo-wide" onClick={() => { change({ hidden: { ...settings.hidden, [room.id]: true } }); onHidden(); }}>Hide this room</button>}
    </div>
  );
}
