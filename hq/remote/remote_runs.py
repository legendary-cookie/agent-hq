"""Transport for the fixed remote run helper."""
import json
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath

from hq.remote.ssh import scan_argv

BOOTSTRAP = "import os\nb=b''\nwhile not b.endswith(b'\\n'):\n b+=os.read(0,1)\nn=int(b)\ns=b''\nwhile len(s)<n:\n s+=os.read(0,n-len(s))\nexec(compile(s,'<hq-helper>','exec'))"
TOOLS = frozenset(('claude', 'codex'))


class RemoteRuns:
    def __init__(self, host, popen=subprocess.Popen):
        if not host.run or host.os not in ('linux', 'macos') or host.container:
            raise ValueError('Host does not support runs')
        self.host, self.popen = host, popen

    def _request(self, action, rid=None, project=None, **fields):
        if rid is not None and not re.fullmatch(r'run_[A-Za-z0-9_-]{1,80}', rid):
            raise ValueError('Invalid run id')
        if project is not None and project not in {p['name'] for p in self.host.projects}:
            raise ValueError('Project is not allowlisted')
        return dict(action=action, id=rid, project=project, projects=list(self.host.projects), **fields)

    def _open(self, request):
        source = Path(__file__).with_name('runner_helper.py').read_text(encoding='utf-8')
        payload = (source + '\nmain(' + repr(json.dumps(request)) + ')\n').encode('utf-8')
        argv = scan_argv(self.host)
        argv[argv.index('--'):argv.index('--')] = ['-o', 'ServerAliveInterval=15']
        argv = argv[:-2] + ['python3 -c ' + shlex.quote(BOOTSTRAP)]
        process = self.popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', errors='replace', bufsize=1)
        process.stdin.write(str(len(payload)) + '\n')
        process.stdin.flush()
        process.stdin.buffer.write(payload)
        process.stdin.buffer.flush()
        return process

    def start(self, rid, project, argv, env, stdin_text, level, *, stdin_eof=False, resume=False):
        if not isinstance(argv, list) or not argv or argv[0] not in TOOLS or any(not isinstance(a, str) or '\0' in a for a in argv):
            raise ValueError('Invalid tool argv')
        if level not in ('read_only', 'ask', 'edit') or (level == 'ask' and argv[0] != 'claude') or any('{workdir}' in a and a != '{workdir}' for a in argv):
            raise ValueError('Invalid remote launch')
        request = self._request('start', rid, project, argv=argv, env=env, stdin_text=stdin_text, level=level, stdin_eof=stdin_eof, resume=resume)
        process = self._open(request)
        line = process.stdout.readline(65536)
        try:
            workdir = json.loads(line)['hq_workdir']
            if not isinstance(workdir, str) or not workdir.startswith('/') or str(PurePosixPath(workdir)) != workdir or '..' in workdir.split('/') or not workdir.endswith('/' + rid):
                raise ValueError('Invalid remote workdir')
        except (ValueError, KeyError, TypeError):
            process.stdin.close()
            error = process.stderr.read()
            process.wait()
            process.stdout.close(); process.stderr.close()
            raise ValueError(error.strip() or 'Invalid remote workdir')
        return RemoteChild(self, process, rid, project, workdir)

    def action(self, action, rid=None, project=None, **fields):
        process = self._open(self._request(action, rid, project, **fields))
        output, error = process.communicate(timeout=120)
        if process.returncode:
            raise ValueError(error.strip() or 'Remote helper failed')
        return json.loads(output)


class RemoteChild:
    def __init__(self, remote, process, rid, project, workdir):
        self.remote, self.process, self.rid, self.project = remote, process, rid, project
        self.workdir = workdir
        self.stdin, self.stdout, self.stderr = process.stdin, process.stdout, process.stderr

    def alive(self):
        return self.process.poll() is None

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def kill_tree(self):
        try:
            self.remote.action('stop', self.rid, self.project)
        except (OSError, ValueError, subprocess.SubprocessError):
            if not self.stdin.closed:
                self.stdin.close()

    def close(self, close_stderr=True):
        for stream in (self.stdin, self.stdout, self.stderr if close_stderr else None):
            if stream and not stream.closed:
                stream.close()
