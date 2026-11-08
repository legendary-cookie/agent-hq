"""Install or remove the HQ limits probe for one agent login. Run as that login:

    python3 limits-probe.py install
    python3 limits-probe.py remove
    python3 limits-probe.py check

Claude Code gives its status line the plan's 5-hour and weekly use. The probe saves those numbers to
~/.claude/hq-limits.json for HQ to read, then hands the same input to the status line the agent
already had, so nothing changes on screen. It never reads a login or token.
"""
import json
import os
import sys
import time
from pathlib import Path

CLAUDE = Path.home() / '.claude'
SETTINGS = CLAUDE / 'settings.json'
BACKUP = CLAUDE / 'settings.json.hq-backup'
PROBE = CLAUDE / 'hq-limits-statusline.py'
PREVIOUS = CLAUDE / 'hq-limits-previous.json'
OUTPUT = CLAUDE / 'hq-limits.json'
COMMAND = f'python3 {PROBE}'

PROBE_CODE = r'''#!/usr/bin/env python3
"""HQ limits probe: saves the plan limit numbers Claude Code gives its status line. Reads no login."""
import json, os, subprocess, sys, time
home = os.path.expanduser('~/.claude')
raw = sys.stdin.read()
try:
    limits = json.loads(raw).get('rate_limits')
    if isinstance(limits, dict) and limits:
        tmp = os.path.join(home, 'hq-limits.json.tmp')
        with open(tmp, 'w') as out:
            json.dump({'captured_at': time.time(), 'rate_limits': limits}, out)
        os.replace(tmp, os.path.join(home, 'hq-limits.json'))
except Exception:
    pass
try:
    with open(os.path.join(home, 'hq-limits-previous.json')) as saved:
        previous = json.load(saved).get('command')
except Exception:
    previous = None
if previous:
    try:
        shown = subprocess.run(previous, shell=True, input=raw, capture_output=True, text=True, timeout=5)
        sys.stdout.write(shown.stdout)
    except Exception:
        pass
'''


def load():
    if not SETTINGS.exists():
        return {}
    return json.loads(SETTINGS.read_text(encoding='utf-8'))


def save(data):
    tmp = SETTINGS.with_suffix('.json.hq-tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, SETTINGS)


def install():
    CLAUDE.mkdir(exist_ok=True)
    data = load()
    current = data.get('statusLine')
    if not (isinstance(current, dict) and current.get('command') == COMMAND):
        if SETTINGS.exists() and not BACKUP.exists():
            BACKUP.write_text(SETTINGS.read_text(encoding='utf-8'), encoding='utf-8')
        previous = current.get('command') if isinstance(current, dict) and current.get('type') == 'command' else None
        PREVIOUS.write_text(json.dumps({'command': previous, 'statusLine': current}), encoding='utf-8')
        data['statusLine'] = {'type': 'command', 'command': COMMAND}
        save(data)
    PROBE.write_text(PROBE_CODE, encoding='utf-8')
    PROBE.chmod(0o755)
    print('installed', 'kept previous status line' if json.loads(PREVIOUS.read_text()).get('command') else 'no previous status line')


def remove():
    data = load()
    if isinstance(data.get('statusLine'), dict) and data['statusLine'].get('command') == COMMAND:
        saved = json.loads(PREVIOUS.read_text(encoding='utf-8')).get('statusLine') if PREVIOUS.exists() else None
        if saved:
            data['statusLine'] = saved
        else:
            data.pop('statusLine', None)
        save(data)
    for path in (PROBE, PREVIOUS, OUTPUT, BACKUP):
        if path.exists():
            path.unlink()
    print('removed')


def check():
    installed = isinstance(load().get('statusLine'), dict) and load()['statusLine'].get('command') == COMMAND
    if OUTPUT.exists():
        data = json.loads(OUTPUT.read_text(encoding='utf-8'))
        age = int(time.time() - data.get('captured_at', 0))
        windows = {k: round(v.get('used_percentage', -1), 1) for k, v in data.get('rate_limits', {}).items() if isinstance(v, dict)}
        print('installed' if installed else 'not installed', f'reading {age}s old', windows)
    else:
        print('installed' if installed else 'not installed', 'no reading yet')


if __name__ == '__main__':
    {'install': install, 'remove': remove, 'check': check}[sys.argv[1] if len(sys.argv) > 1 else 'check']()
