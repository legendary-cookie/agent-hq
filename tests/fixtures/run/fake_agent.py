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
elif mode == 'ratelimit':
    print(json.dumps({'type': 'error', 'message': '429 rate limit'}), flush=True)
elif mode == 'garbage':
    print('not json', flush=True)
elif mode == 'lines':
    for i in range(int(sys.argv[2])): print(json.dumps({'type': 'working', 'i': 'line' + str(i)}), flush=True)
