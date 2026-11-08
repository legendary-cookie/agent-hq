import { createContext, useContext, useEffect, useReducer, useState, type ReactNode } from 'react'
import { activityBus } from './components/Activity'
import { api, type Agent, type Bootstrap, type Conversation, type Delivery, type Message, type Task, type User } from './api'

export type State = {
  me: User; version: string; hq_url?: string | null
  agents: Record<string, Agent>; users: Record<number, User>; convs: Record<number, Conversation>
  messages: Record<number, Message[]>; more: Record<number, boolean>; deliveries: Record<number, Delivery[]>
  tasks: Record<number, Task>; failed: Record<number, Delivery[]>; online: boolean
}

type Action =
  | { t: 'boot'; b: Bootstrap }
  | { t: 'conv'; c: Conversation }
  | { t: 'conv_deleted'; id: number }
  | { t: 'loaded'; conv: number; messages: Message[]; deliveries: Delivery[]; more: boolean; older?: boolean }
  | { t: 'message'; m: Message }
  | { t: 'message_deleted'; id: number; conv: number }
  | { t: 'delivery'; d: Delivery }
  | { t: 'agent'; a: Agent }
  | { t: 'agent_deleted'; id: string }
  | { t: 'task'; task: Task }
  | { t: 'task_deleted'; id: number }
  | { t: 'read'; conv: number; last: number }
  | { t: 'online'; v: boolean }

const ACTIVE = new Set(['queued', 'sent', 'working'])
const byId = <T extends { id: number | string }>(xs: T[]) => Object.fromEntries(xs.map((x) => [x.id, x]))

function reducer(s: State, a: Action): State {
  switch (a.t) {
    case 'boot':
      return { ...s, me: a.b.me, version: a.b.version, hq_url: a.b.hq_url, agents: byId(a.b.agents), users: byId(a.b.users), convs: byId(a.b.conversations), tasks: byId(a.b.tasks) }
    case 'conv': {
      const old = s.convs[a.c.id]
      // Broadcasts carry no per-person fields; keep ours.
      const c = { ...a.c, joined: a.c.joined ?? (old?.joined ?? a.c.members.some((m) => m.type === 'user' && m.id === String(s.me.id))),
        unread: a.c.unread ?? old?.unread ?? 0, last_read_id: a.c.last_read_id ?? old?.last_read_id ?? 0 }
      return { ...s, convs: { ...s.convs, [c.id]: c } }
    }
    case 'conv_deleted': {
      const convs = { ...s.convs }; delete convs[a.id]
      return { ...s, convs }
    }
    case 'loaded': {
      const prev = s.messages[a.conv] || []
      const messages = a.older ? [...a.messages, ...prev] : a.messages
      return { ...s, messages: { ...s.messages, [a.conv]: messages }, more: { ...s.more, [a.conv]: a.more },
        deliveries: a.older ? s.deliveries : { ...s.deliveries, [a.conv]: a.deliveries } }
    }
    case 'message': {
      const m = a.m
      const list = s.messages[m.conv_id]
      let convs = s.convs
      const c = s.convs[m.conv_id]
      if (c) {
        const mine = m.author_type === 'user' && m.author_id === String(s.me.id)
        const isNew = !list || !list.some((x) => x.id === m.id)
        convs = { ...convs, [c.id]: { ...c, last_message_at: m.created, preview: { author_type: m.author_type, author_id: m.author_id, text: m.text.slice(0, 140), created: m.created },
          unread: (c.unread || 0) + (isNew && !mine && m.author_type !== 'system' && c.joined ? 1 : 0) } }
      }
      if (!list) return { ...s, convs }
      const i = list.findIndex((x) => x.id === m.id)
      const next = i >= 0 ? list.map((x) => (x.id === m.id ? m : x)) : [...list, m]
      return { ...s, convs, messages: { ...s.messages, [m.conv_id]: next } }
    }
    case 'message_deleted': {
      const list = s.messages[a.conv]
      return list ? { ...s, messages: { ...s.messages, [a.conv]: list.filter((x) => x.id !== a.id) } } : s
    }
    case 'delivery': {
      const d = a.d
      const list = (s.deliveries[d.conv_id] || []).filter((x) => x.id !== d.id)
      const failed = (s.failed[d.conv_id] || []).filter((x) => x.id !== d.id)
      return { ...s, deliveries: { ...s.deliveries, [d.conv_id]: ACTIVE.has(d.state) ? [...list, d] : list },
        failed: { ...s.failed, [d.conv_id]: d.state === 'error' ? [...failed, d] : failed } }
    }
    case 'agent':
      return { ...s, agents: { ...s.agents, [a.a.id]: { ...s.agents[a.a.id], ...a.a } } }
    case 'agent_deleted': {
      const agents = { ...s.agents }; delete agents[a.id]
      return { ...s, agents }
    }
    case 'task':
      return { ...s, tasks: { ...s.tasks, [a.task.id]: a.task } }
    case 'task_deleted': {
      const tasks = { ...s.tasks }; delete tasks[a.id]
      return { ...s, tasks }
    }
    case 'read': {
      const c = s.convs[a.conv]
      return c ? { ...s, convs: { ...s.convs, [a.conv]: { ...c, unread: 0, last_read_id: Math.max(c.last_read_id || 0, a.last) } } } : s
    }
    case 'online':
      return { ...s, online: a.v }
  }
}

type Ctx = { s: State; dispatch: (a: Action) => void; reload: () => Promise<void> }
const StoreCtx = createContext<Ctx | null>(null)

export function useStore() {
  const ctx = useContext(StoreCtx)
  if (!ctx) throw new Error('no store')
  return ctx
}

export function StoreProvider({ boot, children }: { boot: Bootstrap; children: ReactNode }) {
  const [s, dispatch] = useReducer(reducer, null, () => reducer({
    me: boot.me, version: boot.version, hq_url: boot.hq_url, agents: {}, users: {}, convs: {}, messages: {}, more: {}, deliveries: {}, tasks: {}, failed: {}, online: true,
  }, { t: 'boot', b: boot }))

  const reload = async () => dispatch({ t: 'boot', b: await api.get<Bootstrap>('/api/bootstrap') })

  useEffect(() => {
    let es: EventSource | null = null
    let stopped = false
    let wasDown = false
    const connect = () => {
      es = new EventSource('/api/events')
      es.onopen = () => {
        dispatch({ t: 'online', v: true })
        if (wasDown) { wasDown = false; reload().catch(() => {}) }
      }
      es.onerror = () => { dispatch({ t: 'online', v: false }); wasDown = true }
      es.onmessage = (e) => {
        const { type, data } = JSON.parse(e.data)
        if (type === 'message' || type === 'message_update') dispatch({ t: 'message', m: data })
        else if (type === 'message_deleted') dispatch({ t: 'message_deleted', id: data.id, conv: data.conv_id })
        else if (type === 'conversation') dispatch({ t: 'conv', c: data })
        else if (type === 'conversation_deleted') dispatch({ t: 'conv_deleted', id: data.id })
        else if (type === 'delivery') dispatch({ t: 'delivery', d: data })
        else if (type === 'agent') dispatch({ t: 'agent', a: data })
        else if (type === 'agent_deleted') dispatch({ t: 'agent_deleted', id: data.id })
        else if (type === 'task') dispatch({ t: 'task', task: data })
        else if (type === 'task_deleted') dispatch({ t: 'task_deleted', id: data.id })
        else if (type === 'activity') activityBus.dispatchEvent(new CustomEvent('activity', { detail: data }))
      }
    }
    connect()
    return () => { stopped = true; es?.close(); void stopped }
  }, [])

  return <StoreCtx.Provider value={{ s, dispatch, reload }}>{children}</StoreCtx.Provider>
}

// ------------------------------------------------------------ hash routes
export type Route = { page: 'home' } | { page: 'chat'; id: number } | { page: 'talk'; agent: string } | { page: 'talk'; conv: number; agent?: undefined } | { page: 'tasks' } | { page: 'directory' } | { page: 'admin' }

function parse(hash: string): Route {
  const m = hash.match(/^#\/c\/(\d+)/)
  if (m) return { page: 'chat', id: Number(m[1]) }
  const tc = hash.match(/^#\/talk\/c\/(\d+)/)
  if (tc) return { page: 'talk', conv: Number(tc[1]) }
  const t = hash.match(/^#\/talk\/([^/?#]+)/)
  if (t) return { page: 'talk', agent: decodeURIComponent(t[1]) }
  if (hash.startsWith('#/tasks')) return { page: 'tasks' }
  if (hash.startsWith('#/directory')) return { page: 'directory' }
  if (hash.startsWith('#/admin')) return { page: 'admin' }
  return { page: 'home' }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(location.hash))
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return route
}

export const go = (path: string) => { location.hash = path }

// ------------------------------------------------------------ small helpers
/** What the signed-in person may do (admins: everything). The server checks the same rules. */
export function can(s: State, perm: string) {
  return s.me.role === 'admin' || !!s.me.permissions?.includes(perm)
}

export function convTitle(c: Conversation, s: State) {
  if (c.kind === 'channel') return c.name
  if (c.kind === 'task') return c.name || 'Task'
  return c.name || 'New chat'
}

export function convAgents(c: Conversation, s: State) {
  return c.members.filter((m) => m.type === 'agent').map((m) => s.agents[m.id]).filter(Boolean)
}

export function authorName(m: { author_type: string; author_id: string }, s: State) {
  if (m.author_type === 'agent') return s.agents[m.author_id]?.name || m.author_id
  if (m.author_type === 'user') return s.users[Number(m.author_id)]?.name || 'Someone'
  if (m.author_type === 'external') return (m as { author_name?: string }).author_name || 'Telegram'
  return 'System'
}

export function timeAgo(ts: number) {
  const d = Date.now() / 1000 - ts
  if (d < 60) return 'now'
  if (d < 3600) return `${Math.floor(d / 60)}m`
  if (d < 86400) return `${Math.floor(d / 3600)}h`
  if (d < 7 * 86400) return `${Math.floor(d / 86400)}d`
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** "Today", "Yesterday", "Monday" (this week), else "Mon, 3 Nov" (this year) or "3 Nov 2025". */
export function dayLabel(ts: number) {
  const d = new Date(ts * 1000), today = new Date()
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((start(today) - start(d)) / 86400000)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days > 1 && days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' })
  return d.toLocaleDateString(undefined, d.getFullYear() === today.getFullYear()
    ? { weekday: 'short', day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' })
}

export function dayKey(ts: number) {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

/** Full date and time, for hover titles. */
export function fullTime(ts: number) {
  return new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}
