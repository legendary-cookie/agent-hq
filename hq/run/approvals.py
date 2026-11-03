import shlex
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Approval:
    id: str
    run_id: str
    tool_name: str
    summary: str
    requested_at: float
    expires_at: float
    state: str


class AnsweredError(Exception):
    pass


class ExpiredError(Exception):
    pass


_COMMANDS = {'bash', 'powershell'}
_FILES = {'write', 'edit'}
_DANGEROUS = {'rm', 'rmdir', 'del', 'curl', 'wget', 'ssh', 'scp', 'nc',
              'powershell', 'pwsh', 'sudo'}


def _words(command):
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def _basename(word):
    return word.replace('\\', '/').rsplit('/', 1)[-1].lower()


def summarize(tool_name, tool_input):
    name = tool_name.lower()
    if name in _COMMANDS:
        value = tool_input.get('command', '')
    elif name in _FILES:
        value = tool_input.get('file_path', '')
    else:
        value = f"{tool_name}: {next(iter(tool_input.values()), '')}"
    return str(value)[:200]


def never_auto(tool_name, tool_input):
    if tool_name.lower() not in _COMMANDS:
        return False
    command = tool_input.get('command', '')
    # Any way to chain or redirect a second command always asks; '&' chains on Windows cmd.
    if not isinstance(command, str) or any(op in command for op in ('|', ';', '&', '`', '$(', '>', '<', '\n', '\r')):
        return True
    words = _words(command)
    if not words:
        return True
    first = _basename(words[0])
    # git push/reset anywhere in the arguments (git -C repo push, git -c x=y reset).
    return first in _DANGEROUS or (first == 'git' and any(w.lower() in {'push', 'reset'} for w in words[1:]))


def rule_key(tool_name, tool_input, worktree: Path):
    name = tool_name.lower()
    if never_auto(tool_name, tool_input):
        return None
    if name in _COMMANDS:
        words = _words(tool_input.get('command', ''))
        return f'{tool_name}:{_basename(words[0])}' if words else None
    if name in _FILES:
        value = tool_input.get('file_path')
        if not isinstance(value, str) or not value:
            return None
        root = worktree.resolve()
        path = Path(value)
        path = (root / path).resolve()
        if not path.is_relative_to(root) or not path.parent.is_relative_to(root):
            return None
        return f'{tool_name}:{path.parent.relative_to(root)}'
    return None


class ApprovalBook:
    def __init__(self, store, timeout_s: float, on_change: Callable[[dict], None], clock=time.time):
        self.store = store
        self.timeout_s = timeout_s
        self.on_change = on_change
        self.clock = clock
        self._lock = threading.RLock()
        self._items = {}
        self._rules = {}
        self._seen = set()

    def _event(self, approval):
        self.on_change({'type': 'approval', 'id': approval.id,
                        'run_id': approval.run_id, 'state': approval.state})

    def open(self, run_id, request_id, tool_name, tool_input, reply):
        with self._lock:
            aid = f'{run_id}:{request_id}'
            if aid in self._seen:
                raise AnsweredError(aid)
            run = self.store.get_run(run_id)
            if run is None:
                raise KeyError(run_id)
            key = rule_key(tool_name, tool_input, Path(run['workdir']))
            if key is not None and key in self._rules.get(run_id, set()):
                self._seen.add(aid)
                reply('allow', None)
                return None
            now = self.clock()
            approval = Approval(aid, run_id, tool_name, summarize(tool_name, tool_input),
                                now, now + self.timeout_s, 'waiting')
            timer = threading.Timer(self.timeout_s, self._timeout, args=(aid,))
            timer.daemon = True
            with self.store._lock, self.store._writer:
                self.store._writer.execute(
                    'INSERT INTO approvals (id,run_id,tool_name,summary,requested_at,state) VALUES (?,?,?,?,?,?)',
                    (aid, run_id, tool_name, approval.summary, now, 'waiting'))
            self._items[aid] = (approval, reply, timer, key)
            self._seen.add(aid)
            self._event(approval)
            timer.start()
            return approval

    def _finish(self, aid, state, decision, note):
        approval, reply, timer, key = self._items[aid]
        timer.cancel()
        approval = replace(approval, state=state)
        self._items[aid] = (approval, reply, timer, key)
        with self.store._lock, self.store._writer:
            self.store._writer.execute('UPDATE approvals SET state=?, answered_at=? WHERE id=?',
                                       (state, self.clock(), aid))
        reply(decision, note)
        self._event(approval)
        return approval

    def _timeout(self, aid):
        with self._lock:
            item = self._items.get(aid)
            if item and item[0].state == 'waiting':
                minutes = self.timeout_s / 60
                self._finish(aid, 'timed_out', 'deny', f'Denied: no answer within {minutes:g} minutes')

    def answer(self, approval_id, decision: str, note: str | None):
        if decision not in {'allow_once', 'allow_run', 'deny'}:
            raise ValueError('Invalid decision')
        with self._lock:
            approval, _, _, key = self._items[approval_id]
            if approval.state in {'expired', 'timed_out'}:
                raise ExpiredError(approval_id)
            if approval.state != 'waiting':
                raise AnsweredError(approval_id)
            if self.clock() >= approval.expires_at:
                self._timeout(approval_id)
                raise ExpiredError(approval_id)
            if decision == 'allow_run' and key is not None:
                self._rules.setdefault(approval.run_id, set()).add(key)
            return self._finish(approval_id, 'denied' if decision == 'deny' else 'allowed',
                                'deny' if decision == 'deny' else 'allow',
                                note[:500] if decision == 'deny' and note is not None else None)

    def close_run(self, run_id, state='expired'):
        if state not in {'expired', 'denied'}:
            raise ValueError('Invalid close state')
        with self._lock:
            for aid, item in list(self._items.items()):
                if item[0].run_id == run_id and item[0].state == 'waiting':
                    self._finish(aid, state, 'deny', None)
            self._rules.pop(run_id, None)

    def waiting(self, run_id=None):
        with self._lock:
            return [item[0] for item in self._items.values()
                    if item[0].state == 'waiting' and (run_id is None or item[0].run_id == run_id)]

    def has_waiting(self, run_id):
        return bool(self.waiting(run_id))
