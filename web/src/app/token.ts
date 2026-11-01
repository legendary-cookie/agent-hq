export function bootstrapToken(
  loc: Location,
  storage: Storage,
): string | null | Promise<string | null> {
  const code = new URLSearchParams(loc.hash.slice(1)).get('code');
  if (code) {
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/office`);
    return (
      fetch('/api/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      })
        .then((response) => {
          if (!response.ok) throw new Error('Exchange failed');
          return response.json() as Promise<{ token: string }>;
        })
        .then(({ token }) => {
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
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/office`);
    return token;
  }
  return storage.getItem('hq-token');
}
