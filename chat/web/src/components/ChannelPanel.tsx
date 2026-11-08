import { useState } from 'react'
import { Crown, X } from 'lucide-react'
import { api, type Conversation, type Task } from '../api'
import { convAgents, go, useStore } from '../store'
import { AgentAvatar, UserAvatar } from './bits'
import { AgentChecklist, REPLY_MODES } from './NewChannel'
import { AgentPicker } from './AgentPicker'

export function ChannelPanel({ c, task, onClose }: { c: Conversation; task?: Task; onClose: () => void }) {
  const { s, dispatch } = useStore()
  const [adding, setAdding] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [error, setError] = useState('')
  const agents = convAgents(c, s)
  const people = c.members.filter((m) => m.type === 'user').map((m) => s.users[Number(m.id)]).filter(Boolean)
  const canManage = s.me.role === 'admin' || c.created_by === s.me.id || c.joined

  const run = async (p: Promise<unknown>) => {
    setError('')
    try {
      const r = await p
      if (r && typeof r === 'object' && 'members' in (r as object)) dispatch({ t: 'conv', c: r as Conversation })
    } catch (e) { setError((e as Error).message) }
  }
  const patch = (body: object) => run(api.patch(`/api/conversations/${c.id}`, body))
  const remove = async () => {
    if (!confirm(c.kind === 'task' ? 'Delete this task and its conversation?' : 'Delete this conversation for everyone?')) return
    if (task) await api.del(`/api/tasks/${task.id}`)
    else await api.del(`/api/conversations/${c.id}`)
    dispatch({ t: 'conv_deleted', id: c.id })
    go(c.kind === 'task' ? '/tasks' : '/')
  }
  const rename = () => {
    const name = prompt(c.kind === 'channel' ? 'Channel name' : 'Chat title', c.name)
    if (name !== null) patch({ name })
  }

  return (
    <aside className="panel">
      <div className="panel-head"><h3>{c.kind === 'channel' ? 'Channel' : c.kind === 'task' ? 'Task' : 'Chat'} details</h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
      <div className="panel-body">
        {task && <TaskDetails task={task} />}
        {c.kind !== 'task' && <button className="btn small" onClick={rename}>Rename</button>}
        {c.kind === 'channel' && <>
          <label className="field">Topic
            <input defaultValue={c.topic} placeholder="What is this channel for?" onBlur={(e) => e.target.value !== c.topic && patch({ topic: e.target.value })} />
          </label>
          <div className="field-label">Who answers</div>
          {REPLY_MODES.map((m) => (
            <label key={m.id} className={'radio-card compact' + (c.reply_mode === m.id ? ' on' : '')}>
              <input type="radio" checked={c.reply_mode === m.id} onChange={() => patch({ reply_mode: m.id })} disabled={!canManage} />
              <span><b>{m.title}</b></span>
            </label>
          ))}
        </>}

        <div className="field-label">Agents ({agents.length})</div>
        {agents.map((a) => (
          <div key={a.id} className="member-row">
            <AgentAvatar agent={a} size={28} dot />
            <span className="member-name">{a.name}<small>@{a.handle} · {a.online ? 'online' : 'offline'}</small></span>
            {c.kind === 'channel' && (c.lead_agent === a.id
              ? <span className="lead-tag" title="Lead agent"><Crown size={13} /> Lead</span>
              : <button className="link-btn" onClick={() => patch({ lead_agent: a.id })}>Make lead</button>)}
            {c.kind === 'channel' && canManage && <button className="icon-btn small" title="Remove" onClick={() => run(api.del(`/api/conversations/${c.id}/members/agent/${a.id}`))}><X size={14} /></button>}
          </div>
        ))}
        {c.kind === 'channel' && canManage && (adding ? (
          <div className="add-box">
            <AgentChecklist agents={Object.values(s.agents).filter((a) => !agents.some((x) => x.id === a.id))} picked={picked} onChange={setPicked} />
            <div className="form-actions">
              <button className="btn small" onClick={() => setAdding(false)}>Cancel</button>
              <button className="btn primary small" disabled={!picked.length} onClick={() => { run(api.post(`/api/conversations/${c.id}/members`, { agents: picked })); setPicked([]); setAdding(false) }}>Add {picked.length || ''}</button>
            </div>
          </div>
        ) : <button className="btn small" onClick={() => setAdding(true)}>Add agents</button>)}

        {c.kind !== 'dm' && <>
          <div className="field-label">People ({people.length})</div>
          {people.map((u) => (
            <div key={u.id} className="member-row">
              <UserAvatar user={u} size={28} />
              <span className="member-name">{u.name}<small>@{u.username}</small></span>
              {c.kind === 'channel' && canManage && <button className="icon-btn small" title={u.id === s.me.id ? 'Leave' : 'Remove'} onClick={() => run(api.del(`/api/conversations/${c.id}/members/user/${u.id}`))}><X size={14} /></button>}
            </div>
          ))}
          {c.kind === 'channel' && canManage && <AddPerson c={c} onAdd={(id) => run(api.post(`/api/conversations/${c.id}/members`, { users: [id] }))} />}
        </>}

        {c.kind === 'channel' && <label className="check-row plain"><input type="checkbox" checked={c.private} onChange={(e) => patch({ private: e.target.checked })} disabled={!canManage} /> Private channel</label>}
        {error && <div className="error-text">{error}</div>}
        <div className="panel-danger">
          {c.kind !== 'task' && <button className="btn small" onClick={() => patch({ archived: !c.archived })}>{c.archived ? 'Unarchive' : 'Archive'}</button>}
          {(s.me.role === 'admin' || c.created_by === s.me.id) && <button className="btn small danger" onClick={remove}>Delete</button>}
        </div>
      </div>
    </aside>
  )
}

function AddPerson({ c, onAdd }: { c: Conversation; onAdd: (id: number) => void }) {
  const { s } = useStore()
  const inside = new Set(c.members.filter((m) => m.type === 'user').map((m) => Number(m.id)))
  const rest = Object.values(s.users).filter((u) => !inside.has(u.id) && !u.disabled)
  if (!rest.length) return null
  return (
    <select className="add-person" value="" onChange={(e) => e.target.value && onAdd(Number(e.target.value))}>
      <option value="">Add a person…</option>
      {rest.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
    </select>
  )
}

function TaskDetails({ task }: { task: Task }) {
  const { s } = useStore()
  const set = (body: object) => api.patch(`/api/tasks/${task.id}`, body).catch((e) => alert(e.message))
  return (
    <div className="task-details">
      <label className="field">Title<input defaultValue={task.title} onBlur={(e) => e.target.value.trim() && e.target.value !== task.title && set({ title: e.target.value })} /></label>
      <label className="field">Details<textarea rows={4} defaultValue={task.description} onBlur={(e) => e.target.value !== task.description && set({ description: e.target.value })} /></label>
      <div className="field-label">Assigned to</div>
      <AgentPicker agents={Object.values(s.agents)} value={task.assignee} onChange={(assignee) => set({ assignee })} label="Nobody yet" />
      <div className="row2">
        <label className="field">Priority
          <select value={task.priority} onChange={(e) => set({ priority: e.target.value })}>
            <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option>
          </select>
        </label>
        <label className="field">Due<input type="date" value={task.due || ''} onChange={(e) => set({ due: e.target.value })} /></label>
      </div>
    </div>
  )
}
