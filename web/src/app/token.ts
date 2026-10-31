export function bootstrapToken(loc: Location, storage: Storage): string | null {
  const token = new URLSearchParams(loc.hash.slice(1)).get('token');
  if (token) {
    storage.setItem('hq-token', token);
    history.replaceState(null, '', `${loc.pathname}${loc.search}#/dashboard`);
    return token;
  }
  return storage.getItem('hq-token');
}
