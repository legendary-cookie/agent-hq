export interface SessionRow {
  id: string;
  host?: string | null;
  imported: 0 | 1;
  launched_run_id?: string | null;
  harness: string | null;
  provider: string | null;
  model: string | null;
  account_id: string | null;
  project: string | null;
  title: string | null;
  started_at: number | null;
  last_event_at: number | null;
  ended_at: number | null;
  status: string | null;
  parent_id: string | null;
  source_ref: string | null;
  last_kind: string | null;
  last_tool: string | null;
  last_preview: string | null;
  tokens: number;
}
export interface EventRow {
  id: number;
  session_id: string;
  ext_id: string;
  ts: number;
  kind: string;
  tool: string | null;
  preview: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tokens_cache_read: number | null;
  tokens_cache_write: number | null;
  tokens_estimated: number | null;
  model: string | null;
}
export interface LimitWindow {
  window: string | null;
  scope: string | null;
  used_percent: number | null;
  resets_at: number | null;
  source: string;
  stale: boolean;
  age_s: number | null;
  forecast_s: number | null;
  burn_tokens_per_h: number;
}
export interface Balance {
  currency: string;
  total: number;
  granted: number;
  topped_up: number;
  recorded_at: number;
}
export interface LimitCard {
  account_id: string;
  label: string;
  windows: LimitWindow[];
  balance?: Balance;
}
export interface Recommendation {
  provider: string;
  model: string;
  [key: string]: unknown;
}
export interface Overview {
  status_counts: Record<string, number>;
  best_now: Recommendation[] | null;
  value: Value;
  tokens: Tokens;
  alerts: {
    account_id: string;
    window?: string | null;
    scope?: string | null;
    level: string;
    used_percent?: number | null;
    balance?: Balance;
  }[];
}
export interface Value {
  total_usd: number;
  unpriced_tokens: number;
  estimated: boolean;
}
export interface Tokens {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  cache_hit_ratio: number | null;
}
export interface KpiBlock {
  activity: {
    active_agents: number;
    sessions_started: number;
    tasks: number;
    tool_calls: number;
    agent_hours: number;
    average_task_duration: number | null;
  };
  quality: {
    success_rate: number | null;
    errors_per_100_tool_calls: number | null;
    delegation_outcomes: Record<string, Record<string, number>>;
  };
  tokens: Tokens;
  value: Value;
  plan_roi: Record<
    string,
    { value_usd: number; monthly_price_usd: number | null; roi: number | null }
  >;
  quota_use: Record<string, Record<string, number>>;
  routing: {
    delegations_per_provider: Record<string, number>;
    blocked_count: number;
    hours_blocked: Record<string, number>;
  };
}
export interface Kpis extends KpiBlock {
  previous: KpiBlock;
  groups?: Record<string, KpiBlock>;
  top_sessions: { session_id: string; tokens: number; value_usd: number | null }[];
  top_projects: { project: string; tokens: number; value_usd: number | null }[];
  top_models: { model: string; tokens: number }[];
}
export interface SessionDetail {
  session: Omit<SessionRow, 'last_kind' | 'last_tool' | 'last_preview' | 'tokens'>;
  events: EventRow[];
  tasks: {
    id: number;
    session_id: string;
    started_at: number;
    ended_at: number | null;
    outcome: string;
  }[];
  children: Omit<SessionRow, 'last_kind' | 'last_tool' | 'last_preview' | 'tokens'>[];
  value: {
    tokens: number;
    value_usd: number;
    unpriced_tokens: number;
    effective_cost_usd: number | null;
    estimated: boolean;
  };
  account_limit: LimitCard | null;
}
export interface Settings {
  sources: Record<string, { enabled: boolean; paths: string[] }>;
  accounts: {
    id: string;
    provider: string;
    label: string;
    plan: string | null;
    monthly_price_usd: number | null;
    billing_day: number;
    match: Record<string, string>[];
    cap_tokens: Record<string, number>;
  }[];
  prices: Record<
    string,
    { input: number; output: number; cache_read: number; cache_write: number }
  >;
  alerts: { thresholds: number[]; toast: boolean };
  default_skin: string;
  recommend_task_kind: string;
  zylos_previews: boolean;
  deepseek_alert: number;
  price_sources: Record<string, 'yours' | 'litellm' | 'unpriced'>;
}
export interface ReaderHealth {
  state: string;
  tested: 'real' | 'upstream';
  last_pass: number | null;
  warnings: number;
  error: string | null;
  last_error_at: number | null;
}
export interface HostHealth {
  state: string;
  last_pass: number | null;
  bytes: number;
  catching_up: boolean;
  error: string | null;
  notes?: string[];
}
export type Health = Record<string, ReaderHealth | Record<string, HostHealth> | string | number | null> & {
  hosts: Record<string, HostHealth>;
  recommend: string | null;
  importer: ReaderHealth;
};
export interface SeriesRow {
  t: number;
  key: string;
  tokens: number;
  unpriced_tokens: number;
  value_usd: number | null;
  tasks: number;
  errors: number;
}
export interface DelegationRow {
  id: string;
  ts: number;
  provider: string;
  model: string;
  task_kind: string;
  outcome: string;
}

export interface RunOptions {
  run_key_ok?: boolean;
  run_mode: boolean;
  tools: Record<string, { levels: string[]; harness: string; tested: boolean | string; can_ask?: boolean; steer_mode?: string | null }>;
  projects: string[];
  profiles: { id: string; tool: string; account_id: string; max_used_percent: number | null; blocked_until: number | null }[];
  defaults: { level: string; guard_mode: string; time_limit_min: number };
  max_concurrent: number;
}
export interface RunRequest {
  tool: string; model: string | null; project: string; prompt: string; level: string;
  guard_mode: string; profile: string | null; time_limit_min: number;
}
export interface RunVerdict {
  allowed: boolean; reasons: string[]; warnings: string[]; suggestion: Record<string, unknown> | null;
  profile: string | null; earliest_reset: number | null;
}
export interface Run {
  id: string; tool: string; model: string | null; account: string | null; project: string;
  workdir: string; branch: string | null; base_commit: string | null; level: string; guard_mode: string;
  mode: string; status: string; session_id: string | null; prompt_preview: string;
  started_at: number | null; ended_at: number | null; exit_code: number | null;
  time_limit_s: number; merge_state: string | null;
}

export interface Approval { id: string; run_id: string; tool_name: string; summary: string; requested_at: number; expires_at: number; state: string }
