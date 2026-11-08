import { useEffect, useState } from 'react'
import { Copy, Plus } from 'lucide-react'
import { api, type Agent } from '../api'
import { timeAgo, useStore } from '../store'
import { AgentAvatar, GROUP_ORDER, Modal } from './bits'
import { AdminSearch, People, RolesTab } from './AdminPeople'

export function Admin() {
  const [tab, setTab] = useState<'people' | 'roles' | 'agents'>('people')
  const [query, setQuery] = useState('')
  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Admin</h1><p className="muted">People who can sign in, what they're allowed to do, and the agents.</p></div>
        <div className="seg">
          <button className={tab === 'people' ? 'on' : ''} onClick={() => setTab('people')}>People</button>
          <button className={tab === 'roles' ? 'on' : ''} onClick={() => setTab('roles')}>Roles</button>
          <button className={tab === 'agents' ? 'on' : ''} onClick={() => setTab('agents')}>Agents</button>
        </div>
      </header>
      <AdminSearch value={query} onChange={setQuery} />
      {tab === 'people' ? <People query={query} /> : tab === 'roles' ? <RolesTab query={query} /> : <Agents query={query} />}
    </div>
  )
}

function Agents({ query }: { query: string }) {
  const { s } = useStore()
  const [agents, setAgents] = useState<Agent[]>([])
  const [edit, setEdit] = useState<Agent | 'new' | null>(null)
  const load = () => api.get<Agent[]>('/api/admin/agents').then(setAgents)
  useEffect(() => { load() }, [s.agents])
  return (
    <>
      <div className="toolbar"><button className="btn primary" onClick={() => setEdit('new')}><Plus size={16} /> Add agent</button>
        <span className="muted small">{agents.filter((a) => a.online).length} of {agents.length} connected</span></div>
      <table className="table">
        <thead><tr><th>Agent</th><th>Kind</th><th>Group</th><th>Owner</th><th>Who sees it</th><th>Bridge</th><th /></tr></thead>
        <tbody>
          {GROUP_ORDER.flatMap((g) => agents.filter((a) => a.grp === g)).filter((a) => !query.trim() || [a.name, a.handle, a.id, a.grp, a.kind, a.owner_name,
            a.owner_user_id ? s.users[a.owner_user_id]?.name || '' : ''].some((x) => x.toLowerCase().includes(query.trim().toLowerCase()))).map((a) => (
            <tr key={a.id} className={a.disabled ? 'dim' : ''}>
              <td><span className="cell-user"><AgentAvatar agent={a} size={26} dot /> <span>{a.name}<small className="muted"> @{a.handle} · {a.id}</small></span></span></td>
              <td>{a.kind}</td>
              <td>{a.grp}</td>
              <td>{a.owner_user_id ? s.users[a.owner_user_id]?.name : <span className="muted">{a.owner_name || '—'}</span>}</td>
              <td>{a.visibility === 'owner' ? 'Owner and admins' : 'Everyone'}</td>
              <td>{['demo', 'openai'].includes(a.kind) ? <span className="muted">server</span> : a.online ? <span className="ok-text">online{a.bridge?.bridge ? ' · v' + a.bridge.bridge : ''}</span>
                : a.last_seen ? <span className="muted">seen {timeAgo(a.last_seen)} ago</span> : a.has_token ? <span className="muted">never connected</span> : <span className="muted">not set up</span>}</td>
              <td className="right"><button className="link-btn" onClick={() => setEdit(a)}>Edit</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit && <EditAgent agent={edit === 'new' ? null : edit} onClose={() => setEdit(null)} onDone={load} />}
    </>
  )
}

function EditAgent({ agent, onClose, onDone }: { agent: Agent | null; onClose: () => void; onDone: () => void }) {
  const { s } = useStore()
  const [f, setF] = useState({
    id: agent?.id || '', name: agent?.name || '', handle: agent?.handle || '', kind: agent?.kind || 'zylos', grp: agent?.grp || 'Staff',
    title: agent?.title || '', department: agent?.department || '', owner_name: agent?.owner_name || '',
    owner_user_id: agent?.owner_user_id ? String(agent.owner_user_id) : '', visibility: agent?.visibility || 'everyone', color: agent?.color || '#c2410c',
    disabled: agent?.disabled || false,
    base_url: agent?.config?.base_url || '', model: agent?.config?.model || '', system: agent?.config?.system || '', api_key: '',
  })
  const [error, setError] = useState('')
  const [token, setToken] = useState('')
  const set = (k: keyof typeof f, v: string | boolean) => setF({ ...f, [k]: v })
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const body: Record<string, unknown> = { name: f.name, handle: f.handle, kind: f.kind, grp: f.grp, title: f.title, department: f.department,
      owner_name: f.owner_name, owner_user_id: f.owner_user_id ? Number(f.owner_user_id) : null, visibility: f.visibility, color: f.color, disabled: f.disabled }
    if (f.kind === 'openai') body.config = { base_url: f.base_url, model: f.model, system: f.system, ...(f.api_key ? { api_key: f.api_key } : {}) }
    try {
      if (agent) await api.patch(`/api/admin/agents/${agent.id}`, body)
      else await api.post('/api/admin/agents', { id: f.id, ...body })
      onDone(); onClose()
    } catch (err) { setError((err as Error).message) }
  }
  const newToken = async () => {
    if (agent?.has_token && !confirm('Make a new token? The old one stops working, so the bridge on the container must get the new one.')) return
    const r = await api.post<{ token: string }>(`/api/admin/agents/${agent!.id}/token`)
    setToken(r.token)
  }
  const ct = (agent?.id || f.id).match(/^ct(\d+)/)?.[1]
  return (
    <Modal title={agent ? `Edit ${agent.name}` : 'Add agent'} onClose={onClose} wide>
      <form className="form" onSubmit={save}>
        <div className="form-grid">
          <div>
            {!agent && <label>Id (as in HQ, e.g. ct219-hermes)<input value={f.id} onChange={(e) => set('id', e.target.value.toLowerCase())} /></label>}
            <label>Name<input value={f.name} onChange={(e) => set('name', e.target.value)} /></label>
            <label>Handle for @mentions<input value={f.handle} onChange={(e) => set('handle', e.target.value.toLowerCase())} /></label>
            <label>Description<input value={f.title} onChange={(e) => set('title', e.target.value)} placeholder="What this agent is for" /></label>
            <div className="row2">
              <label>Group<select value={f.grp} onChange={(e) => set('grp', e.target.value)}>{GROUP_ORDER.map((g) => <option key={g}>{g}</option>)}</select></label>
              <label>Colour<input type="color" value={f.color || '#c2410c'} onChange={(e) => set('color', e.target.value)} /></label>
            </div>
            <label>Department<input value={f.department} onChange={(e) => set('department', e.target.value)} /></label>
          </div>
          <div>
            <label>Runs on<select value={f.kind} onChange={(e) => set('kind', e.target.value)}>
              <option value="zylos">Zylos (staff and management containers)</option>
              <option value="hermes">Hermes (department containers)</option>
              <option value="openai">OpenAI-compatible API, answered by this server</option>
              <option value="demo">Demo (answers by itself)</option>
            </select></label>
            <label>Owner (person who can use it)<select value={f.owner_user_id} onChange={(e) => set('owner_user_id', e.target.value)}>
              <option value="">Nobody</option>{Object.values(s.users).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select></label>
            <label>Owner name shown<input value={f.owner_name} onChange={(e) => set('owner_name', e.target.value)} /></label>
            <label>Who can use it<select value={f.visibility} onChange={(e) => set('visibility', e.target.value)}>
              <option value="everyone">Everyone</option><option value="owner">Only its owner (and admins)</option>
            </select></label>
            <label className="check-row plain"><input type="checkbox" checked={f.disabled} onChange={(e) => set('disabled', e.target.checked)} /> Turned off (hidden, gets no messages)</label>
            {f.kind === 'openai' && <>
              <label>Base URL<input value={f.base_url} onChange={(e) => set('base_url', e.target.value)} placeholder="http://192.0.2.80:20128/v1" /></label>
              <label>Model<input value={f.model} onChange={(e) => set('model', e.target.value)} placeholder="default" /></label>
              <label>API key {agent?.has_api_key && <span className="muted">(saved; leave empty to keep)</span>}<input type="password" value={f.api_key} onChange={(e) => set('api_key', e.target.value)} autoComplete="off" /></label>
              <label>System prompt<textarea rows={3} value={f.system} onChange={(e) => set('system', e.target.value)} /></label>
            </>}
          </div>
        </div>
        {agent && ['zylos', 'hermes'].includes(f.kind) && (
          <div className="connect-box">
            <b>Connect the container</b>
            <p className="muted small">On the Proxmox host, this makes a token, installs the bridge on the container and starts it. The token never shows on screen.</p>
            <CodeLine text={`pct exec 123 -- cat /opt/agent-chat/deploy/connect-agent.sh | bash -s -- ${ct || '<ctid>'}`} />
            <p className="muted small">Or make a token here for a manual install (shown once):</p>
            <button type="button" className="btn small" onClick={newToken}>{agent.has_token ? 'Replace token' : 'Make token'}</button>
            {token && <CodeLine text={token} />}
          </div>
        )}
        {error && <div className="error-text">{error}</div>}
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Save</button></div>
      </form>
    </Modal>
  )
}

function CodeLine({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  return (
    <div className="code-line"><code>{text}</code>
      <button type="button" className="icon-btn small" onClick={() => { navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200) }} aria-label="Copy">
        {done ? '✓' : <Copy size={14} />}
      </button>
    </div>
  )
}
