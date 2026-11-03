import { useCallback, useEffect, useState } from 'react';
import { changeCounter, subscribeChanges } from './live';
export class ApiError extends Error {
  constructor(
    public status: number,
    message = `Request failed (${status})`,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
const inFlight = new Map<string, Promise<unknown>>();
export function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (
    Object.keys(init).some((key) => key !== 'method') ||
    (init.method ?? 'GET').toUpperCase() !== 'GET'
  ) {
    return request<T>(path, init);
  }
  const key = JSON.stringify([path, sessionStorage.getItem('hq-token'), changeCounter]);
  const existing = inFlight.get(key);
  if (existing) return existing as Promise<T>;
  const promise = request<T>(path, init);
  inFlight.set(key, promise);
  void promise.then(
    () => {
      if (inFlight.get(key) === promise) inFlight.delete(key);
    },
    () => {
      if (inFlight.get(key) === promise) inFlight.delete(key);
    },
  );
  return promise;
}
async function request<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('X-HQ-Token', sessionStorage.getItem('hq-token') ?? '');
  if ((init.method ?? 'GET').toUpperCase() !== 'GET')
    headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: string;
    } | null;
    const error = new ApiError(response.status, body?.error);
    if (response.status === 403 && !path.startsWith('/api/runs')) window.dispatchEvent(new Event('hq-forbidden'));
    throw error;
  }
  return response.json() as Promise<T>;
}
export function useApi<T>(
  path: string | null,
  {
    live = true,
  }: {
    live?: boolean;
  } = {},
): {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload(): void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(false);
  }, [path]);
  useEffect(() => {
    if (!path) return;
    let active = true;
    setLoading(true);
    api<T>(path)
      .then((value) => {
        if (active) {
          setData(value);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  useEffect(() => (path && live ? subscribeChanges(reload) : undefined), [path, live, reload]);
  return { data, error, loading, reload };
}

import type { Run, RunOptions, RunRequest, RunVerdict } from './types';
const runPath = (id: string) => `/api/runs/${encodeURIComponent(id)}`;
const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const runOptions = () => api<RunOptions>('/api/runs/options');
export const prepareRun = (body: RunRequest) => post<{ verdict: RunVerdict; nonce: string | null; expires_at: number | null }>('/api/runs/prepare', body);
export const startRun = (body: RunRequest & { nonce: string }) => post<Run>('/api/runs', body);
export const listRuns = () => api<Run[]>('/api/runs');
export const getRun = (id: string) => api<Run>(runPath(id));
export const runOutput = (id: string) => api<string[]>(`${runPath(id)}/output`);
export const stopRun = (id: string) => post<unknown>(`${runPath(id)}/stop`);
export const runDiff = (id: string) => api<{ diff: string; truncated: boolean; branch: string | null }>(`${runPath(id)}/diff`);
export const mergeRun = (id: string) => post<{ merge_state: string }>(`${runPath(id)}/merge`);
export const discardRun = (id: string) => post<unknown>(`${runPath(id)}/discard`);
