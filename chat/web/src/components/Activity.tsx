import { useEffect, useRef, useState } from 'react'
import { Brain, ChevronDown, ChevronRight, Download, MessageCircle, Terminal, X } from 'lucide-react'
import { api, type Agent } from '../api'
import { useStore } from '../store'
import { AgentAvatar } from './bits'

// Live activity: what an agent is doing right now, read from its Claude session log by the bridge
// (messages it was handed, its thinking, the tools it runs, what it wrote). Updates every few seconds.

export type Step = { at: string; kind: 'input' | 'thinking' | 'text' | 'tool'; tool?: string; text: string }
type Activity = { agent_id: string; steps: Step[]; log_updated: number | null; received: number | null; note?: string }

export const activityBus = new EventTarget()  // the store forwards 'activity' events from the server here

function ago(ts: number | null) {
  if (!ts) return 'never'
  const d = Math.max(0, Date.now() / 1000 - ts)
  if (d < 20) return 'just now'
  if (d < 90) return `${Math.round(d)} s ago`
  if (d < 3600) return `${Math.round(d / 60)} min ago`
  if (d < 86400) return `${Math.round(d / 3600)} h ago`
  return `${Math.round(d / 86400)} days ago`
}

function clockOf(at: string) {
  const t = Date.parse(at)
  return isNaN(t) ? '' : new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** One-line summary of the latest step, for headers. */
export function nowDoing(a: Activity | null) {
  const last = a?.steps[a.steps.length - 1]
  if (!last) return ''
  if (last.kind === 'tool') return `${last.tool}: ${last.text}`
  if (last.kind === 'thinking') return 'Thinking…'
  if (last.kind === 'input') return 'Reading a message'
  return 'Writing'
}

export function ActivityPanel({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  const [act, setAct] = useState<Activity | null>(null)
  const [error, setError] = useState('')
  const [showThinking, setShowThinking] = useState(true)
  const [, tick] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    api.get<Activity>(`/api/agents/${agent.id}/activity`).then((a) => alive && setAct(a)).catch((e) => setError(e.message))
    const onEvent = (e: Event) => { const d = (e as CustomEvent).detail as Activity; if (d.agent_id === agent.id) setAct(d) }
    activityBus.addEventListener('activity', onEvent)
    const t = setInterval(() => tick((n) => n + 1), 5000)
    return () => { alive = false; activityBus.removeEventListener('activity', onEvent); clearInterval(t) }
  }, [agent.id])

  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }) }, [act?.steps.length])

  const busy = !!act?.log_updated && Date.now() / 1000 - act.log_updated < 60
  const steps = (act?.steps || []).filter((s) => showThinking || s.kind !== 'thinking')

  return (
    <aside className="panel activity-panel" aria-label={`${agent.name} live activity`}>
      <div className="panel-head">
        <h3><AgentAvatar agent={agent} size={22} dot /> {agent.name}: live</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </div>
      <div className="activity-status">
        <span className={'act-dot' + (busy ? ' busy' : '')} />
        <span>{busy ? <b>Working</b> : 'Idle'} · last change {ago(act?.log_updated ?? null)}</span>
        <label className="act-toggle"><input type="checkbox" checked={showThinking} onChange={(e) => setShowThinking(e.target.checked)} /> Thinking</label>
      </div>
      {busy && nowDoing(act) && <div className="activity-now">Now: {nowDoing(act)}</div>}
      <div className="activity-list" ref={listRef}>
        {error && <p className="error-text pad">{error}</p>}
        {act?.note && <p className="muted small pad">{act.note}</p>}
        {act && !act.steps.length && !act.note && <p className="muted small pad">Nothing yet.</p>}
        {steps.map((s, i) => <StepRow key={i + s.at} s={s} />)}
      </div>
      <p className="muted small activity-foot">This is the agent's whole session: Telegram, Agent HQ Chat, scheduled jobs and other agents, not only this chat. Thinking is shown as the model wrote it.</p>
    </aside>
  )
}

function StepRow({ s }: { s: Step }) {
  const [open, setOpen] = useState(false)
  const long = s.text.length > 220
  const icon = s.kind === 'thinking' ? <Brain size={14} /> : s.kind === 'tool' ? <Terminal size={14} /> : s.kind === 'input' ? <Download size={14} /> : <MessageCircle size={14} />
  const label = s.kind === 'thinking' ? 'Thinking' : s.kind === 'tool' ? s.tool || 'Tool' : s.kind === 'input' ? 'Got' : 'Wrote'
  return (
    <div className={'act-step ' + s.kind}>
      <div className="act-head">{icon}<b>{label}</b><span className="muted small">{clockOf(s.at)}</span>
        {long && <button className="icon-btn small" onClick={() => setOpen(!open)} aria-label={open ? 'Less' : 'More'}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button>}
      </div>
      <div className="act-text">{long && !open ? s.text.slice(0, 220) + '…' : s.text}</div>
    </div>
  )
}

/** Download button with format menu, for a conversation. */
export function ExportMenu({ convId }: { convId: number }) {
  const [open, setOpen] = useState(false)
  const get = (fmt: string) => { setOpen(false); window.location.href = `/api/conversations/${convId}/export?format=${fmt}` }
  return (
    <div className="export-wrap">
      <button className="icon-btn" onClick={() => setOpen(!open)} title="Download this conversation" aria-label="Download this conversation" aria-expanded={open}><Download size={18} /></button>
      {open && (
        <div className="popover" onMouseLeave={() => setOpen(false)}>
          <button onClick={() => get('md')}>Markdown (.md)</button>
          <button onClick={() => get('txt')}>Plain text (.txt)</button>
          <button onClick={() => get('json')}>JSON (.json)</button>
        </div>
      )}
    </div>
  )
}

export function useCanWatch(agent?: Agent) {
  const { s } = useStore()
  if (!agent || !['zylos'].includes(agent.kind)) return false
  return s.me.role === 'admin' || agent.owner_user_id === s.me.id || !!s.me.permissions?.includes('activity.view')
}
