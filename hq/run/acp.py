import json

from hq.run.stream import StreamEvent, valid_session_id


class AcpDriver:
    def __init__(self, workdir, resume_session_id=None):
        if resume_session_id is not None and not valid_session_id(resume_session_id):
            raise ValueError('Invalid session id')
        self.workdir = str(workdir)
        self.resume = resume_session_id
        self.session_id = None
        self.pending = {}
        self.permissions = {}
        self.next_id = 0
        self.prompt = None

    def _line(self, data):
        return json.dumps({'jsonrpc': '2.0', **data}, separators=(',', ':')) + '\n'

    def _request(self, method, params):
        self.next_id += 1
        self.pending[self.next_id] = method
        return self._line({'id': self.next_id, 'method': method, 'params': params})

    def start(self, prompt):
        self.prompt = prompt
        return [self._request('initialize', {'protocolVersion': 1, 'clientCapabilities': {
            'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}})]

    def follow_up(self, text):
        if not self.session_id:
            return []
        return [self._request('session/prompt', {'sessionId': self.session_id,
                'prompt': [{'type': 'text', 'text': text}]})]

    def on_line(self, line):
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            return [StreamEvent('warning')], []
        if not isinstance(data, dict):
            return [StreamEvent('warning')], []
        method = data.get('method')
        if method == 'session/request_permission' and 'id' in data:
            return self._permission(data)
        if method and 'id' in data:
            return [], [self._line({'id': data['id'], 'error': {'code': -32601, 'message': 'not supported'}})]
        if method == 'session/update':
            params = data.get('params') or {}
            update = params.get('update') if isinstance(params, dict) else {}
            content = update.get('content') if isinstance(update, dict) else {}
            text = content.get('text') if isinstance(content, dict) else None
            return [StreamEvent('working', text=text)], []
        ident = data.get('id')
        try:
            pending = self.pending.pop(ident, None)
        except TypeError:
            return [StreamEvent('warning')], []
        if not pending or 'error' in data or not isinstance(data.get('result'), dict):
            return [StreamEvent('warning')], []
        if pending == 'initialize':
            params = {'cwd': self.workdir, 'mcpServers': []}
            if self.resume:
                params['sessionId'] = self.resume
            return [], [self._request('session/load' if self.resume else 'session/new', params)]
        if pending in ('session/new', 'session/load'):
            session = data['result'].get('sessionId', self.resume)
            if not valid_session_id(session) or (self.resume and session != self.resume):
                return [StreamEvent('warning')], []
            self.session_id = session
            return [StreamEvent('session', session_id=session)], self.follow_up(self.prompt)
        if pending == 'session/prompt':
            return [StreamEvent('turn_end')], []
        return [], []

    def _permission(self, data):
        ident = data['id']
        if not isinstance(ident, (str, int)) or isinstance(ident, bool):
            return [StreamEvent('warning')], []
        key = json.dumps(ident, separators=(',', ':'))
        params = data.get('params') if isinstance(data.get('params'), dict) else {}
        call = params.get('toolCall') if isinstance(params.get('toolCall'), dict) else {}
        options = params.get('options') if isinstance(params.get('options'), list) else []
        if key in self.permissions:
            return [StreamEvent('warning')], []
        self.permissions[key] = (ident, options)
        raw = call.get('rawInput')
        request = {'request_id': key, 'tool_name': call.get('kind') or 'edit',
                   'input': raw if isinstance(raw, dict) and raw else {'file_path': call.get('title')}}
        return [StreamEvent('permission', request=request)], []

    def answer(self, request_id, decision, note):
        entry = self.permissions.pop(request_id, None)
        if entry is None:
            return []
        ident, options = entry
        kind = {'allow': 'allow_once', 'deny': 'reject_once'}.get(decision)
        if kind is None:
            raise ValueError('Invalid decision')
        option = next((o.get('optionId') for o in options if isinstance(o, dict)
                       and o.get('kind') == kind and isinstance(o.get('optionId'), str)), None)
        outcome = {'outcome': 'selected', 'optionId': option} if option is not None else {'outcome': 'cancelled'}
        return [self._line({'id': ident, 'result': {'outcome': outcome}})]
