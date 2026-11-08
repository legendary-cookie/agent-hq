import { useEffect, useState } from 'react'
import { Hash, MessageSquare, Search } from 'lucide-react'
import { api, type Message } from '../api'
import { authorName, convAgents, go, timeAgo, useStore } from '../store'
import { AgentAvatar, Modal } from './bits'
import { openChatWith } from './ChatView'

type Hit = Message & { conv_kind: string; conv_name: string }

export function SearchDialog({ onClose }: { onClose: () => void }) {
  const { s, dispatch } = useStore()
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const query = q.trim().toLowerCase()
  useEffect(() => {
    if (query.length < 2) { setHits([]); return }
    const t = setTimeout(() => api.get<Hit[]>('/api/search?q=' + encodeURIComponent(query)).then(setHits).catch(() => {}), 200)
    return () => clearTimeout(t)
  }, [query])
  const agents = query ? Object.values(s.agents).filter((a) => `${a.name} ${a.handle}`.toLowerCase().includes(query)).slice(0, 5) : []
  const convs = query ? Object.values(s.convs).filter((c) => c.name.toLowerCase().includes(query)).slice(0, 6) : []
  const open = (path: string) => { go(path); onClose() }
  return (
    <Modal title="Search" onClose={onClose} wide>
      <div className="search-box big"><Search size={17} /><input autoFocus placeholder="Search messages, chats and agents" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="search-results">
        {agents.length > 0 && <div className="picker-group">Agents</div>}
        {agents.map((a) => <button key={a.id} className="hit" onClick={() => { openChatWith(a.id, dispatch); onClose() }}><AgentAvatar agent={a} size={24} dot /><span><b>{a.name}</b> <small className="muted">new chat</small></span></button>)}
        {convs.length > 0 && <div className="picker-group">Conversations</div>}
        {convs.map((c) => (
          <button key={c.id} className="hit" onClick={() => open(`/c/${c.id}`)}>
            {c.kind === 'channel' ? <Hash size={18} /> : c.kind === 'dm' ? <AgentAvatar agent={convAgents(c, s)[0]} size={24} /> : <MessageSquare size={18} />}
            <span><b>{c.name || 'Chat'}</b></span>
          </button>
        ))}
        {hits.length > 0 && <div className="picker-group">Messages</div>}
        {hits.map((m) => (
          <button key={m.id} className="hit msg" onClick={() => open(`/c/${m.conv_id}`)}>
            <span className="hit-where">{m.conv_kind === 'channel' ? '#' + m.conv_name : m.conv_name || 'Chat'} · {authorName(m, s)} · {timeAgo(m.created)}</span>
            <span className="hit-text">{highlight(m.text, query)}</span>
          </button>
        ))}
        {query.length >= 2 && !agents.length && !convs.length && !hits.length && <div className="muted pad">Nothing found</div>}
      </div>
    </Modal>
  )
}

function highlight(text: string, q: string) {
  const i = text.toLowerCase().indexOf(q)
  if (i < 0) return text.slice(0, 160)
  const start = Math.max(0, i - 60)
  return <>{start > 0 && '…'}{text.slice(start, i)}<mark>{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length, i + q.length + 100)}</>
}
