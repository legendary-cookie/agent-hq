import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import type { Agent } from '../api'
import { AgentAvatar, GROUP_ORDER } from './bits'

export function AgentPicker({ agents, value, onChange, label, align = 'left', up }: {
  agents: Agent[]; value?: string | null; onChange: (id: string) => void; label?: string; align?: 'left' | 'right'; up?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const on = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    addEventListener('mousedown', on)
    return () => removeEventListener('mousedown', on)
  }, [open])
  const current = agents.find((a) => a.id === value)
  const query = q.trim().toLowerCase()
  const shown = agents.filter((a) => !a.disabled && (!query || `${a.name} ${a.handle} ${a.department} ${a.owner_name}`.toLowerCase().includes(query)))
  const groups = GROUP_ORDER.map((g) => [g, shown.filter((a) => a.grp === g)] as const).filter(([, xs]) => xs.length)
  return (
    <div className="picker" ref={box}>
      <button className="picker-btn" onClick={() => setOpen(!open)}>
        {current ? <><AgentAvatar agent={current} size={22} dot /> <span>{current.name}</span></> : <span className="muted">{label || 'Choose an agent'}</span>}
        <ChevronDown size={15} />
      </button>
      {open && (
        <div className={'picker-menu ' + align + (up ? ' up' : '')}>
          <div className="picker-search"><Search size={15} /><input autoFocus placeholder="Find an agent" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <div className="picker-list">
            {groups.map(([g, xs]) => (
              <div key={g}>
                <div className="picker-group">{g}</div>
                {xs.map((a) => (
                  <button key={a.id} onClick={() => { onChange(a.id); setOpen(false); setQ('') }} className={a.id === value ? 'on' : ''}>
                    <AgentAvatar agent={a} size={26} dot />
                    <span className="picker-name">{a.name}<small>@{a.handle}{a.owner_name ? ' · ' + a.owner_name : ''}{a.online ? '' : ' · offline'}</small></span>
                    {a.id === value && <Check size={16} />}
                  </button>
                ))}
              </div>
            ))}
            {!groups.length && <div className="muted pad">No agents match</div>}
          </div>
        </div>
      )}
    </div>
  )
}
