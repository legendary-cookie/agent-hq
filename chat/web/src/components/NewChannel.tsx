import { useMemo, useState } from 'react'
import { api, type Agent, type Conversation } from '../api'
import { go, useStore } from '../store'
import { AgentAvatar, GROUP_ORDER, Modal, UserAvatar } from './bits'

export const REPLY_MODES = [
  { id: 'lead', title: 'Mentioned agents, otherwise the lead', text: 'Agents you @mention answer. If you mention nobody, the lead agent answers.' },
  { id: 'all', title: 'Every agent answers', text: 'Each agent in the channel replies to every message. Uses more quota.' },
  { id: 'mention', title: 'Only mentioned agents', text: 'Nobody answers unless you @mention them.' },
] as const

export function AgentChecklist({ agents, picked, onChange }: { agents: Agent[]; picked: string[]; onChange: (ids: string[]) => void }) {
  const [q, setQ] = useState('')
  const query = q.toLowerCase()
  const shown = agents.filter((a) => !a.disabled && (!query || `${a.name} ${a.handle} ${a.owner_name} ${a.department}`.toLowerCase().includes(query)))
  return (
    <div className="checklist">
      <input placeholder="Find agents" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="checklist-items">
        {GROUP_ORDER.map((g) => {
          const xs = shown.filter((a) => a.grp === g)
          if (!xs.length) return null
          return (
            <div key={g}>
              <div className="picker-group">{g}</div>
              {xs.map((a) => (
                <label key={a.id} className="check-row">
                  <input type="checkbox" checked={picked.includes(a.id)} onChange={(e) => onChange(e.target.checked ? [...picked, a.id] : picked.filter((x) => x !== a.id))} />
                  <AgentAvatar agent={a} size={24} dot />
                  <span>{a.name} <small className="muted">@{a.handle}{a.owner_name ? ' · ' + a.owner_name : ''}</small></span>
                </label>
              ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function NewChannel({ onClose, privateDefault = false, preselect = [] }: { onClose: () => void; privateDefault?: boolean; preselect?: string[] }) {
  const { s, dispatch } = useStore()
  const agents = useMemo(() => Object.values(s.agents), [s.agents])
  const people = Object.values(s.users).filter((u) => u.id !== s.me.id && !u.disabled)
  const [name, setName] = useState('')
  const [topic, setTopic] = useState('')
  const [picked, setPicked] = useState<string[]>(preselect)
  const [users, setUsers] = useState<number[]>([])
  const [mode, setMode] = useState<'lead' | 'all' | 'mention'>('lead')
  const [lead, setLead] = useState<string>('')
  const [priv, setPriv] = useState(privateDefault)
  const [error, setError] = useState('')
  const leadId = picked.includes(lead) ? lead : picked[0] || ''

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      const c = await api.post<Conversation>('/api/conversations', {
        kind: 'channel', name, topic, agents: picked, users, reply_mode: mode, lead_agent: mode === 'lead' ? leadId || null : null, private: priv,
      })
      dispatch({ t: 'conv', c })
      onClose()
      go(`/c/${c.id}`)
    } catch (err) { setError((err as Error).message) }
  }

  return (
    <Modal title={privateDefault ? 'New group chat' : 'New channel'} onClose={onClose} wide>
      <form className="form" onSubmit={create}>
        <div className="form-grid">
          <div>
            <label>Name<input autoFocus placeholder="e.g. launch-plan" value={name} onChange={(e) => setName(e.target.value)} /></label>
            <label>Topic <span className="muted">(optional, agents see it)</span><input placeholder="What is this channel for?" value={topic} onChange={(e) => setTopic(e.target.value)} /></label>
            <div className="field-label">Who answers</div>
            {REPLY_MODES.map((m) => (
              <label key={m.id} className={'radio-card' + (mode === m.id ? ' on' : '')}>
                <input type="radio" name="mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
                <span><b>{m.title}</b><small>{m.text}</small></span>
              </label>
            ))}
            {mode === 'lead' && picked.length > 0 && (
              <label>Lead agent
                <select value={leadId} onChange={(e) => setLead(e.target.value)}>
                  {picked.map((id) => <option key={id} value={id}>{s.agents[id]?.name}</option>)}
                </select>
              </label>
            )}
            <label className="check-row plain"><input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} /> Private: only people added can see it</label>
          </div>
          <div>
            <div className="field-label">Agents ({picked.length})</div>
            <AgentChecklist agents={agents} picked={picked} onChange={setPicked} />
            {people.length > 0 && <>
              <div className="field-label">People</div>
              <div className="people-pick">
                {people.map((u) => (
                  <label key={u.id} className="check-row">
                    <input type="checkbox" checked={users.includes(u.id)} onChange={(e) => setUsers(e.target.checked ? [...users, u.id] : users.filter((x) => x !== u.id))} />
                    <UserAvatar user={u} size={22} /> {u.name}
                  </label>
                ))}
              </div>
            </>}
          </div>
        </div>
        {error && <div className="error-text">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!name.trim()}>Create</button>
        </div>
      </form>
    </Modal>
  )
}
