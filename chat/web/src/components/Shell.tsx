import { useEffect, useState } from 'react'
import { useRoute, useStore } from '../store'
import { Sidebar } from './Sidebar'
import { Home } from './Home'
import { ChatView } from './ChatView'
import { TasksBoard } from './Tasks'
import { Directory } from './Directory'
import { Admin } from './Admin'
import { Talk } from './Talk'
import { Menu } from 'lucide-react'

export function Shell({ onSignOut }: { onSignOut: () => void }) {
  const route = useRoute()
  const { s } = useStore()
  const [navOpen, setNavOpen] = useState(false)
  useEffect(() => setNavOpen(false), [route])
  const unread = Object.values(s.convs).reduce((n, c) => n + (c.joined ? c.unread || 0 : 0), 0)
  useEffect(() => { document.title = (unread ? `(${unread}) ` : '') + 'Agent HQ Chat' }, [unread])

  if (route.page === 'talk') return route.agent !== undefined
    ? <Talk key={route.agent} agentId={route.agent} />
    : <Talk key={'c' + route.conv} convId={route.conv} />

  let page
  if (route.page === 'chat') page = <ChatView key={route.id} id={route.id} />
  else if (route.page === 'tasks') page = <TasksBoard />
  else if (route.page === 'directory') page = <Directory />
  else if (route.page === 'admin' && s.me.role === 'admin') page = <Admin />
  else page = <Home />

  return (
    <div className={'shell' + (navOpen ? ' nav-open' : '')}>
      <Sidebar route={route} onSignOut={onSignOut} />
      <div className="nav-scrim" onClick={() => setNavOpen(false)} />
      <main className="main">
        <button className="icon-btn mobile-menu" onClick={() => setNavOpen(true)} aria-label="Menu"><Menu size={20} /></button>
        {!s.online && <div className="offline-bar">Reconnecting…</div>}
        {page}
      </main>
    </div>
  )
}
