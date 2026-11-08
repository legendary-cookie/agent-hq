"""Passwords (salted scrypt), browser sessions and agent bridge tokens. Only hashes are stored."""
import base64
import hashlib
import hmac
import secrets

from .db import now

SESSION_DAYS = 30
N, R, P = 2 ** 14, 8, 1
LOCK_AFTER = 5


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return 'scrypt$%d$%d$%d$%s$%s' % (N, R, P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split('$')
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(db, user_id):
    token = secrets.token_urlsafe(32)
    db.run('INSERT INTO sessions VALUES (?,?,?,?)', (token_hash(token), user_id, now(), now() + SESSION_DAYS * 86400))
    db.run('DELETE FROM sessions WHERE expires < ?', (now(),))
    return token


def session_user(db, token):
    if not token:
        return None
    return db.one('SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id '
                  'WHERE s.token_hash = ? AND s.expires > ? AND u.disabled = 0', (token_hash(token), now()))


def end_session(db, token):
    if token:
        db.run('DELETE FROM sessions WHERE token_hash = ?', (token_hash(token),))


def login(db, username, password):
    """Return the user, or raise ValueError with a message safe to show."""
    state = db.one('SELECT * FROM logins WHERE username = ?', (username,)) or {'failures': 0, 'locked_until': 0}
    if state['locked_until'] > now():
        raise ValueError('Too many wrong passwords. Try again in %d seconds.' % int(state['locked_until'] - now() + 1))
    user = db.one('SELECT * FROM users WHERE username = ? AND disabled = 0', (username,))
    # Check a dummy hash for unknown users, so timing doesn't reveal which names exist.
    ok = check_password(password, user['pw_hash'] if user else _DUMMY)
    if user and ok:
        db.run('DELETE FROM logins WHERE username = ?', (username,))
        return user
    failures = state['failures'] + 1
    lock = now() + min(900, 30 * 2 ** (failures - LOCK_AFTER)) if failures >= LOCK_AFTER else 0
    db.run('INSERT INTO logins VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET failures=?, locked_until=?',
           (username, failures, lock, failures, lock))
    raise ValueError('Wrong username or password.')


def new_agent_token(db, agent_id):
    token = 'acb_' + secrets.token_urlsafe(32)
    db.run('UPDATE agents SET token_hash = ? WHERE id = ?', (token_hash(token), agent_id))
    return token


def agent_for_token(db, token):
    if not token:
        return None
    return db.one('SELECT * FROM agents WHERE token_hash = ? AND disabled = 0', (token_hash(token),))


_DUMMY = hash_password(secrets.token_hex(8))
