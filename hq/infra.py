"""The Proxmox side of the fleet: cluster, nodes, storage, every guest, backups, and how much room is left.

One read-only script runs on the Proxmox host over HQ's existing SSH key and prints everything as JSON.
It asks the cluster-wide API (`/cluster/resources`), so a single node and a cluster of several work the
same way: every row says which node it's on. The heavier backup listing is read less often.

CT 500 is left out of everything, as in the rest of HQ. Nothing here changes anything on the host.
"""
import json
import statistics
import threading
import time

from .control.pve import EXCLUDED, ControlError

REFRESH_S = 60
BACKUPS_EVERY_S = 600
RRD_CELLS = 48  # 24 hours in 30-minute cells

# Runs on the Proxmox host as root, read-only: only `pvesh get`.
SCRIPT = r'''
import json, subprocess, sys
full = sys.argv[1] == 'full' if len(sys.argv) > 1 else False
def get(path, *extra, timeout=25):
    try:
        out = subprocess.run(['pvesh', 'get', path, '--output-format', 'json', *extra], capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {'_error': str(exc)[:200]}
    if out.returncode:
        return {'_error': (out.stderr or out.stdout or 'failed').strip()[-200:]}
    try:
        return json.loads(out.stdout or 'null')
    except ValueError:
        return {'_error': 'unreadable answer'}
resources = get('/cluster/resources')
rows = resources if isinstance(resources, list) else []
answer = {'resources': resources, 'cluster': get('/cluster/status'), 'version': get('/version'), 'nodes': {}}
for row in rows:
    if row.get('type') == 'node' and row.get('status') == 'online':
        name = row['node']
        answer['nodes'][name] = {'status': get(f'/nodes/{name}/status'), 'rrd': get(f'/nodes/{name}/rrddata', '--timeframe', 'day', '--cf', 'AVERAGE')}
if full:
    answer['backup_jobs'] = get('/cluster/backup')
    tasks = get('/cluster/tasks')
    answer['backup_tasks'] = [t for t in tasks if isinstance(t, dict) and t.get('type') == 'vzdump'][:200] if isinstance(tasks, list) else tasks
    latest, seen = {}, set()
    for row in rows:
        if row.get('type') != 'storage' or row.get('status') != 'available' or 'backup' not in (row.get('content') or ''):
            continue
        key = row['storage'] if row.get('shared') else (row['node'], row['storage'])
        if key in seen:
            continue
        seen.add(key)
        items = get(f"/nodes/{row['node']}/storage/{row['storage']}/content", '--content', 'backup', timeout=60)
        if not isinstance(items, list):
            continue
        for item in items:
            vmid, at = item.get('vmid'), item.get('ctime')
            if isinstance(vmid, int) and isinstance(at, (int, float)):
                count = latest.get(vmid, {}).get('count', 0) + 1
                if at > latest.get(vmid, {}).get('at', 0):
                    latest[vmid] = {'at': at, 'storage': row['storage'], 'size': item.get('size')}
                latest[vmid]['count'] = count
    answer['latest_backups'] = {str(k): v for k, v in latest.items()}
print(json.dumps(answer))
'''


def _num(value, default=0):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _rows(value):
    return value if isinstance(value, list) else []


def _series(rrd, now):
    """24 hours of node CPU and memory in 30-minute cells, ending at the latest whole half hour."""
    end = int(now // 1800 * 1800) + 1800
    start = end - RRD_CELLS * 1800
    cells = [[] for _ in range(RRD_CELLS)]
    for point in _rows(rrd):
        t = _num(point.get('time'), None)
        if t is None or not start <= t < end:
            continue
        cells[int((t - start) // 1800)].append(point)
    def avg(points, key, scale=1.0):
        values = [_num(p.get(key), None) for p in points]
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values) * scale, 2) if values else None
    cpu = [avg(points, 'cpu', 100) for points in cells]
    mem = []
    for points in cells:
        used, total = avg(points, 'memused'), avg(points, 'memtotal')
        mem.append(round(used / total * 100, 1) if used is not None and total else None)
    return {'start': start, 'bucket_s': 1800, 'count': RRD_CELLS, 'cpu_pct': cpu, 'mem_pct': mem}


def summarize(raw, agents, now, backups=None):
    """Turn the host's answer into what the page and the watcher use.

    agents: vmid -> {'host': ..., 'label': ...} for the fleet's agents, so guests can be named and linked.
    backups: the last full answer's backup parts, kept between the lighter refreshes.
    """
    resources = _rows(raw.get('resources'))
    errors = [raw['resources']['_error']] if isinstance(raw.get('resources'), dict) and raw['resources'].get('_error') else []
    backups = backups or {}
    latest = backups.get('latest_backups') if isinstance(backups.get('latest_backups'), dict) else None

    guests, hidden = [], 0
    for row in resources:
        if row.get('type') not in ('lxc', 'qemu'):
            continue
        vmid = row.get('vmid')
        if vmid in EXCLUDED:
            hidden += 1
            continue
        agent = agents.get(vmid) or {}
        last = (latest or {}).get(str(vmid))
        guests.append({
            'vmid': vmid, 'name': row.get('name') or str(vmid), 'type': row['type'], 'node': row.get('node'), 'status': row.get('status'),
            'uptime': _num(row.get('uptime')), 'cpu': _num(row.get('cpu')), 'cpus': _num(row.get('maxcpu')),
            'mem': _num(row.get('mem')), 'maxmem': _num(row.get('maxmem')), 'disk': _num(row.get('disk')), 'maxdisk': _num(row.get('maxdisk')),
            'netin': _num(row.get('netin')), 'netout': _num(row.get('netout')), 'template': bool(row.get('template')),
            'tags': [t for t in (row.get('tags') or '').replace(',', ';').split(';') if t], 'pool': row.get('pool'),
            'host': agent.get('host'), 'label': agent.get('label') or row.get('name') or str(vmid),
            'last_backup': last.get('at') if last else None, 'backups': last.get('count') if last else 0,
        })
    guests.sort(key=lambda g: g['vmid'])

    nodes = []
    for row in resources:
        if row.get('type') != 'node':
            continue
        name = row.get('node')
        detail = (raw.get('nodes') or {}).get(name) or {}
        status = detail.get('status') if isinstance(detail.get('status'), dict) and not detail['status'].get('_error') else {}
        mine = [g for g in guests if g['node'] == name and not g['template']]
        cpuinfo, memory, swap, rootfs = status.get('cpuinfo') or {}, status.get('memory') or {}, status.get('swap') or {}, status.get('rootfs') or {}
        maxmem, maxcpu = _num(row.get('maxmem')) or _num(memory.get('total')), _num(row.get('maxcpu')) or _num(cpuinfo.get('cpus'))
        nodes.append({
            'name': name, 'status': row.get('status'), 'uptime': _num(row.get('uptime')) or _num(status.get('uptime')),
            'cpu': _num(row.get('cpu')), 'cpus': maxcpu, 'cpu_model': cpuinfo.get('model'), 'sockets': cpuinfo.get('sockets'),
            'load': [float(x) for x in status.get('loadavg') or [] if str(x).replace('.', '', 1).isdigit()][:3],
            'mem': _num(row.get('mem')) or _num(memory.get('used')), 'maxmem': maxmem,
            'swap': _num(swap.get('used')), 'maxswap': _num(swap.get('total')),
            'rootfs': _num(rootfs.get('used')), 'maxrootfs': _num(rootfs.get('total')),
            'pve': status.get('pveversion'), 'kernel': (status.get('current-kernel') or {}).get('release') or status.get('kversion'),
            'guests': len(mine), 'running': sum(g['status'] == 'running' for g in mine),
            'allocated_mem': sum(g['maxmem'] for g in mine), 'allocated_cpus': sum(g['cpus'] for g in mine),
            'running_mem': sum(g['maxmem'] for g in mine if g['status'] == 'running'),
            'series': _series(detail.get('rrd'), now) if detail.get('rrd') is not None else None,
        })
    nodes.sort(key=lambda n: n['name'] or '')

    storage, seen = [], set()
    for row in resources:
        if row.get('type') != 'storage':
            continue
        key = row.get('storage') if row.get('shared') else (row.get('node'), row.get('storage'))
        if key in seen:
            continue
        seen.add(key)
        storage.append({'id': row.get('storage'), 'node': None if row.get('shared') else row.get('node'), 'shared': bool(row.get('shared')),
                        'type': row.get('plugintype'), 'status': row.get('status'), 'used': _num(row.get('disk')), 'total': _num(row.get('maxdisk')),
                        'content': [c for c in (row.get('content') or '').split(',') if c]})
    storage.sort(key=lambda s: (s['node'] or '', s['id'] or ''))

    cluster_rows = _rows(raw.get('cluster'))
    cluster_row = next((r for r in cluster_rows if r.get('type') == 'cluster'), None)
    cluster = {
        'name': cluster_row.get('name') if cluster_row else None,
        'is_cluster': bool(cluster_row),
        'quorate': bool(cluster_row.get('quorate')) if cluster_row else True,
        'nodes': len(nodes), 'online': sum(n['status'] == 'online' for n in nodes),
        'version': (raw.get('version') or {}).get('version') if isinstance(raw.get('version'), dict) else None,
    }

    # Room left: memory not yet promised to any guest, and free space where container disks live.
    # A typical agent's size; until agents are linked to their containers, a typical running container's.
    agent_guests = [g for g in guests if g['host']] or [g for g in guests if g['type'] == 'lxc' and g['status'] == 'running' and not g['template']]
    typical_mem = statistics.median([g['maxmem'] for g in agent_guests if g['maxmem']]) if agent_guests else None
    typical_disk = statistics.median([g['maxdisk'] for g in agent_guests if g['maxdisk']]) if agent_guests else None
    for node in nodes:
        disks = [s for s in storage if (s['node'] == node['name'] or s['shared']) and 'rootdir' in s['content'] and s['status'] == 'available']
        free_disk = max((s['total'] - s['used'] for s in disks), default=0)
        node['unallocated_mem'] = max(0, node['maxmem'] - node['allocated_mem'])
        node['free_mem'] = max(0, node['maxmem'] - node['mem'])
        node['free_disk'] = free_disk
        fits = []
        if typical_mem:
            fits.append(int(node['free_mem'] // typical_mem))
        if typical_disk:
            fits.append(int(free_disk // typical_disk))
        node['room_for_agents'] = min(fits) if fits and node['status'] == 'online' else None

    jobs = []
    for job in _rows(backups.get('backup_jobs')):
        jobs.append({'id': job.get('id'), 'schedule': job.get('schedule') or (f"{job.get('dow', '')} {job.get('starttime', '')}".strip() or None),
                     'enabled': job.get('enabled', 1) not in (0, '0', False), 'storage': job.get('storage'), 'mode': job.get('mode'),
                     'all': bool(job.get('all')), 'vmids': [v for v in str(job.get('vmid') or '').split(',') if v and v != '500'],
                     'node': job.get('node'), 'comment': job.get('comment')})
    failed = []
    for task in _rows(backups.get('backup_tasks')):
        state = task.get('status')
        if task.get('endtime') and state and state != 'OK' and now - _num(task.get('endtime')) < 7 * 86400 and str(task.get('id') or '') != '500':
            failed.append({'at': _num(task.get('endtime')), 'node': task.get('node'), 'vmid': task.get('id'), 'status': str(state)[:200]})

    totals = {
        'cpus': sum(n['cpus'] for n in nodes), 'cpu': (sum(n['cpu'] * n['cpus'] for n in nodes) / sum(n['cpus'] for n in nodes)) if sum(n['cpus'] for n in nodes) else 0,
        'mem': sum(n['mem'] for n in nodes), 'maxmem': sum(n['maxmem'] for n in nodes), 'allocated_mem': sum(n['allocated_mem'] for n in nodes),
        'allocated_cpus': sum(n['allocated_cpus'] for n in nodes),
        'storage_used': sum(s['used'] for s in storage if s['status'] == 'available'), 'storage_total': sum(s['total'] for s in storage if s['status'] == 'available'),
        'guests': len([g for g in guests if not g['template']]), 'running': sum(g['status'] == 'running' for g in guests),
        'room_for_agents': sum(n['room_for_agents'] or 0 for n in nodes) if any(n['room_for_agents'] is not None for n in nodes) else None,
        'typical_agent': {'mem': typical_mem, 'disk': typical_disk},
    }
    return {'at': now, 'cluster': cluster, 'totals': totals, 'nodes': nodes, 'storage': storage, 'guests': guests, 'hidden': hidden,
            'backups': {'read': latest is not None, 'at': backups.get('at'), 'jobs': jobs, 'failed': failed[:50]},
            'errors': errors}


class Infra:
    """Keeps a fresh snapshot in the background, so pages and the watcher never wait on the host."""

    def __init__(self, pve, agents, clock=time.time):
        self.pve, self.agents, self.clock = pve, agents, clock
        self._lock = threading.Lock()
        self._snapshot = None
        self._backups = {}
        self._error = None

    def refresh(self):
        now = self.clock()
        full = now - (self._backups.get('at') or 0) >= BACKUPS_EVERY_S
        out = self.pve.run(['python3', '-c', SCRIPT, 'full' if full else 'light'], timeout=180 if full else 60)
        raw = json.loads(out.strip().splitlines()[-1])
        if full:
            self._backups = {'at': now, **{k: raw.get(k) for k in ('backup_jobs', 'backup_tasks', 'latest_backups')}}
        snapshot = summarize(raw, self.agents(), now, self._backups)
        with self._lock:
            self._snapshot, self._error = snapshot, None
        return snapshot

    def snapshot(self):
        with self._lock:
            return self._snapshot, self._error

    def run(self, stop):
        while not stop.is_set():
            if self.pve.configured():
                try:
                    self.refresh()
                except (ControlError, ValueError, IndexError) as exc:
                    with self._lock:
                        self._error = str(exc)[:300]
                except Exception as exc:  # noqa: BLE001 - the loop must keep going
                    with self._lock:
                        self._error = f'{type(exc).__name__}: {str(exc)[:200]}'
            stop.wait(REFRESH_S)
