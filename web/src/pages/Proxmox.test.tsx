import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { InfraData, InfraSnapshot } from './Proxmox';

const GB = 1024 ** 3;
const NOW = Date.now() / 1000;
const state = vi.hoisted(() => ({ data: null as unknown }));
vi.mock('../api/client', () => ({
  useApi: (path: string) => ({ data: path === '/api/infra' ? state.data : null, error: null, loading: false, reload: () => undefined }),
  api: vi.fn(),
}));
import { Proxmox, backupAge, bytes } from './Proxmox';

const SNAP: InfraSnapshot = {
  at: NOW - 30,
  cluster: { name: null, is_cluster: false, quorate: true, nodes: 1, online: 1, version: '9.0.3' },
  totals: { cpus: 16, cpu: 0.25, mem: 40 * GB, maxmem: 64 * GB, allocated_mem: 80 * GB, allocated_cpus: 40, storage_used: 300 * GB, storage_total: 1000 * GB,
    guests: 3, running: 2, room_for_agents: 3, typical_agent: { mem: 8 * GB, disk: 100 * GB } },
  nodes: [{ name: 'pve', status: 'online', uptime: 7 * 86400, cpu: 0.25, cpus: 16, cpu_model: 'Xeon', sockets: 1, load: [0.5, 0.4, 0.3], mem: 40 * GB, maxmem: 64 * GB,
    swap: GB, maxswap: 8 * GB, rootfs: 20 * GB, maxrootfs: 100 * GB, pve: 'pve-manager/9.0.3', kernel: '6.14', guests: 3, running: 2,
    allocated_mem: 80 * GB, allocated_cpus: 40, running_mem: 72 * GB, unallocated_mem: 0, free_mem: 24 * GB, free_disk: 700 * GB, room_for_agents: 3, series: null }],
  storage: [{ id: 'local-lvm', node: 'pve', shared: false, type: 'lvmthin', status: 'available', used: 300 * GB, total: 1000 * GB, content: ['rootdir', 'images'] }],
  guests: [
    { vmid: 121, name: 'agent-hq', type: 'lxc', node: 'pve', status: 'running', uptime: 3600, cpu: 0.02, cpus: 2, mem: GB, maxmem: 2 * GB, disk: 5 * GB, maxdisk: 20 * GB, netin: 0, netout: 0, template: false, tags: [], host: null, label: 'agent-hq', last_backup: NOW - 3600, backups: 3 },
    { vmid: 300, name: 'roy', type: 'lxc', node: 'pve', status: 'running', uptime: 7200, cpu: 0.1, cpus: 4, mem: 2 * GB, maxmem: 8 * GB, disk: 30 * GB, maxdisk: 100 * GB, netin: 0, netout: 0, template: false, tags: ['staff'], host: 'ct300-worker', label: 'Worker', last_backup: null, backups: 0 },
    { vmid: 301, name: 'parthiv', type: 'lxc', node: 'pve', status: 'stopped', uptime: 0, cpu: 0, cpus: 4, mem: 0, maxmem: 8 * GB, disk: 0, maxdisk: 100 * GB, netin: 0, netout: 0, template: false, tags: [], host: 'ct301-parthiv', label: 'Parthiv', last_backup: NOW - 3 * 86400, backups: 2 },
  ],
  hidden: 1,
  backups: { read: true, at: NOW - 60, jobs: [{ id: 'backup-1', schedule: 'daily 02:00', enabled: true, storage: 'pbs', mode: 'snapshot', all: true, vmids: [], node: null, comment: null }],
    failed: [{ at: NOW - 600, node: 'pve', vmid: '301', status: 'job errors' }] },
  errors: [],
};

afterEach(() => cleanup());

it('shows totals, room left, the node, storage and backups', () => {
  state.data = { configured: true, snapshot: SNAP, error: null } satisfies InfraData;
  render(<Proxmox />);
  expect(screen.getByText(/Single node, pve · Proxmox VE 9.0.3/)).toBeInTheDocument();
  const totals = screen.getByRole('region', { name: 'Totals' });
  expect(within(totals).getByText('3')).toBeInTheDocument();
  expect(within(totals).getByText(/promised to guests \(125%\)/)).toBeInTheDocument();
  const node = screen.getByRole('article', { name: 'Node pve' });
  expect(within(node).getByText(/overcommitted/)).toBeInTheDocument();
  expect(within(node).getByText('40 cores on 16 threads (2.5×)')).toBeInTheDocument();
  expect(within(screen.getByRole('table', { name: 'Storage' })).getByText('700 GB free of 1000 GB')).toBeInTheDocument();
  expect(screen.getByText(/2 have no backup in the last 48 hours:/)).toBeInTheDocument();
  expect(screen.getByText(/Failed .* on pve for Parthiv \(301\): job errors/)).toBeInTheDocument();
  expect(screen.getByText('1 guest is left out on purpose (CT 500).')).toBeInTheDocument();
});

it('filters guests to agents and searches', () => {
  state.data = { configured: true, snapshot: SNAP, error: null } satisfies InfraData;
  render(<Proxmox />);
  const table = () => screen.getByRole('table', { name: 'Containers and VMs' });
  fireEvent.click(screen.getByRole('button', { name: 'Agents' }));
  expect(within(table()).queryByText('agent-hq')).toBeNull();
  expect(within(table()).getByRole('button', { name: 'Worker' })).toBeInTheDocument();
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search guests' }), { target: { value: 'parth' } });
  expect(within(table()).queryByRole('button', { name: 'Worker' })).toBeNull();
  expect(within(table()).getByRole('button', { name: 'Parthiv' })).toBeInTheDocument();
});

it('explains when there is no Proxmox access or no reading yet', () => {
  state.data = { configured: false, snapshot: null, error: null } satisfies InfraData;
  render(<Proxmox />);
  expect(screen.getByText(/no Proxmox access yet/)).toBeInTheDocument();
  cleanup();
  state.data = { configured: true, snapshot: null, error: 'ssh: timed out' } satisfies InfraData;
  render(<Proxmox />);
  expect(screen.getByText('Couldn’t read Proxmox: ssh: timed out')).toBeInTheDocument();
});

it('formats sizes and backup ages', () => {
  expect([bytes(0), bytes(1.5 * GB), bytes(64 * GB), bytes(2048 * GB)]).toEqual(['0 GB', '1.5 GB', '64 GB', '2.0 TB']);
  expect(backupAge(null, NOW)).toEqual({ text: 'None found', level: 'bad' });
  expect(backupAge(NOW - 3 * 86400, NOW).level).toBe('warn');
  expect(backupAge(NOW - 9 * 86400, NOW).level).toBe('bad');
});
