"""Host validation and remote run operations shared by the service and runner."""
from pathlib import Path
import subprocess

from hq.remote import remote_runs
from hq.remote.config import load_hosts
from hq.run.config import Project
from hq.run import request


class RemoteGlue:
    def __init__(self, config_dir, launchers):
        self.config_dir = Path(config_dir)
        self.launchers = launchers

    def hosts(self):
        hosts, _ = load_hosts(self.config_dir)
        return [h for h in hosts if h.run and h.os in ('linux', 'macos') and not h.container]

    def remote(self, name):
        host = next((h for h in self.hosts() if h.name == name), None)
        if host is None:
            raise ValueError('Host does not support runs')
        return remote_runs.RemoteRuns(host)

    def tools(self, remote):
        detected = remote.action('tools').get('tools', [])
        allowed = {'claude': {'read_only', 'ask', 'edit'}, 'codex': {'read_only', 'edit'}}
        return {name: allowed[name] & launcher.levels() for name, launcher in self.launchers.items()
                if name in allowed and name in detected and getattr(launcher, 'tested', False)}

    def parse(self, data, run_cfg):
        remote = self.remote(data.get('host'))
        projects = [Project(p['name'], Path(p['path'])) for p in remote.host.projects]
        return request.parse_request(data, run_cfg, projects, self.tools(remote), remote=True)

    def options(self):
        result = []
        for host in self.hosts():
            try:
                remote = remote_runs.RemoteRuns(host)
                tools = self.tools(remote)
            except (ValueError, OSError, subprocess.SubprocessError):
                tools = {}
            result.append({'name': host.name, 'projects': [p['name'] for p in host.projects],
                           'tools': {n: {'levels': sorted(levels), 'harness': self.launchers[n].harness,
                                         'tested': True, 'can_ask': getattr(self.launchers[n], 'can_ask', False),
                                         'steer_mode': getattr(self.launchers[n], 'steer_mode', None)}
                                     for n, levels in tools.items()}})
        return result

    def action(self, run, action, **fields):
        return self.remote(run['host']).action(action, run['id'], run['project'], **fields)
