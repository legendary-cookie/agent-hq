import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { api, ApiError, type Bootstrap } from './api'
import { StoreProvider } from './store'
import { Shell } from './components/Shell'
import { Login } from './components/Login'
import './styles.css'

function Root() {
  const [boot, setBoot] = useState<Bootstrap | null | undefined>(undefined)
  const load = () => api.get<Bootstrap>('/api/bootstrap').then(setBoot).catch((e) => {
    if (e instanceof ApiError && e.status === 401) setBoot(null)
    else setTimeout(load, 3000)
  })
  useEffect(() => { load() }, [])
  if (boot === undefined) return <div className="splash"><img src="/icon.svg" alt="" /></div>
  if (boot === null) return <Login onDone={load} />
  return <StoreProvider boot={boot}><Shell onSignOut={() => setBoot(null)} /></StoreProvider>
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>)
