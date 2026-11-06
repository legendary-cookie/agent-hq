/** The sign-in lives in lasting storage so this browser stays signed in (the server ends it after
 *  30 days). A sign-in left in tab storage by an older version is moved across once. */
export function keepSignIn(from: Storage, to: Storage) {
  for (const key of ['hq-token', 'hq-run-key']) {
    const value = from.getItem(key);
    if (value && !to.getItem(key)) to.setItem(key, value);
    from.removeItem(key);
  }
}

export function bootstrapToken(
  loc: Location,
  storage: Storage,
): string | null | Promise<string | null> {
  const code = new URLSearchParams(loc.hash.slice(1)).get('code');
  if (code) {
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/overview`);
    return (
      fetch('/api/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      })
        .then((response) => {
          if (!response.ok) throw new Error('Exchange failed');
          return response.json() as Promise<{ token: string; run_key: string }>;
        })
        .then(({ token, run_key }) => {
          if (run_key) storage.setItem('hq-run-key', run_key);
          else storage.removeItem('hq-run-key');
          storage.setItem('hq-token', token);
          return token;
        })
        // A spent or expired code must not sign out a tab that already has a
        // working token (e.g. re-opening an old launch link).
        .catch(() => storage.getItem('hq-token'))
    );
  }
  const token = new URLSearchParams(loc.hash.slice(1)).get('token');
  if (token) {
    storage.setItem('hq-token', token);
    storage.removeItem('hq-run-key');
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/overview`);
    return token;
  }
  return storage.getItem('hq-token');
}
