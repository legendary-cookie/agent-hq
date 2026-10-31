export function bootstrapToken(
  loc: Location,
  storage: Storage,
): string | null | Promise<string | null> {
  const code = new URLSearchParams(loc.hash.slice(1)).get('code');
  if (code) {
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/dashboard`);
    return fetch('/api/exchange', {
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
      .catch(() => {
        storage.removeItem('hq-token');
        return null;
      });
  }
  const token = new URLSearchParams(loc.hash.slice(1)).get('token');
  if (token) {
    storage.setItem('hq-token', token);
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/dashboard`);
    return token;
  }
  return storage.getItem('hq-token');
}
