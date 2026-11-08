import { useMemo, useState } from 'react'
import { KanbanSquare, LayoutGrid, MessageSquare, Network, Search } from 'lucide-react'
import type { Agent } from '../api'
import { useStore } from '../store'
import { AgentAvatar, GROUP_ORDER } from './bits'
import { openChatWith } from './ChatView'
import { NewTask } from './Tasks'

export function Directory() {
  const { s } = useStore()
  const [q, setQ] = useState('')
  const [view, setView] = useState<'grid' | 'org'>('grid')
  const [taskFor, setTaskFor] = useState<string | null>(null)
  const query = q.toLowerCase()
  const agents = useMemo(() => Object.values(s.agents).filter((a) => !a.disabled &&
    (!query || `${a.name} ${a.handle} ${a.title} ${a.department} ${a.owner_name}`.toLowerCase().includes(query))), [s.agents, query])
  const online = agents.filter((a) => a.online).length

  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Agents</h1><p className="muted">{agents.length} agents · {online} online</p></div>
        <div className="page-actions">
          <div className="search-box"><Search size={15} /><input placeholder="Search agents, owners, departments" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div className="seg">
            <button className={view === 'grid' ? 'on' : ''} onClick={() => setView('grid')}><LayoutGrid size={15} /> Cards</button>
            <button className={view === 'org' ? 'on' : ''} onClick={() => setView('org')}><Network size={15} /> Org chart</button>
          </div>
        </div>
      </header>
      {view === 'grid' ? GROUP_ORDER.map((g) => {
        const xs = agents.filter((a) => a.grp === g)
        if (!xs.length) return null
        return (
          <section key={g} className="dir-section">
            <h2>{g === 'Department' ? 'Departments' : g === 'Other' ? 'Other' : g + ' agents'}</h2>
            <div className="dir-grid">{xs.map((a) => <AgentCard key={a.id} a={a} onTask={() => setTaskFor(a.id)} />)}</div>
          </section>
        )
      }) : <OrgChart agents={agents} onTask={setTaskFor} />}
      {taskFor && <NewTask assignee={taskFor} onClose={() => setTaskFor(null)} />}
    </div>
  )
}

function AgentCard({ a, onTask }: { a: Agent; onTask: () => void }) {
  const { dispatch } = useStore()
  return (
    <div className="agent-card">
      <div className="agent-card-top">
        <AgentAvatar agent={a} size={44} dot />
        <div><b>{a.name}</b><small className="muted">@{a.handle}</small></div>
      </div>
      <div className="agent-card-info">
        <span>{a.title || a.grp}</span>
        {a.owner_name && <span className="muted">Owner: {a.owner_name}</span>}
        <span className={a.online ? 'ok-text' : 'muted'}>{a.online ? '● Online' : '○ Offline'}</span>
      </div>
      <div className="agent-card-actions">
        <button className="btn small primary" onClick={() => openChatWith(a.id, dispatch)}><MessageSquare size={14} /> Message</button>
        <button className="btn small" onClick={onTask}><KanbanSquare size={14} /> Task</button>
      </div>
    </div>
  )
}

function OrgChart({ agents, onTask }: { agents: Agent[]; onTask: (id: string) => void }) {
  const { dispatch } = useStore()
  const byOwner = new Map<string, Agent[]>()
  agents.filter((a) => a.grp !== 'Department').forEach((a) => {
    const key = a.owner_name || 'No owner yet'
    byOwner.set(key, [...(byOwner.get(key) || []), a])
  })
  const owners = [...byOwner.entries()].sort((a, b) => {
    const m = (xs: Agent[]) => (xs.some((x) => x.grp === 'Management') ? 0 : 1)
    return m(a[1]) - m(b[1]) || a[0].localeCompare(b[0])
  })
  const departments = agents.filter((a) => a.grp === 'Department')
  const Node = ({ a }: { a: Agent }) => (
    <button className="org-node" onClick={() => openChatWith(a.id, dispatch)} onContextMenu={(e) => { e.preventDefault(); onTask(a.id) }} title="Click to message, right-click to give a task">
      <AgentAvatar agent={a} size={26} dot /><span>{a.name}<small>@{a.handle}</small></span>
    </button>
  )
  return (
    <div className="org">
      <div className="org-root"><span className="org-company">Example Company</span></div>
      {departments.length > 0 && (
        <section className="org-branch">
          <h3>Departments</h3>
          <div className="org-row">{departments.map((a) => <Node key={a.id} a={a} />)}</div>
        </section>
      )}
      <section className="org-branch">
        <h3>People and their agents</h3>
        <div className="org-people">
          {owners.map(([owner, xs]) => (
            <div key={owner} className="org-person">
              <div className="org-person-name">{owner}</div>
              <div className="org-person-agents">{xs.sort((a, b) => a.grp.localeCompare(b.grp)).map((a) => <Node key={a.id} a={a} />)}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
