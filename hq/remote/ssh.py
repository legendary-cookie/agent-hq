import json
import subprocess
import threading


class RemoteError(Exception):
    pass


def scan_argv(host):
    command = (['docker', 'exec', '-i', host.container, 'python3', '-'] if host.container
               else ['python' if host.os == 'windows' else 'python3', '-'])
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=10', '--', host.ssh, *command]


ANSWER_LIMIT = 64 * 1024 * 1024
STDERR_LIMIT = 64 * 1024


def run_scan(host, request: dict, scanner_text: str, popen=subprocess.Popen) -> dict:
    payload = (scanner_text + '\n_HQ_REQUEST = ' + json.dumps(json.dumps(request)) +
               '\nmain(_HQ_REQUEST)\n').encode('utf-8')
    try:
        process = popen(scan_argv(host), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise RemoteError(str(exc)) from exc

    timed_out = threading.Event()
    def timeout():
        timed_out.set()
        process.kill()
    timer = threading.Timer(120, timeout)
    def write_input():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (OSError, ValueError):
            pass
    stderr_chunks = []
    def read_stderr():
        # Continue draining after the cap to avoid blocking the child.
        kept = 0
        while True:
            chunk = process.stderr.read(65536)
            if not chunk:
                break
            if kept < STDERR_LIMIT:
                stderr_chunks.append(chunk[:STDERR_LIMIT - kept])
                kept += len(stderr_chunks[-1])
    writer = threading.Thread(target=write_input, daemon=True)
    err_reader = threading.Thread(target=read_stderr, daemon=True)
    timer.start()
    writer.start()
    err_reader.start()
    chunks = []
    total = 0
    too_large = False
    try:
        while total <= ANSWER_LIMIT:
            chunk = process.stdout.read(min(65536, ANSWER_LIMIT + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > ANSWER_LIMIT:
            too_large = True
            process.kill()
        process.wait()
    finally:
        timer.cancel()
        writer.join()
        err_reader.join()
        process.stdout.close()
        process.stderr.close()
    if too_large:
        raise RemoteError('scanner answer too large')
    if timed_out.is_set():
        raise RemoteError('ssh timed out after 120 seconds')
    if process.returncode:
        stderr = b''.join(stderr_chunks).decode('utf-8', errors='replace')
        raise RemoteError(f'ssh exited {process.returncode}: {stderr[:300]}')
    try:
        answer = json.loads(b''.join(chunks))
        if not isinstance(answer, dict):
            raise ValueError('answer is not an object')
        return answer
    except (ValueError, UnicodeDecodeError) as exc:
        raise RemoteError(f'invalid scanner JSON: {exc}') from exc
