"""Agents the server answers for itself, with no bridge on a container.

- demo: a stand-in agent for trying the app. It says what it received.
- openai: calls any OpenAI-compatible endpoint (for example 9router) with a system prompt.
  Handy for an agent that has no container yet, or as a fallback.
"""
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .db import loads
from .service import SERVER_KINDS


class Responders:
    def __init__(self, chat):
        self.chat = chat
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix='responder')
        self.stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, name='responders', daemon=True).start()

    def _loop(self):
        marks = ','.join('?' * len(SERVER_KINDS))
        while not self.stop.wait(0.4):
            try:
                agents = self.chat.db.q('SELECT DISTINCT a.* FROM deliveries d JOIN agents a ON a.id=d.agent_id '
                                        "WHERE d.state='queued' AND a.kind IN (%s) AND a.disabled=0" % marks, SERVER_KINDS)
                for a in agents:
                    for job in self.chat.take(a):
                        self.pool.submit(self._answer, a, job)
                if int(time.time()) % 30 == 0:
                    self.chat.sweep()
            except Exception as exc:  # keep the loop alive whatever one agent does
                print('responders:', exc, flush=True)

    def _answer(self, a, job):
        did = job['delivery_id']
        try:
            self.chat.working(a, did)
            if a['kind'] == 'demo':
                self._demo(a, job)
            else:
                self._openai(a, job)
        except Exception as exc:
            self.chat.fail(a, did, str(exc) or exc.__class__.__name__)

    def _demo(self, a, job):
        conv = job['conversation']
        where = {'dm': 'our chat', 'channel': '#' + conv['name'], 'task': 'this task'}[conv['kind']]
        parts = ['Hi %s, %s here (a demo agent).' % (job['from']['name'].split(' (')[0], a['name']),
                 '', 'You wrote in %s:' % where, '', '> ' + (job['text'] or '(nothing)').replace('\n', '\n> ')]
        if job['context']:
            parts += ['', 'I also see %d earlier message(s) from others since my last reply.' % len(job['context'])]
        if job['files']:
            parts += ['', 'Files: ' + ', '.join('`%s`' % f['name'] for f in job['files'])]
        if job['task']:
            parts += ['', 'Working on task #%d, **%s**. Done.' % (job['task']['id'], job['task']['title']), '', '[task:review]']
        text = '\n'.join(parts)
        shown = ''
        for word in text.split(' '):
            shown += ('' if not shown else ' ') + word
            if len(shown) % 5 == 0:
                if not self.chat.reply(a, job['delivery_id'], shown, final=False):
                    return  # cancelled
                time.sleep(0.03)
        self.chat.reply(a, job['delivery_id'], text, final=True)

    def _openai(self, a, job):
        cfg = loads(a['config'], {})
        base, model, key = (cfg.get('base_url') or '').rstrip('/'), cfg.get('model') or 'default', cfg.get('api_key') or ''
        if not base:
            raise RuntimeError('this agent has no base URL set')
        system = cfg.get('system') or ('You are %s, an AI agent at Example Company. Answer clearly and briefly.' % a['name'])
        messages = [{'role': 'system', 'content': system}]
        for h in job['history']:
            content = h['text'] if h['role'] == 'assistant' else '%s: %s' % (h['name'], h['text'])
            messages.append({'role': h['role'], 'content': content})
        messages.append({'role': 'user', 'content': job['prompt']})
        body = json.dumps({'model': model, 'messages': messages, 'stream': True}).encode()
        req = urllib.request.Request(base + '/chat/completions', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        text, last = '', 0.0
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    delta = json.loads(data)['choices'][0].get('delta', {}).get('content') or ''
                except (ValueError, KeyError, IndexError):
                    continue
                text += delta
                if text and time.time() - last > 0.4:
                    last = time.time()
                    if not self.chat.reply(a, job['delivery_id'], text, final=False):
                        return
        if not text.strip():
            raise RuntimeError('the model returned an empty answer')
        self.chat.reply(a, job['delivery_id'], text, final=True)
