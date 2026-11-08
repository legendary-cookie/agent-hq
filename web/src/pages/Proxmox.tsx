/** The Proxmox side: cluster and nodes, what's used and what's left, storage, every guest, and backups. */
import { useState } from 'react';
import { useApi } from '../api/client';
import { Segmented } from '../components/Segmented';
import { fmtDuration, fmtRelative } from '../lib/format';
import { AgentSheet, useOpenAgent } from './AgentSheet';
import { useFleet } from './fleet-shared';
import { GraphsToggle, TrendChart, useGraphs } from './Trends';
import './fleet.css';
import './control.css';
import './proxmox.css';

export type InfraNode = {
  name: string; status: string; uptime: number; cpu: number; cpus: number; cpu_model?: string | null; sockets?: number | null; load: number[];
  mem: number; maxmem: number; swap: number; maxswap: number; rootfs: number; maxrootfs: number; pve?: string | null; kernel?: string | null;
  guests: number; running: number; allocated_mem: number; allocated_cpus: number; running_mem: number;
  unallocated_mem: number; free_mem: number; free_disk: number; room_for_agents: number | null;
  series: { start: number; bucket_s: number; count: number; cpu_pct: (number | null)[]; mem_pct: (number | null)[] } | null;
};
export type InfraStorage = { id: string; node: string | null; shared: boolean; type?: string | null; status?: string | null; used: number; total: number; content: string[] };
export type InfraGuest = {
  vmid: number; name: string; type: 'lxc' | 'qemu'; node: string; status: string; uptime: number; cpu: number; cpus: number;
  mem: number; maxmem: number; disk: number; maxdisk: number; netin: number; netout: number; template: boolean; tags: string[];
  host: string | null; label: string; last_backup: number | null; backups: number;
};
export type InfraSnapshot = {
  at: number;
  cluster: { name: string | null; is_cluster: boolean; quorate: boolean; nodes: number; online: number; version: string | null };
  totals: { cpus: number; cpu: number; mem: number; maxmem: number; allocated_mem: number; allocated_cpus: number; storage_used: number; storage_total: number;
    guests: number; running: number; room_for_agents: number | null; typical_agent: { mem: number | null; disk: number | null } };
  nodes: InfraNode[]; storage: InfraStorage[]; guests: InfraGuest[]; hidden: number;
  backups: { read: boolean; at: number | null; jobs: { id: string; schedule: string | null; enabled: boolean; storage: string | null; mode: string | null; all: boolean; vmids: string[]; node: string | null; comment: string | null }[];
    failed: { at: number; node: string; vmid: string | number | null; status: string }[] };
  errors: string[];
};
export type InfraData = { configured: boolean; snapshot: InfraSnapshot | null; error: string | null };

export function bytes(n: number) {
  if (!n) return '0 GB';
  const gb = n / 1024 ** 3;
  return gb >= 1024 ? `${(gb / 1024).toFixed(gb >= 10240 ? 0 : 1)} TB` : gb >= 10 ? `${Math.round(gb)} GB` : `${gb.toFixed(1)} GB`;
}
const pct = (used: number, total: number) => (total ? Math.round((used / total) * 100) : 0);

export function Meter({ label, used, total, text }: { label: string; used: number; total: number; text?: string }) {
  const p = pct(used, total);
  return (
    <div className="px-meter">
      <span className="px-meter-label">{label}</span>
      <span className="px-bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={p}><i className={p >= 90 ? 'bad' : p >= 75 ? 'warn' : ''} style={{ width: `${Math.min(100, p)}%` }} /></span>
      <span className="num px-meter-text">{text ?? `${bytes(used)} of ${bytes(total)}`} <span className="muted">{p}%</span></span>
    </div>
  );
}

/** How long ago the last backup was, in words, and whether that's worrying. */
export function backupAge(at: number | null, now: number): { text: string; level: '' | 'warn' | 'bad' } {
  if (!at) return { text: 'None found', level: 'bad' };
  const hours = (now - at) / 3600;
  return { text: fmtRelative(at, now), level: hours > 7 * 24 ? 'bad' : hours > 48 ? 'warn' : '' };
}

type Show = 'all' | 'agents' | 'other' | 'stopped';
type Sort = 'id' | 'mem' | 'cpu' | 'disk' | 'uptime' | 'backup';

export function Proxmox() {
  const { data, error } = useApi<InfraData>('/api/infra');
  const [graphs, setGraphs] = useGraphs('proxmox');
  const [show, setShow] = useState<Show>('all');
  const [sort, setSort] = useState<Sort>('id');
  const [text, setText] = useState('');
  const [open, setOpen] = useOpenAgent();
  const { data: fleet } = useFleet();
  const current = fleet?.agents.find((agent) => agent.host === open);
  const { data: watch } = useApi<{ settings?: { backup_hours?: number } }>('/api/watch', { live: false });
  const backupHours = watch?.settings?.backup_hours ?? 48;
  const snap = data?.snapshot;
  const now = Date.now() / 1000;
  if (error && !data) return <div className="hq-page"><h1>Proxmox</h1><p className="muted">Couldn’t load Proxmox data. Check that HQ is running, then refresh.</p></div>;
  if (data && !data.configured) return <div className="hq-page"><h1>Proxmox</h1><p className="muted">HQ has no Proxmox access yet. Install HQ’s key on the Proxmox host (see “Control” in the HQ runbook).</p></div>;
  if (!snap) return <div className="hq-page"><h1>Proxmox</h1><p className="muted">{data?.error ? `Couldn’t read Proxmox: ${data.error}` : 'Reading the Proxmox host… this takes a few seconds after HQ starts.'}</p></div>;

  const t = snap.totals;
  const needle = text.trim().toLowerCase();
  const order: Record<Sort, (g: InfraGuest) => number> = {
    id: (g) => g.vmid, mem: (g) => -(g.mem / (g.maxmem || 1)), cpu: (g) => -g.cpu, disk: (g) => -(g.disk / (g.maxdisk || 1)),
    uptime: (g) => -g.uptime, backup: (g) => g.last_backup ?? 0,
  };
  const guests = snap.guests.filter((g) => !g.template)
    .filter((g) => show === 'all' || (show === 'agents' ? !!g.host : show === 'other' ? !g.host : g.status !== 'running'))
    .filter((g) => !needle || `${g.vmid} ${g.name} ${g.label} ${g.node} ${g.tags.join(' ')}`.toLowerCase().includes(needle))
    .sort((a, b) => order[sort](a) - order[sort](b));
  // Agents if HQ knows which containers they are, otherwise every container; the age is the alert setting.
  const watched = snap.guests.some((g) => g.host) ? snap.guests.filter((g) => g.host) : snap.guests.filter((g) => g.type === 'lxc' && !g.template);
  const noBackup = snap.backups.read ? watched.filter((g) => !g.last_backup || now - g.last_backup > backupHours * 3600) : [];
  const guestName = (vmid: string | number | null) => snap.guests.find((g) => String(g.vmid) === String(vmid))?.label ?? `guest ${vmid}`;

  return (
    <div className="hq-page proxmox">
      <header className="page-head">
        <div>
          <h1>Proxmox</h1>
          <p className="lede">
            {snap.cluster.is_cluster ? `Cluster ${snap.cluster.name ?? ''}: ${snap.cluster.online} of ${snap.cluster.nodes} nodes online${snap.cluster.quorate ? '' : ', no quorum'}` : `Single node${snap.nodes[0] ? `, ${snap.nodes[0].name}` : ''}`}
            {snap.cluster.version ? ` · Proxmox VE ${snap.cluster.version}` : ''} · read {fmtRelative(snap.at, now)}
          </p>
        </div>
        <GraphsToggle on={graphs} onChange={setGraphs} />
      </header>
      {snap.errors.length > 0 && <p className="sheet-error">Part of the reading failed: {snap.errors.join('; ')}</p>}
      {data?.error && <p className="sheet-error">The latest reading failed, so this is from {fmtRelative(snap.at, now)}: {data.error}</p>}

      <section className="stats stats-grid" aria-label="Totals">
        <div className="stat"><div className="k">CPU</div><div className="v num">{Math.round(t.cpu * 100)}%</div><div className="d num">of {t.cpus} threads · {t.allocated_cpus} given to guests</div></div>
        <div className="stat"><div className="k">Memory</div><div className="v num">{pct(t.mem, t.maxmem)}%</div><div className="d num">{bytes(t.mem)} of {bytes(t.maxmem)} · {bytes(t.allocated_mem)} promised to guests ({pct(t.allocated_mem, t.maxmem)}%)</div></div>
        <div className="stat"><div className="k">Storage</div><div className="v num">{pct(t.storage_used, t.storage_total)}%</div><div className="d num">{bytes(t.storage_total - t.storage_used)} free of {bytes(t.storage_total)}</div></div>
        <div className="stat"><div className="k">Guests</div><div className="v num">{t.running}/{t.guests}</div><div className="d">running{snap.hidden ? ` · ${snap.hidden} hidden` : ''}</div></div>
        <div className="stat"><div className="k">Room left</div><div className="v num">{t.room_for_agents ?? '—'}</div><div className="d">{t.typical_agent.mem ? `more agents of a typical size (${bytes(t.typical_agent.mem)} memory, ${bytes(t.typical_agent.disk ?? 0)} disk)` : 'more agents'}</div></div>
      </section>

      <section aria-label="Nodes">
        <div className="section-head"><h2>{snap.nodes.length === 1 ? 'Node' : 'Nodes'}</h2></div>
        <div className="px-nodes">
          {snap.nodes.map((node) => (
            <article key={node.name} className={`px-node${node.status !== 'online' ? ' offline' : ''}`} aria-label={`Node ${node.name}`}>
              <div className="px-node-head">
                <b>{node.name}</b>
                <span className={`px-level${node.status === 'online' ? '' : ' offline'}`}>{node.status === 'online' ? `Up ${fmtDuration(node.uptime)}` : 'Offline'}</span>
              </div>
              <div className="muted px-node-sub">{[node.cpu_model, node.cpus ? `${node.cpus} threads` : null, node.pve, node.kernel ? `kernel ${node.kernel}` : null].filter(Boolean).join(' · ')}</div>
              {node.status === 'online' && <>
                <Meter label="CPU" used={node.cpu} total={1} text={`${Math.round(node.cpu * 100)}%${node.load.length ? ` · load ${node.load.map((l) => l.toFixed(2)).join(' ')}` : ''}`} />
                <Meter label="Memory" used={node.mem} total={node.maxmem} />
                {node.maxswap > 0 && <Meter label="Swap" used={node.swap} total={node.maxswap} />}
                {node.maxrootfs > 0 && <Meter label="System disk" used={node.rootfs} total={node.maxrootfs} />}
                <dl className="px-facts">
                  <div><dt>Guests</dt><dd className="num">{node.running} running of {node.guests}</dd></div>
                  <div><dt>Memory promised</dt><dd className="num">{bytes(node.allocated_mem)} ({pct(node.allocated_mem, node.maxmem)}% of real){node.allocated_mem > node.maxmem ? ' · overcommitted' : ''}</dd></div>
                  <div><dt>CPU given out</dt><dd className="num">{node.allocated_cpus} cores on {node.cpus} threads ({node.cpus ? (node.allocated_cpus / node.cpus).toFixed(1) : '—'}×)</dd></div>
                  <div><dt>Free now</dt><dd className="num">{bytes(node.free_mem)} memory · {bytes(node.free_disk)} for containers</dd></div>
                  <div><dt>Room for</dt><dd className="num">{node.room_for_agents ?? '—'} more agents</dd></div>
                </dl>
                {graphs && node.series && (
                  <TrendChart title="Last 24 hours" win={node.series} height={150} format={(n) => `${Math.round(n)}%`}
                    series={[{ key: 'cpu', label: 'CPU', values: node.series.cpu_pct, color: 'var(--text)', kind: 'line' }, { key: 'mem', label: 'Memory', values: node.series.mem_pct, color: 'var(--working)', kind: 'line' }]} />
                )}
              </>}
            </article>
          ))}
        </div>
      </section>

      <section aria-label="Storage">
        <div className="section-head"><h2>Storage</h2></div>
        <div className="mini-table px-storage" role="table" aria-label="Storage">
          <div className="mini-row mini-headrow" role="row"><span role="columnheader">Storage</span><span role="columnheader">Where</span><span role="columnheader">Used</span><span role="columnheader">Holds</span></div>
          {snap.storage.map((s) => (
            <div className="mini-row" role="row" key={`${s.node}-${s.id}`}>
              <span role="cell"><b>{s.id}</b> <span className="muted">{s.type}</span></span>
              <span role="cell" className="muted">{s.shared ? 'Shared' : s.node}{s.status !== 'available' ? ` · ${s.status ?? 'unavailable'}` : ''}</span>
              <span role="cell">{s.total ? <Meter label={`${s.id} used`} used={s.used} total={s.total} text={`${bytes(s.total - s.used)} free of ${bytes(s.total)}`} /> : <span className="muted">—</span>}</span>
              <span role="cell" className="muted">{s.content.join(', ')}</span>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Guests">
        <div className="section-head"><h2>Containers and VMs</h2></div>
        <div className="tools">
          <input type="search" aria-label="Search guests" placeholder="Search by name, ID, node or tag" value={text} onChange={(event) => setText(event.target.value)} />
          <Segmented value={show} onChange={setShow} options={[{ value: 'all', label: 'All' }, { value: 'agents', label: 'Agents' }, { value: 'other', label: 'Other' }, { value: 'stopped', label: 'Not running' }]} />
          <select aria-label="Sort by" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
            <option value="id">By ID</option><option value="mem">Most memory used</option><option value="cpu">Most CPU</option>
            <option value="disk">Fullest disk</option><option value="uptime">Longest up</option><option value="backup">Oldest backup</option>
          </select>
        </div>
        <div className="mini-table px-guests" role="table" aria-label="Containers and VMs">
          <div className="mini-row mini-headrow" role="row">
            <span role="columnheader">Guest</span><span role="columnheader">Node</span><span role="columnheader">Up</span><span role="columnheader">CPU</span>
            <span role="columnheader">Memory</span><span role="columnheader">Disk</span><span role="columnheader">Last backup</span>
          </div>
          {guests.map((g) => {
            const backup = backupAge(g.last_backup, now);
            return (
              <div className="mini-row" role="row" key={g.vmid} data-status={g.status}>
                <span role="cell" className="px-guest">
                  <span className={`dot ${g.status === 'running' ? 'dot-working' : 'dot-quiet'}`} />
                  <span className="num muted">{g.vmid}</span>
                  {g.host ? <button type="button" className="link-button" onClick={() => setOpen(g.host)}>{g.label}</button> : <span>{g.name}</span>}
                  {g.type === 'qemu' && <span className="chip">VM</span>}
                  {g.tags.slice(0, 2).map((tag) => <span key={tag} className="chip">{tag}</span>)}
                </span>
                <span role="cell" className="muted">{g.node}</span>
                <span role="cell" className="num">{g.status === 'running' ? fmtDuration(g.uptime) : <span className="muted">{g.status}</span>}</span>
                <span role="cell" className="num">{g.status === 'running' ? `${Math.round(g.cpu * 100)}% of ${g.cpus}` : '—'}</span>
                <span role="cell">{g.maxmem ? <Meter label={`${g.label} memory`} used={g.mem} total={g.maxmem} text={`${bytes(g.mem)} / ${bytes(g.maxmem)}`} /> : '—'}</span>
                <span role="cell">{g.maxdisk && g.type === 'lxc' ? <Meter label={`${g.label} disk`} used={g.disk} total={g.maxdisk} text={`${bytes(g.disk)} / ${bytes(g.maxdisk)}`} /> : <span className="muted">{g.maxdisk ? bytes(g.maxdisk) : '—'}</span>}</span>
                <span role="cell" className={`num px-backup ${snap.backups.read ? backup.level : ''}`}>{snap.backups.read ? backup.text : '…'}</span>
              </div>
            );
          })}
        </div>
        {!guests.length && <p className="empty">Nothing matches.</p>}
        {snap.hidden > 0 && <p className="note">{snap.hidden} guest{snap.hidden === 1 ? ' is' : 's are'} left out on purpose (CT 500).</p>}
      </section>

      <section aria-label="Backups">
        <div className="section-head"><h2>Backups</h2>{snap.backups.at && <span className="muted">checked {fmtRelative(snap.backups.at, now)}</span>}</div>
        {!snap.backups.read ? <p className="muted">Backup listings are read every 10 minutes; the first one hasn’t finished yet.</p> : <>
          {snap.backups.failed.length > 0 && (
            <ul className="px-failed">{snap.backups.failed.map((f, i) => <li key={i}>Failed {fmtRelative(f.at, now)} on {f.node}{f.vmid ? ` for ${guestName(f.vmid)} (${f.vmid})` : ''}: {f.status}</li>)}</ul>
          )}
          <p>{noBackup.length
            ? <><b>{noBackup.length} {noBackup.length === 1 ? 'has' : 'have'} no backup in the last {backupHours} hours:</b> <span className="muted">{noBackup.map((g) => g.label).join(', ')}</span></>
            : `Every ${watched.some((g) => g.host) ? 'agent' : 'container'} has a backup from the last ${backupHours} hours.`}</p>
          <div className="mini-table px-jobs" role="table" aria-label="Backup jobs">
            <div className="mini-row mini-headrow" role="row"><span role="columnheader">Job</span><span role="columnheader">When</span><span role="columnheader">To</span><span role="columnheader">Covers</span></div>
            {snap.backups.jobs.map((job) => (
              <div className="mini-row" role="row" key={job.id}>
                <span role="cell">{job.comment || job.id}{!job.enabled && <span className="chip">Off</span>}</span>
                <span role="cell" className="num">{job.schedule ?? '—'}</span>
                <span role="cell">{job.storage ?? '—'} <span className="muted">{job.mode}</span></span>
                <span role="cell" className="muted">{job.all ? 'All guests' : job.vmids.length ? `${job.vmids.length} guests` : '—'}{job.node ? ` on ${job.node}` : ''}</span>
              </div>
            ))}
          </div>
          {!snap.backups.jobs.length && <p className="muted">No scheduled backup jobs in Proxmox. Backups may be run some other way; see docs/backups-plan.md.</p>}
        </>}
      </section>
      {current && <AgentSheet key={current.host} agent={current} plan={fleet?.plan} periodLabel="in the last 24 hours" onClose={() => setOpen(null)} />}
      <p className="note">Read-only: HQ reads this from the Proxmox API every minute (backup listings every 10 minutes) and changes nothing. Clusters work the same way: each node, its storage and its guests appear here as they join.</p>
    </div>
  );
}
