import { useEffect, useState } from 'react'
import { Hash, KanbanSquare, KeyRound, Lock, LogOut, Plus, Search, Send, Settings, SquarePen, Users } from 'lucide-react'
import { api } from '../api'
import { can, convAgents, convTitle, go, useStore, type Route } from '../store'
import { AgentAvatar, Modal, UserAvatar } from './bits'
import { NewChannel } from './NewChannel'
import { SearchDialog } from './SearchDialog'

export function Sidebar({ route, onSignOut }: { route: Route; onSignOut: () => void }) {
  const { s } = useStore()
  const [newChannel, setNewChannel] = useState(false)
  const [search, setSearch] = useState(false)
  const [menu, setMenu] = useState(false)
  const [pw, setPw] = useState(false)
  const convs = Object.values(s.convs).filter((c) => !c.archived)
  const sortKey = (c: typeof convs[0]) => c.last_message_at || c.created
  const channels = convs.filter((c) => c.kind === 'channel').sort((a, b) => Number(b.joined) - Number(a.joined) || a.name.localeCompare(b.name))
  const chats = convs.filter((c) => c.kind === 'dm' && c.joined).sort((a, b) => sortKey(b) - sortKey(a))
  const telegram = convs.filter((c) => c.kind === 'telegram' && c.joined).sort((a, b) => sortKey(b) - sortKey(a))
  const [tgOpen, setTgOpen] = useState(false)
  const openTasks = Object.values(s.tasks).filter((t) => t.status !== 'done').length
  const active = route.page === 'chat' ? route.id : null

  const signOut = async () => { await api.post('/api/logout').catch(() => {}); onSignOut() }

  return (
    <aside className="sidebar">
      <div className="side-top">
        <button className="brand" onClick={() => go('/')}><img src="/icon.svg" alt="" /> Agent HQ Chat</button>
        <button className="icon-btn" onClick={() => setSearch(true)} title="Search (Ctrl+K)" aria-label="Search"><Search size={18} /></button>
      </div>
      {s.hq_url && <a className="nav-item" href={s.hq_url}>Agent HQ ↗</a>}
      <button className={'nav-item new-chat' + (route.page === 'home' ? ' active' : '')} onClick={() => go('/')}><SquarePen size={17} /> New chat</button>
      <button className={'nav-item' + (route.page === 'tasks' ? ' active' : '')} onClick={() => go('/tasks')}>
        <KanbanSquare size={17} /> Tasks {openTasks > 0 && <span className="count">{openTasks}</span>}
      </button>
      <button className={'nav-item' + (route.page === 'directory' ? ' active' : '')} onClick={() => go('/directory')}><Users size={17} /> Agents</button>
      {s.me.role === 'admin' && <button className={'nav-item' + (route.page === 'admin' ? ' active' : '')} onClick={() => go('/admin')}><Settings size={17} /> Admin</button>}

      <div className="side-scroll">
        <div className="side-section">
          <span>Channels</span>
          {can(s, 'channels.create') && <button className="icon-btn small" onClick={() => setNewChannel(true)} title="New channel" aria-label="New channel"><Plus size={15} /></button>}
        </div>
        {channels.length === 0 && can(s, 'channels.create') && <button className="side-hint" onClick={() => setNewChannel(true)}>Start a channel with several agents</button>}
        {channels.map((c) => (
          <button key={c.id} className={'conv-item' + (active === c.id ? ' active' : '') + (c.unread && c.joined ? ' unread' : '') + (c.joined ? '' : ' dim')} onClick={() => go(`/c/${c.id}`)}>
            {c.private ? <Lock size={15} className="conv-icon" /> : <Hash size={15} className="conv-icon" />}
            <span className="conv-name">{c.name}</span>
            {c.joined && !!c.unread && <span className="badge">{c.unread}</span>}
          </button>
        ))}

        <div className="side-section"><span>Chats</span></div>
        {chats.length === 0 && <div className="side-hint static">Your chats with agents show here</div>}
        {chats.map((c) => {
          const agent = convAgents(c, s)[0]
          return (
            <button key={c.id} className={'conv-item' + (active === c.id ? ' active' : '') + (c.unread ? ' unread' : '')} onClick={() => go(`/c/${c.id}`)}>
              <AgentAvatar agent={agent} size={20} />
              <span className="conv-name">{convTitle(c, s)}</span>
              {!!c.unread && <span className="badge">{c.unread}</span>}
            </button>
          )
        })}

        {telegram.length > 0 && <>
          <button className="side-section side-toggle" onClick={() => setTgOpen(!tgOpen)} aria-expanded={tgOpen}>
            <span>Telegram history</span><span className="muted small">{tgOpen ? 'Hide' : telegram.length}</span>
          </button>
          {tgOpen && telegram.map((c) => {
            const agent = convAgents(c, s)[0]
            return (
              <button key={c.id} className={'conv-item' + (active === c.id ? ' active' : '')} onClick={() => go(`/c/${c.id}`)} title={agent ? `${agent.name} on Telegram` : undefined}>
                <Send size={14} className="conv-icon tg" />
                <span className="conv-name">{c.name.replace(/ · Telegram( group)?$/, '')}{agent && <small className="muted"> · {agent.name}</small>}</span>
              </button>
            )
          })}
        </>}
      </div>

      <div className="side-foot">
        <button className="me" onClick={() => setMenu(!menu)}>
          <UserAvatar user={s.me} size={28} />
          <span className="me-name">{s.me.name}<small>{s.me.role === 'admin' ? 'Admin' : '@' + s.me.username}</small></span>
        </button>
        {menu && (
          <div className="popover up" onMouseLeave={() => setMenu(false)}>
            <button onClick={() => { setPw(true); setMenu(false) }}><KeyRound size={15} /> Change password</button>
            <button onClick={signOut}><LogOut size={15} /> Sign out</button>
            <div className="popover-note">Version {s.version}</div>
          </div>
        )}
      </div>
      {newChannel && <NewChannel onClose={() => setNewChannel(false)} />}
      {search && <SearchDialog onClose={() => setSearch(false)} />}
      {pw && <PasswordDialog onClose={() => setPw(false)} />}
      <KeyboardShortcuts onSearch={() => setSearch(true)} />
    </aside>
  )
}

function KeyboardShortcuts({ onSearch }: { onSearch: () => void }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); onSearch() }
    }
    addEventListener('keydown', on)
    return () => removeEventListener('keydown', on)
  }, [onSearch])
  return null
}

function PasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState('')
  const [ok, setOk] = useState(false)
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    try { await api.post('/api/me/password', { current, new: next }); setOk(true); setMsg('Password changed. Other browsers were signed out.') }
    catch (err) { setMsg((err as Error).message) }
  }
  return (
    <Modal title="Change password" onClose={onClose}>
      <form className="form" onSubmit={save}>
        <label>Current password<input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" /></label>
        <label>New password (10+ characters)<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></label>
        {msg && <div className={ok ? 'ok-text' : 'error-text'}>{msg}</div>}
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Close</button><button className="btn primary" disabled={!current || next.length < 10}>Save</button></div>
      </form>
    </Modal>
  )
}
