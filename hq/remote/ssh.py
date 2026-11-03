import json
import subprocess


class RemoteError(Exception):
    pass


def scan_argv(host):
    command = (['docker', 'exec', '-i', host.container, 'python3', '-'] if host.container
               else ['python' if host.os == 'windows' else 'python3', '-'])
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=10', '--', host.ssh, *command]


def run_scan(host, request: dict, scanner_text: str, runner=subprocess.run) -> dict:
    payload = (scanner_text + '\n__HQ_REQUEST__\n' + json.dumps(request)).encode('utf-8')
    try:
        result = runner(scan_argv(host), input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RemoteError(str(exc)) from exc
    if result.returncode:
        stderr = result.stderr.decode('utf-8', errors='replace') if isinstance(result.stderr, bytes) else str(result.stderr)
        raise RemoteError(f'ssh exited {result.returncode}: {stderr[:300]}')
    if len(result.stdout) > 64 * 1024 * 1024:
        raise RemoteError('scanner answer exceeds 64 MB')
    try:
        answer = json.loads(result.stdout)
        if not isinstance(answer, dict):
            raise ValueError('answer is not an object')
        return answer
    except (ValueError, UnicodeDecodeError) as exc:
        raise RemoteError(f'invalid scanner JSON: {exc}') from exc
