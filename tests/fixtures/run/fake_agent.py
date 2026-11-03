import json
import subprocess
import sys
import time

mode = sys.argv[1]
if mode == 'ok':
    print(json.dumps({'type': 'session', 'session_id': 's-1'}), flush=True)
    for _ in range(3): print(json.dumps({'type': 'working'}), flush=True)
elif mode == 'sleep':
    time.sleep(float(sys.argv[2]))
elif mode == 'fail':
    sys.exit(3)
elif mode == 'grandchild':
    child = subprocess.Popen([sys.executable, __file__, 'sleep', '60'])
    print(json.dumps({'type': 'pid', 'pid': child.pid}), flush=True)
    time.sleep(60)
elif mode == 'longline':
    print('x' * 5_000_000, flush=True)
elif mode == 'ratelimit':
    print(json.dumps({'type': 'error', 'status': 429}), flush=True)
elif mode == 'garbage':
    print('not json', flush=True)
elif mode == 'lines':
    for i in range(int(sys.argv[2])): print(json.dumps({'type': 'working', 'i': 'line' + str(i)}), flush=True)
elif mode == 'live':
    for line in sys.stdin:
        data = json.loads(line)
        print(json.dumps({'type': 'received', 'data': data}), flush=True)
        print(json.dumps({'type': 'result'}), flush=True)
elif mode == 'ask':
    sys.stdin.readline()
    print(json.dumps({'type': 'control_request', 'request_id': 'r1', 'request': {'subtype': 'can_use_tool', 'tool_name': sys.argv[2], 'input': {'command': sys.argv[3]}, 'tool_use_id': 'u1'}}), flush=True)
    if len(sys.argv) > 4 and sys.argv[4] == 'exit':
        sys.exit(0)
    reply = json.loads(sys.stdin.readline())
    print(json.dumps({'type': 'tool_done', 'behavior': reply['response']['response']['behavior']}), flush=True)
    time.sleep(.15)
elif mode == 'unknown':
    sys.stdin.readline()
    print(json.dumps({'type': 'control_request', 'request_id': 'r2', 'request': {'subtype': 'other'}}), flush=True)
    reply = json.loads(sys.stdin.readline())
    print(json.dumps({'type': 'tool_done', 'behavior': reply['response']['response']['behavior']}), flush=True)
elif mode == 'resume':
    print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'raw-id'}), flush=True)
