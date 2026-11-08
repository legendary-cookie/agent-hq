/** The signed-in user's own account: who they are, and changing their password. Open to everyone who can sign in. */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api/client';
import { useMe } from '../api/me';
import { MIN_PASSWORD, passwordProblem } from './Accounts';
import './fleet.css';
import './control.css';
import './router.css';
import './accounts.css';

export function MyAccount() {
  const { me } = useMe();
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const change = async (event: FormEvent) => {
    event.preventDefault();
    const wrong = !current ? 'Enter your current password.' : passwordProblem(password, repeat);
    if (wrong) return setNote({ text: wrong, bad: true });
    setBusy(true);
    setNote(null);
    try {
      await api('/api/account/password', { method: 'POST', body: JSON.stringify({ current, new: password }) });
      setCurrent(''); setPassword(''); setRepeat('');
      setNote({ text: 'Password changed. Any other browsers signed in as you were signed out; this one stays signed in.' });
    } catch (problem) {
      setNote({ text: (problem as Error).message, bad: true });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="hq-page my-account">
      <header className="page-head">
        <div><h1>My account</h1><p className="lede">Your sign-in for HQ.</p></div>
      </header>
      {me && (
        <dl aria-label="You">
          <dt>Username</dt><dd>{me.username}</dd>
          {me.name && <><dt>Name</dt><dd>{me.name}</dd></>}
          <dt>Role</dt><dd>{me.role_label}{me.admin ? ' · can do everything' : ''}</dd>
        </dl>
      )}
      <section aria-label="Change password">
        <div className="section-head"><h2>Change password</h2></div>
        <form className="router-key-editor acct-editor" aria-label="Change your password" onSubmit={change}>
          <label>Current password <input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} /></label>
          <label>New password <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <label>Repeat new password <input type="password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} /></label>
          <p className="muted router-small">At least {MIN_PASSWORD} characters. Changing it signs out your other browsers.</p>
          {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}
          <div className="control-buttons">
            <button type="submit" className="plain-button" disabled={busy || !current || !password || !repeat}>{busy ? 'Changing…' : 'Change password'}</button>
          </div>
        </form>
      </section>
    </div>
  );
}
