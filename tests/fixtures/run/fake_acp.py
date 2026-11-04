"""A small ACP peer for subprocess integration tests."""
import json
import sys


def emit(message):
    print(json.dumps({'jsonrpc': '2.0', **message}), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        emit({'id': message['id'], 'result': {'protocolVersion': 1}})
    elif method == 'session/new':
        emit({'id': message['id'], 'result': {'sessionId': 'fake-session'}})
    elif method == 'session/load':
        emit({'id': message['id'], 'result': {}})
    elif method == 'session/prompt':
        text = message['params']['prompt'][0]['text']
        if text == 'fail':  # real opencode 1.18: an error reply, and the process stays alive
            emit({'id': message['id'], 'error': {'code': -32603, 'message': 'Internal error: Cannot connect to API'}})
            continue
        if text == 'fail_hang':  # an error reply, then ignore stdin EOF
            emit({'id': message['id'], 'error': {'code': -32603, 'message': 'Internal error: stuck'}})
            import time; time.sleep(30)
        emit({'method': 'session/update', 'params': {'sessionId': 'fake-session',
              'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})
        if text == 'ask':
            emit({'id': 42, 'method': 'session/request_permission', 'params': {
                'sessionId': 'fake-session', 'toolCall': {'title': 'a.py', 'kind': 'edit', 'rawInput': {'file_path': 'a.py'}},
                'options': [{'optionId': 'yes', 'kind': 'allow_once'}, {'optionId': 'no', 'kind': 'reject_once'}]}})
            answer = json.loads(sys.stdin.readline())
            emit({'method': 'session/update', 'params': {'sessionId': 'fake-session', 'update': {
                'content': {'type': 'text', 'text': json.dumps(answer)}}}})
        emit({'id': message['id'], 'result': {'stopReason': 'end_turn'}})
