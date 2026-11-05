import { useState, type FormEvent } from 'react';
import './sign-in.css';

export function signOut() {
  const token = localStorage.getItem('hq-token') ?? '';
  const done = () => {
    localStorage.removeItem('hq-token');
    localStorage.removeItem('hq-run-key');
    location.hash = '';
    location.reload();
  };
  void fetch('/api/logout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-HQ-Token': token },
    body: '{}',
  }).finally(done);
}

export function SignIn() {
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const body = (await response.json()) as { token?: string; run_key?: string; error?: string; retry_after?: number };
      if (response.ok && body.token) {
        localStorage.setItem('hq-token', body.token);
        if (body.run_key) localStorage.setItem('hq-run-key', body.run_key);
        location.reload();
        return;
      }
      setPassword('');
      setMessage(
        response.status === 429 && body.retry_after
          ? `Too many attempts. Try again in ${Math.ceil(body.retry_after / 60)} min.`
          : (body.error ?? 'Sign-in failed.'),
      );
    } catch {
      setMessage('Can’t reach the server. Check your connection and try again.');
    }
    setBusy(false);
  };
  return (
    <div className="sign-in">
      <form className="sign-in-card" onSubmit={(event) => void submit(event)}>
        <h1>Sign in to Agent HQ</h1>
        <p>Enter your password to continue.</p>
        <label htmlFor="sign-in-password">Password</label>
        <input
          id="sign-in-password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {message && <div className="sign-in-message" role="alert">{message}</div>}
        <button type="submit" disabled={busy || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <p className="sign-in-help">
          On the computer running Agent HQ, open it from the link printed when it starts, or from
          the <code>token</code> file beside hq.db.
        </p>
      </form>
    </div>
  );
}
