import { useMemo, useState } from 'react'
import { CalendarDays, MessageSquare, Plus } from 'lucide-react'
import { api, type Task } from '../api'
import { can, go, timeAgo, useStore } from '../store'
import { AgentAvatar, Modal } from './bits'
import { AgentPicker } from './AgentPicker'

export const TASK_COLUMNS = [
  { id: 'todo', title: 'To do' },
  { id: 'doing', title: 'In progress' },
  { id: 'review', title: 'Review' },
  { id: 'done', title: 'Done' },
] as const

export function TasksBoard() {
  const { s } = useStore()
  const [creating, setCreating] = useState(false)
  const [filter, setFilter] = useState<string>('')
  const [drag, setDrag] = useState<number | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const tasks = useMemo(() => Object.values(s.tasks).filter((t) => !filter || t.assignee === filter).sort((a, b) =>
    (b.priority === 'high' ? 1 : 0) - (a.priority === 'high' ? 1 : 0) || b.updated - a.updated), [s.tasks, filter])
  const assignees = [...new Set(Object.values(s.tasks).map((t) => t.assignee).filter(Boolean))] as string[]

  const drop = (status: string) => {
    if (drag == null) return
    const t = s.tasks[drag]
    setDrag(null); setOver(null)
    if (t && t.status !== status) api.patch(`/api/tasks/${t.id}`, { status }).catch((e) => alert(e.message))
  }

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Tasks</h1><p className="muted">Give work to agents and follow it. Agents move cards themselves as they go.</p></div>
        <div className="page-actions">
          {assignees.length > 0 && (
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">All agents</option>
              {assignees.map((id) => <option key={id} value={id}>{s.agents[id]?.name || id}</option>)}
            </select>
          )}
          {can(s, 'tasks.create') && <button className="btn primary" onClick={() => setCreating(true)}><Plus size={16} /> New task</button>}
        </div>
      </header>
      <div className="board">
        {TASK_COLUMNS.map((col) => {
          const items = tasks.filter((t) => t.status === col.id)
          return (
            <section key={col.id} className={'column' + (over === col.id ? ' over' : '')}
              onDragOver={(e) => { e.preventDefault(); setOver(col.id) }} onDragLeave={() => setOver(null)} onDrop={() => drop(col.id)}>
              <div className="column-head"><span className={'dot st-' + col.id} />{col.title}<span className="muted">{items.length}</span></div>
              {items.map((t) => <TaskCard key={t.id} t={t} onDrag={() => setDrag(t.id)} />)}
              {col.id === 'todo' && <button className="add-card" onClick={() => setCreating(true)}><Plus size={14} /> Add a task</button>}
            </section>
          )
        })}
      </div>
      {creating && <NewTask onClose={() => setCreating(false)} />}
    </div>
  )
}

function TaskCard({ t, onDrag }: { t: Task; onDrag: () => void }) {
  const { s } = useStore()
  const a = t.assignee ? s.agents[t.assignee] : undefined
  const pending = t.conv_id ? s.deliveries[t.conv_id] || [] : []
  const working = pending.some((d) => d.state !== 'queued')
  const waiting = !working && pending.length > 0
  const overdue = t.due && t.status !== 'done' && new Date(t.due + 'T23:59:59') < new Date()
  return (
    <div className="task-card" draggable onDragStart={onDrag} onClick={() => t.conv_id && go(`/c/${t.conv_id}`)}>
      <div className="task-title">{t.priority === 'high' && <span className="prio">High</span>}{t.title}</div>
      {t.description && <div className="task-desc">{t.description}</div>}
      <div className="task-meta">
        {a ? <span className="task-agent"><AgentAvatar agent={a} size={20} dot /> {a.name}</span> : <span className="muted">Unassigned</span>}
        {working && <span className="working-pill">working</span>}
        {waiting && <span className="working-pill waiting" title="The agent has not picked this up yet">waiting</span>}
        {t.due && <span className={'due' + (overdue ? ' late' : '')}><CalendarDays size={12} /> {t.due}</span>}
        <span className="muted small"><MessageSquare size={12} /> {timeAgo(t.updated)}</span>
      </div>
    </div>
  )
}

export function NewTask({ onClose, assignee: initial }: { onClose: () => void; assignee?: string }) {
  const { s, dispatch } = useStore()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [assignee, setAssignee] = useState<string | null>(initial || null)
  const [priority, setPriority] = useState('normal')
  const [due, setDue] = useState('')
  const [error, setError] = useState('')
  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const t = await api.post<Task>('/api/tasks', { title, description, assignee, priority, due: due || null })
      dispatch({ t: 'task', task: t })
      onClose()
    } catch (err) { setError((err as Error).message) }
  }
  return (
    <Modal title="New task" onClose={onClose}>
      <form className="form" onSubmit={create}>
        <label>Title<input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Draft the November newsletter" /></label>
        <label>Details<textarea rows={5} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What should be done, by when, and what a good result looks like" /></label>
        <div className="field-label">Assign to</div>
        <AgentPicker agents={Object.values(s.agents)} value={assignee} onChange={setAssignee} label="Pick an agent" />
        <div className="row2">
          <label>Priority<select value={priority} onChange={(e) => setPriority(e.target.value)}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></label>
          <label>Due<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        </div>
        <p className="muted small">The agent gets the task right away and replies in the task's conversation.</p>
        {error && <div className="error-text">{error}</div>}
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!title.trim()}>Create task</button></div>
      </form>
    </Modal>
  )
}
