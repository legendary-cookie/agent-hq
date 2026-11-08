"""Password sign-in for use over a network.

Only a salted scrypt hash is stored. Sessions are kept as hashes of random tokens, in memory and in
sessions.json beside the password file (readable only by HQ), so a restart doesn't sign anyone out.
A session lasts 30 days on that browser; signing out ends it, and changing the password ends every
session.
"""
import getpass
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

MIN_LENGTH = 12
MAX_LENGTH = 1024
SESSION_S = 30 * 86400
MAX_SESSIONS = 20
FREE_ATTEMPTS = 5
FIRST_LOCK_S = 30
MAX_LOCK_S = 900
_SCRYPT = {'n': 2 ** 15, 'r': 8, 'p': 1}


def _hash(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **params)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class Locked(Exception):
    def __init__(self, retry_after: int):
        super().__init__('too many attempts')
        self.retry_after = retry_after


class Login:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._hashing = threading.Lock()  # scrypt is costly on purpose, so one at a time
        self._waiting = threading.BoundedSemaphore(4)  # at most this many attempts queue for it
        self._sessions = {}
        self._failures = 0
        self._locked_until = 0.0
        self.sessions_path = self.path.with_name('sessions.json')
        self._load_sessions()

    def _load_sessions(self):
        try:
            saved = json.loads(self.sessions_path.read_text(encoding='utf-8'))
            now = self.clock()
            self._sessions = {str(key): (float(value[0]), tuple(value[1]) if value[1] else None)
                              for key, value in saved.items() if float(value[0]) > now}
        except Exception:  # noqa: BLE001 - a damaged file only means signing in again
            self._sessions = {}

    def _save_sessions(self):
        """Called with self._lock held. Only token hashes are written, never the tokens."""
        try:
            self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.sessions_path.with_name(self.sessions_path.name + '.tmp')
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({key: [value[0], list(value[1]) if value[1] else None] for key, value in self._sessions.items()}, stream)
            os.replace(temp, self.sessions_path)
        except OSError:
            pass  # Sessions still work for this run; they just won't survive a restart.

    def configured(self) -> bool:
        return self.path.is_file()

    def set_password(self, password: str) -> None:
        if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
            raise ValueError(f'password must be at least {MIN_LENGTH} characters')
        salt = secrets.token_bytes(16)
        body = json.dumps({'salt': salt.hex(), 'hash': _hash(password, salt, _SCRYPT).hex(), **_SCRYPT})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(body)
        os.replace(temp, self.path)

    def _stamp(self):
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _matches(self, password: str) -> bool:
        record = json.loads(self.path.read_text(encoding='utf-8'))
        params = {key: int(record[key]) for key in ('n', 'r', 'p')}
        return hmac.compare_digest(_hash(password, bytes.fromhex(record['salt']), params), bytes.fromhex(record['hash']))

    def _raise_if_locked(self):
        with self._lock:
            wait = self._locked_until - self.clock()
        if wait > 0:
            raise Locked(int(wait) + 1)

    def _verify(self, password) -> bool:
        """Check the password, counting wrong answers towards the lockout. Raises Locked while blocked.

        The lockout is checked again once it's this attempt's turn to hash, and only a few attempts
        may wait at once, so a burst of parallel guesses can't slip past it."""
        self._raise_if_locked()
        if not isinstance(password, str) or not 0 < len(password) <= MAX_LENGTH or not self.configured():
            return False
        if not self._waiting.acquire(blocking=False):
            raise Locked(1)
        try:
            with self._hashing:
                self._raise_if_locked()
                good = self._matches(password)
        finally:
            self._waiting.release()
        with self._lock:
            if good:
                self._failures = 0
                return True
            self._failures += 1
            if self._failures >= FREE_ATTEMPTS:
                delay = min(MAX_LOCK_S, FIRST_LOCK_S * 2 ** (self._failures - FREE_ATTEMPTS))
                self._locked_until = self.clock() + delay
            return False

    def sign_in(self, password) -> str | None:
        """Return a session token, or None if wrong. Raises Locked while attempts are blocked."""
        if not self._verify(password):
            return None
        with self._lock:
            now = self.clock()
            self._sessions = {key: value for key, value in self._sessions.items() if value[0] > now}
            while len(self._sessions) >= MAX_SESSIONS:
                del self._sessions[min(self._sessions, key=lambda key: self._sessions[key][0])]
            token = secrets.token_urlsafe(32)
            self._sessions[_digest(token)] = (now + SESSION_S, self._stamp())
            self._save_sessions()
            return token

    def check(self, password) -> bool:
        """Confirm the password without starting a session, for actions that ask for it again.
        Wrong answers count towards the same lockout as sign-in."""
        return self._verify(password)

    def valid(self, token) -> bool:
        if not isinstance(token, str) or not token:
            return False
        with self._lock:
            session = self._sessions.get(_digest(token))
            return bool(session and session[0] > self.clock() and session[1] == self._stamp())

    def sign_out(self, token) -> None:
        if isinstance(token, str):
            with self._lock:
                if self._sessions.pop(_digest(token), None) is not None:
                    self._save_sessions()


def prompt_for_password(path: Path) -> int:
    """Set the password from a terminal. Nothing is echoed, logged or passed on a command line."""
    first = getpass.getpass('New password: ')
    if first != getpass.getpass('Repeat password: '):
        print('The passwords do not match. Nothing was changed.')
        return 1
    try:
        Login(path).set_password(first)
    except ValueError as exc:
        print(f'{exc}. Nothing was changed.')
        return 1
    print('Password saved. Anyone signed in has been signed out.')
    return 0
