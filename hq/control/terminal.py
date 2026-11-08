"""A browser terminal: WebSocket on one side, an ssh command in a pseudo-terminal on the other.

Only the parts of WebSocket a browser terminal needs are implemented: one connection, text and binary
frames, ping, pong and close. Messages from the browser are JSON: {"type":"input","data":...} or
{"type":"resize","cols":..,"rows":..}. Output goes back as binary frames.
"""
import base64
import hashlib
import json
import os
import select
import socket
import struct
import subprocess
import threading
import time

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_FRAME = 1 << 20
IDLE_S = 4 * 3600
MAX_SESSION_S = 12 * 3600  # a terminal is closed after this, however busy
RECHECK_S = 30  # how often an open terminal checks it's still allowed


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(payload: bytes, opcode=2):
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack('>H', n)
    else:
        head += bytes([127]) + struct.pack('>Q', n)
    return head + payload


def read_frame(stream):
    """Return (opcode, payload), or (None, b'') when the browser has gone. Browser frames are always masked."""
    head = stream.read(2)
    if len(head) < 2:
        return None, b''
    opcode, masked, n = head[0] & 0x0F, head[1] & 0x80, head[1] & 0x7F
    if n in (126, 127):
        size = 2 if n == 126 else 8
        extra = stream.read(size)
        if len(extra) < size:
            return None, b''
        n = struct.unpack('>H' if size == 2 else '>Q', extra)[0]
    if not masked or n > MAX_FRAME:
        return None, b''
    mask = stream.read(4)
    data = stream.read(n)
    if len(mask) < 4 or len(data) < n:
        return None, b''
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def bridge(handler, command, readonly=False, on_end=lambda read, written: None, allowed=lambda: True, max_s=MAX_SESSION_S):
    """Upgrade the request and relay between the browser and the command until either side ends.

    allowed() is asked again every RECHECK_S seconds; once it says no (signed out, password changed, or
    the password check ran out) the terminal is closed. It is also closed after max_s seconds."""
    import fcntl
    import pty
    import termios
    key = handler.headers.get('Sec-WebSocket-Key')
    if not key or handler.headers.get('Upgrade', '').lower() != 'websocket':
        handler.send_response(400)
        handler.end_headers()
        return
    # Written by hand: the standard handler answers as HTTP/1.0, and browsers need HTTP/1.1 for a WebSocket.
    handler.wfile.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                         f'Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n').encode())
    handler.wfile.flush()
    handler.close_connection = True
    try:  # notice a laptop that vanished without closing the connection
        handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (AttributeError, OSError):
        pass

    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 110, 0, 0))
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True)
    os.close(slave)
    send_lock = threading.Lock()
    counts = {'read': 0, 'written': 0}

    def send(payload, opcode=2):
        with send_lock:
            try:
                handler.wfile.write(frame(payload, opcode))
                handler.wfile.flush()
            except OSError:
                pass

    def pump_out():
        while True:
            ready, _, _ = select.select([master], [], [], IDLE_S)
            if not ready:
                break
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            counts['read'] += len(data)
            send(data)
        send(b'', 8)

    stop = threading.Event()

    def guard():
        ends = time.monotonic() + max_s
        while not stop.wait(RECHECK_S):
            try:
                still = allowed() and time.monotonic() < ends
            except Exception:  # noqa: BLE001 - if it can't be checked, it isn't allowed
                still = False
            if not still:
                send(b'\x1b[0m\r\n[Closed by HQ: sign in or confirm your password again.]\r\n')
                send(b'', 8)
                if process.poll() is None:
                    process.terminate()
                try:
                    handler.connection.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):
                    pass
                return

    reader = threading.Thread(target=pump_out, daemon=True)
    reader.start()
    threading.Thread(target=guard, daemon=True).start()
    try:
        while process.poll() is None:
            opcode, payload = read_frame(handler.rfile)
            if opcode is None or opcode == 8:
                break
            if opcode == 9:
                send(payload, 10)
                continue
            if opcode not in (1, 2):
                continue
            try:
                message = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get('type') == 'input' and isinstance(message.get('data'), str) and not readonly:
                data = message['data'].encode()
                counts['written'] += len(data)
                os.write(master, data)
            elif message.get('type') == 'resize' and not readonly:  # a viewer mustn't resize the controller's screen
                cols, rows = message.get('cols'), message.get('rows')
                if isinstance(cols, int) and isinstance(rows, int) and 10 <= cols <= 500 and 3 <= rows <= 200:
                    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    finally:
        stop.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            os.close(master)
        except OSError:
            pass
        on_end(counts['read'], counts['written'])
