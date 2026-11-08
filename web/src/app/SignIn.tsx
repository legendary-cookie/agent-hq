import { useEffect, useState, type FormEvent } from 'react';
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
  const [brand, setBrand] = useState('Agent HQ');
  const [username, setUsername] = useState(() => localStorage.getItem('hq-last-user') ?? '');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void fetch('/api/brand')
      .then((response) => response.json() as Promise<{ brand: string | null }>)
      .then((body) => {
        if (body.brand) {
          setBrand(body.brand);
          document.title = body.brand;
        }
      })
      .catch(() => {});
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const name = username.trim().toLowerCase();
    if (!name || !password || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: name, password }),
      });
      const body = (await response.json()) as { token?: string; run_key?: string; error?: string; retry_after?: number };
      if (response.ok && body.token) {
        localStorage.setItem('hq-token', body.token);
        localStorage.setItem('hq-last-user', name);
        if (body.run_key) localStorage.setItem('hq-run-key', body.run_key);
        location.hash = '/overview';
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
        <span className="sign-in-mark" aria-hidden="true" />
        <h1>Sign in to {brand}</h1>
        <p>Enter your username and password to continue.</p>
        <label htmlFor="sign-in-username">Username</label>
        <input
          id="sign-in-username"
          type="text"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          autoFocus={!username}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <label htmlFor="sign-in-password">Password</label>
        <input
          id="sign-in-password"
          type="password"
          autoComplete="current-password"
          autoFocus={!!username}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {message && <div className="sign-in-message" role="alert">{message}</div>}
        <button type="submit" disabled={busy || !password || !username.trim()}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  );
}
