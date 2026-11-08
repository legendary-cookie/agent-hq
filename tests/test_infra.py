import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from hq.control.audit import ControlLog
from hq.infra import SCRIPT, Infra, summarize
from hq.watch import AlertSettings, Watchman

GB = 1024 ** 3
NOW = 1_800_000_000.0


def raw(cluster=False, node_status='online', storage_used=100 * GB):
    resources = [
        {'type': 'node', 'node': 'pve', 'status': node_status, 'cpu': 0.25, 'maxcpu': 16, 'mem': 40 * GB, 'maxmem': 64 * GB, 'uptime': 86400 * 7},
        {'type': 'storage', 'storage': 'local-lvm', 'node': 'pve', 'status': 'available', 'disk': storage_used, 'maxdisk': 1000 * GB, 'plugintype': 'lvmthin', 'content': 'rootdir,images', 'shared': 0},
        {'type': 'storage', 'storage': 'pbs', 'node': 'pve', 'status': 'available', 'disk': 2000 * GB, 'maxdisk': 4000 * GB, 'plugintype': 'pbs', 'content': 'backup', 'shared': 1},
        {'type': 'lxc', 'vmid': 300, 'name': 'roy', 'node': 'pve', 'status': 'running', 'cpu': 0.1, 'maxcpu': 4, 'mem': 2 * GB, 'maxmem': 8 * GB, 'disk': 30 * GB, 'maxdisk': 100 * GB, 'uptime': 3600, 'tags': 'staff;zylos'},
        {'type': 'lxc', 'vmid': 301, 'name': 'parthiv', 'node': 'pve', 'status': 'stopped', 'maxcpu': 4, 'maxmem': 8 * GB, 'maxdisk': 100 * GB},
        {'type': 'lxc', 'vmid': 121, 'name': 'agent-hq', 'node': 'pve', 'status': 'running', 'cpu': 0.02, 'maxcpu': 2, 'mem': int(1.95 * GB), 'maxmem': 2 * GB, 'disk': 5 * GB, 'maxdisk': 20 * GB, 'uptime': 99},
        {'type': 'lxc', 'vmid': 500, 'name': 'private', 'node': 'pve', 'status': 'running', 'maxmem': 4 * GB},
        {'type': 'qemu', 'vmid': 900, 'name': 'template', 'node': 'pve', 'status': 'stopped', 'template': 1, 'maxmem': 2 * GB},
    ]
    if cluster:
        resources.append({'type': 'node', 'node': 'pve2', 'status': 'offline', 'maxcpu': 8, 'maxmem': 32 * GB})
    nodes = {'pve': {'status': {'cpuinfo': {'model': 'Xeon', 'cpus': 16, 'sockets': 1}, 'loadavg': ['0.50', '0.40', '0.30'],
                                'memory': {'used': 40 * GB, 'total': 64 * GB}, 'swap': {'used': GB, 'total': 8 * GB},
                                'rootfs': {'used': 20 * GB, 'total': 100 * GB}, 'pveversion': 'pve-manager/9.0.3', 'kversion': '6.14'},
                     'rrd': [{'time': NOW - 3600, 'cpu': 0.2, 'memused': 32 * GB, 'memtotal': 64 * GB}]}} if node_status == 'online' else {}
    status = [{'type': 'cluster', 'name': 'lab', 'quorate': 0}] + [{'type': 'node', 'name': 'pve'}] if cluster else [{'type': 'node', 'name': 'pve'}]
    return {'resources': resources, 'cluster': status, 'version': {'version': '9.0.3'}, 'nodes': nodes}


BACKUPS = {'at': NOW, 'backup_jobs': [{'id': 'backup-1', 'schedule': 'daily 02:00', 'storage': 'pbs', 'all': 1, 'mode': 'snapshot', 'enabled': 1}],
           'backup_tasks': [{'type': 'vzdump', 'status': 'job errors', 'endtime': NOW - 600, 'node': 'pve', 'id': '301'},
                            {'type': 'vzdump', 'status': 'OK', 'endtime': NOW - 600, 'node': 'pve', 'id': '300'},
                            {'type': 'vzdump', 'status': 'failed', 'endtime': NOW - 600, 'node': 'pve', 'id': '500'}],
           'latest_backups': {'300': {'at': NOW - 3600, 'count': 7}, '500': {'at': NOW - 60, 'count': 1}}}
AGENTS = {300: {'host': 'ct300-worker', 'label': 'Worker'}, 301: {'host': 'ct301-parthiv', 'label': 'Parthiv'}}


class SummaryTests(unittest.TestCase):
    def test_nodes_storage_guests_and_room(self):
        snap = summarize(raw(), AGENTS, NOW, BACKUPS)
        node = snap['nodes'][0]
        self.assertEqual((node['name'], node['cpus'], node['load'], node['pve']), ('pve', 16, [0.5, 0.4, 0.3], 'pve-manager/9.0.3'))
        self.assertEqual(node['allocated_mem'], 18 * GB)  # 8 + 8 + 2: the template and CT 500 don't count
        self.assertEqual(node['free_disk'], 900 * GB)    # only storage that holds container disks
        self.assertEqual(node['room_for_agents'], 3)      # 24 GB free memory / 8 GB typical agent
        self.assertEqual(snap['totals']['room_for_agents'], 3)
        self.assertEqual([s['id'] for s in snap['storage']], ['pbs', 'local-lvm'])  # shared storage first, then per node
        self.assertEqual(node['series']['count'], 48)
        self.assertEqual(sum(v is not None for v in node['series']['cpu_pct']), 1)

    def test_ct_500_is_left_out_everywhere(self):
        snap = summarize(raw(), AGENTS, NOW, BACKUPS)
        self.assertNotIn(500, [g['vmid'] for g in snap['guests']])
        self.assertEqual(snap['hidden'], 1)
        self.assertNotIn('500', json.dumps(snap['backups']))

    def test_guests_are_named_and_backups_matched(self):
        snap = summarize(raw(), AGENTS, NOW, BACKUPS)
        roy = next(g for g in snap['guests'] if g['vmid'] == 300)
        self.assertEqual((roy['label'], roy['host'], roy['tags'], roy['last_backup'], roy['backups']), ('Worker', 'ct300-worker', ['staff', 'zylos'], NOW - 3600, 7))
        self.assertEqual(next(g for g in snap['guests'] if g['vmid'] == 121)['label'], 'agent-hq')
        self.assertEqual(snap['backups']['jobs'][0]['schedule'], 'daily 02:00')
        self.assertEqual([f['vmid'] for f in snap['backups']['failed']], ['301'])

    def test_a_cluster_with_an_offline_node(self):
        snap = summarize(raw(cluster=True), AGENTS, NOW, BACKUPS)
        self.assertEqual(snap['cluster'], {'name': 'lab', 'is_cluster': True, 'quorate': False, 'nodes': 2, 'online': 1, 'version': '9.0.3'})
        self.assertIsNone(next(n for n in snap['nodes'] if n['name'] == 'pve2')['room_for_agents'])

    def test_missing_parts_dont_break_it(self):
        snap = summarize({'resources': {'_error': 'permission denied'}}, {}, NOW)
        self.assertEqual((snap['nodes'], snap['guests'], snap['errors']), ([], [], ['permission denied']))
        self.assertFalse(snap['backups']['read'])


class FakePve:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def configured(self):
        return True

    def run(self, words, timeout=60):
        self.calls.append(words[-1])
        return json.dumps(self.answer)

    def containers(self):
        return {}


class InfraTests(unittest.TestCase):
    def test_backups_are_read_every_ten_minutes(self):
        now = [NOW]
        pve = FakePve({**raw(), **{k: v for k, v in BACKUPS.items() if k != 'at'}})
        infra = Infra(pve, lambda: AGENTS, clock=lambda: now[0])
        infra.refresh(); now[0] += 60; infra.refresh(); now[0] += 600; infra.refresh()
        self.assertEqual(pve.calls, ['full', 'light', 'full'])
        self.assertTrue(infra.snapshot()[0]['backups']['read'])

    def test_the_host_script_compiles(self):
        compile(SCRIPT, 'infra-script', 'exec')


class WatchInfraTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = AlertSettings(Path(tmp.name) / 'alerts.json')
        self.log = ControlLog(Path(tmp.name) / 'log.jsonl')

    def warnings(self, snap):
        watch = Watchman(self.settings, FakePve({}), self.log, lambda: {'agents': []}, clock=lambda: NOW, infra=lambda: snap)
        watch.tick()
        return {(w['kind'], w['host']): w['text'] for w in watch.summary()['warnings']}

    def test_hq_itself_storage_backups_and_cluster(self):
        found = self.warnings(summarize(raw(cluster=True, storage_used=950 * GB), AGENTS, NOW, BACKUPS))
        self.assertEqual(found[('memory', 'ct121')], 'agent-hq (CT 121): memory 97% full')
        self.assertIn('Storage local-lvm on pve is 95% full', found.values())
        self.assertIn('Proxmox node pve2 is offline', found.values())
        self.assertTrue(found[('host', 'cluster')].startswith('The Proxmox cluster lab has lost quorum'))
        self.assertEqual(found[('backup', 'backups')], '1 agent has no backup in the last 48 hours: Parthiv')
        self.assertIn(('backup', 'backup-task:301'), found)

    def test_no_backup_warning_before_backups_are_read(self):
        found = self.warnings(summarize(raw(), AGENTS, NOW))
        self.assertFalse(any(kind == 'backup' for kind, _ in found))

    def test_backup_hours_is_a_setting(self):
        self.settings.update({'backup_hours': 2000})
        self.assertEqual(self.settings.load()['backup_hours'], 2000)
        with self.assertRaises(ValueError):
            self.settings.update({'backup_hours': 1})


if __name__ == '__main__':
    unittest.main()
