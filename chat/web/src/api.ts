export type User = { id: number; username: string; name: string; role: string; disabled: boolean; permissions?: string[] }
export type Agent = {
  id: string; handle: string; name: string; kind: string; grp: string; title: string; department: string
  owner_name: string; owner_user_id: number | null; reports_to: string | null; visibility: string; color: string
  online: boolean; last_seen: number | null; disabled: boolean
  bridge?: Record<string, string>; has_token?: boolean; config?: Record<string, string>; has_api_key?: boolean
}
export type Member = { type: 'user' | 'agent'; id: string }
export type Conversation = {
  id: number; kind: 'dm' | 'channel' | 'task' | 'telegram'; name: string; topic: string; created_by: number | null; created: number
  reply_mode: 'lead' | 'all' | 'mention'; lead_agent: string | null; private: boolean; archived: boolean
  last_message_at: number | null; members: Member[]; joined?: boolean; unread?: number; last_read_id?: number
  preview: { author_type: string; author_id: string; text: string; created: number } | null
  task?: { id: number; status: string } | null
}
export type ReplyRef = { id: number; quote: string; author: string; author_type: string; author_id: string; gone: boolean }
export type FileRef = { id: string; name: string; mime: string; size: number }
export type Message = {
  id: number; conv_id: number; author_type: 'user' | 'agent' | 'system' | 'external'; author_id: string; author_name?: string; spoken?: boolean; reply?: ReplyRef; text: string
  status: 'done' | 'streaming' | 'error'; delivery_id: number | null; files: FileRef[]; created: number; edited: number | null
}
export type Delivery = { id: number; agent_id: string; conv_id: number; state: string; trigger_id: number; error: string | null }
export type Task = {
  id: number; title: string; description: string; status: 'todo' | 'doing' | 'review' | 'done'; priority: 'low' | 'normal' | 'high'
  assignee: string | null; conv_id: number | null; created_by: number | null; due: string | null; created: number; updated: number
}
export type Bootstrap = { hq_url?: string | null; me: User; version: string; agents: Agent[]; users: User[]; conversations: Conversation[]; tasks: Task[] }

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { 'X-Requested-With': 'agent-hq-chat' }, credentials: 'same-origin' }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(data.error || data.detail || `Request failed (${res.status})`, res.status)
  return data as T
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  patch: <T>(p: string, b: unknown) => request<T>('PATCH', p, b),
  put: <T>(p: string, body?: unknown) => request<T>('PUT', p, body),
  del: <T>(p: string) => request<T>('DELETE', p),
  upload: (file: File) => { const f = new FormData(); f.append('file', file); return request<FileRef>('POST', '/api/files', f) },
}
