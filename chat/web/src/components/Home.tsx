import { useMemo, useState } from 'react'
import { Hash, KanbanSquare, Users } from 'lucide-react'
import { api, type Conversation, type FileRef } from '../api'
import { can, convAgents, go, useStore } from '../store'
import { AgentAvatar } from './bits'
import { AgentPicker } from './AgentPicker'
import { Composer } from './Composer'
import { NewChannel } from './NewChannel'

const LAST = 'agent-hq-chat:last-agent'

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export function Home() {
  const { s, dispatch } = useStore()
  const agents = useMemo(() => Object.values(s.agents).filter((a) => !a.disabled), [s.agents])
  // Agents this person talks to most, so switching between managers is one click.
  const recent = useMemo(() => {
    const seen: string[] = []
    Object.values(s.convs).filter((c) => c.kind === 'dm' && c.joined).sort((a, b) => (b.last_message_at || b.created) - (a.last_message_at || a.created))
      .forEach((c) => convAgents(c, s).forEach((a) => { if (!seen.includes(a.id)) seen.push(a.id) }))
    const own = agents.filter((a) => a.owner_user_id === s.me.id).map((a) => a.id)
    const managers = agents.filter((a) => a.grp === 'Management').map((a) => a.id)
    return [...new Set([...seen, ...own, ...managers])].map((id) => s.agents[id]).filter(Boolean).slice(0, 8)
  }, [s.convs, agents])
  const [agentId, setAgentId] = useState<string | null>(() => {
    const last = localStorage.getItem(LAST)
    return (last && s.agents[last] ? last : recent[0]?.id) || null
  })
  const [group, setGroup] = useState(false)
  const agent = agentId ? s.agents[agentId] : undefined
  const choose = (id: string) => { setAgentId(id); localStorage.setItem(LAST, id) }

  const start = async (text: string, files: FileRef[]) => {
    if (!agentId) throw new Error('Pick an agent first')
    const c = await api.post<Conversation>('/api/conversations', { kind: 'dm', agent_id: agentId })
    dispatch({ t: 'conv', c })
    await api.post(`/api/conversations/${c.id}/messages`, { text, files: files.map((f) => f.id) })
    go(`/c/${c.id}`)
  }

  return (
    <div className="home">
      <div className="home-inner">
        <h1 className="home-title">{greeting()}, {s.me.name.split(' ')[0]}</h1>
        <p className="muted home-sub">Who do you want to work with?</p>
        <div className="home-picker">
          <AgentPicker agents={agents} value={agentId} onChange={choose} />
          {agent && <span className="muted small">{agent.title || agent.grp}{agent.online ? '' : ' · offline, it will answer when it is back'}</span>}
        </div>
        <Composer onSend={start} autoFocus draftKey="home" placeholder={agent ? `Message ${agent.name}…` : 'Pick an agent, then write your message'} disabled={!agent} />
        {recent.length > 0 && (
          <div className="quick-agents">
            {recent.map((a) => (
              <button key={a.id} className={'quick-agent' + (a.id === agentId ? ' on' : '')} onClick={() => choose(a.id)}>
                <AgentAvatar agent={a} size={22} dot /> {a.name}
              </button>
            ))}
          </div>
        )}
        <div className="home-cards">
          {can(s, 'channels.create') && <button className="home-card" onClick={() => setGroup(true)}><Hash size={18} /><b>Group chat</b><span>Several agents in one conversation</span></button>}
          {can(s, 'tasks.create') && <button className="home-card" onClick={() => go('/tasks')}><KanbanSquare size={18} /><b>Give a task</b><span>Assign work and track it on the board</span></button>}
          <button className="home-card" onClick={() => go('/directory')}><Users size={18} /><b>Browse agents</b><span>Who does what, and who is online</span></button>
        </div>
      </div>
      {group && <NewChannel onClose={() => setGroup(false)} privateDefault preselect={agentId ? [agentId] : []} />}
    </div>
  )
}
