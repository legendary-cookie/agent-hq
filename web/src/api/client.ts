import { useCallback, useEffect, useState } from 'react';
import { subscribeChanges } from './live';
export class ApiError extends Error { constructor(public status: number, message = `Request failed (${status})`) { super(message); this.name = 'ApiError'; } }
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers); headers.set('X-HQ-Token', sessionStorage.getItem('hq-token') ?? '');
  if ((init.method ?? 'GET').toUpperCase() !== 'GET') headers.set('Content-Type','application/json');
  const response = await fetch(path, {...init, headers});
  if (!response.ok) { const body = await response.json().catch(() => null) as {error?: string} | null; const error = new ApiError(response.status, body?.error); if (response.status === 403) window.dispatchEvent(new Event('hq-forbidden')); throw error; }
  return response.json() as Promise<T>;
}
export function useApi<T>(path: string | null): { data: T | null; error: ApiError | null; loading: boolean; reload(): void } {
  const [data,setData] = useState<T | null>(null); const [error,setError] = useState<ApiError | null>(null); const [loading,setLoading] = useState(false); const [revision,setRevision] = useState(0);
  const reload = useCallback(() => setRevision(n => n + 1), []);
  useEffect(() => { if (!path) return; let active=true; setLoading(true); api<T>(path).then(value => { if(active) { setData(value); setError(null); } }).catch((err: unknown) => { if(active) setError(err instanceof ApiError ? err : new ApiError(0, String(err))); }).finally(() => { if(active) setLoading(false); }); return () => { active=false; }; }, [path,revision]);
  useEffect(() => path ? subscribeChanges(reload) : undefined, [path,reload]);
  return {data,error,loading,reload};
}
