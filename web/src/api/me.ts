/** Who is signed in, and what their role allows. Read once per sign-in (per token) and shared by every component. */
import { useEffect, useState } from 'react';
import type { Route } from '../app/router';

export type Me = { username: string; name: string; role: string; role_label: string; permissions: string[]; admin: boolean };

/** The permission each page needs. null: everyone who is signed in. */
export const ROUTE_PERMISSION: Record<Route, string | null> = {
  overview: 'view.overview',
  office: 'view.overview',
  agents: 'view.agents',
  'new-agent': 'agents.create',
  sessions: 'view.transcripts',
  usage: 'view.usage',
  insights: 'view.usage',
  dashboard: 'view.usage',
  limits: 'view.usage',
  router: 'view.router',
  proxmox: 'view.proxmox',
  updates: 'view.updates',
  memory: 'view.memory',
  activity: 'view.activity',
  settings: 'view.settings',
  accounts: 'accounts.manage',
  account: null,
  // Run mode has its own run key on top of the sign-in.
  runs: null,
  goals: null,
};

type Entry = { token: string | null; done: boolean; value: Me | null };
let cache: Entry | null = null;
const listeners = new Set<() => void>();

function isMe(value: unknown): value is Me {
  return !!value && typeof value === 'object' && Array.isArray((value as Me).permissions) && typeof (value as Me).username === 'string';
}

function load(): Entry {
  const token = localStorage.getItem('hq-token');
  if (cache && cache.token === token) return cache;
  const entry: Entry = { token, done: false, value: null };
  cache = entry;
  const finish = (value: Me | null) => {
    if (cache !== entry) return;
    entry.value = value;
    entry.done = true;
    listeners.forEach((listener) => listener());
  };
  Promise.resolve()
    .then(() => fetch('/api/me', { headers: { 'X-HQ-Token': token ?? '' } }))
    .then((response) => (response?.ok ? response.json() : null))
    .then((body: unknown) => finish(isMe(body) ? body : null), () => finish(null));
  return entry;
}

/** Forget the cached answer (tests, and after the signed-in user's own details change). */
export function resetMe() {
  cache = null;
  listeners.forEach((listener) => listener());
}

/** Whether `me` may use `permission`. Admin may do everything. When it isn't known who is signed in (an older
 *  server, or /api/me failed) everything is shown: the server still checks every call. */
export function canDo(me: Me | null | undefined, permission: string | null | undefined) {
  if (!permission || !me) return true;
  return me.admin || me.permissions.includes(permission);
}

export function useMe() {
  const entry = load();
  const [, rerender] = useState(0);
  const seen = entry.done;
  useEffect(() => {
    const listener = () => rerender((n) => n + 1);
    listeners.add(listener);
    // It may have arrived between this render and subscribing.
    if (!seen && load().done) listener();
    return () => { listeners.delete(listener); };
  }, [seen]);
  const me = entry.value;
  return { me, loaded: entry.done, can: (permission: string | null | undefined) => canDo(me, permission) };
}
