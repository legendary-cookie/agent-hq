import { useEffect, useRef, type ReactNode } from 'react'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { X } from 'lucide-react'
import type { Agent, User } from '../api'

marked.setOptions({ gfm: true, breaks: true })

const PALETTE = ['#c2410c', '#0f766e', '#7c3aed', '#be123c', '#1d4ed8', '#4d7c0f', '#a16207', '#0e7490']

function initials(name: string) {
  const parts = name.replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/)
  if (!parts[0]) return '?'
  if (/^\d+$/.test(parts[parts.length - 1]) && parts[parts.length - 1].length <= 3) return parts[parts.length - 1]
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase()
}

function hashColor(key: string) {
  let h = 0
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) | 0
  return PALETTE[Math.abs(h) % PALETTE.length]
}

export function AgentAvatar({ agent, size = 32, dot = false }: { agent?: Agent; size?: number; dot?: boolean }) {
  const name = agent?.name || '?'
  return (
    <span className="avatar agent" style={{ width: size, height: size, fontSize: size * 0.38, background: agent?.color || hashColor(name) }} title={name}>
      {initials(name)}
      {dot && agent && <span className={'presence ' + (agent.online ? 'on' : 'off')} />}
    </span>
  )
}

export function UserAvatar({ user, size = 32 }: { user?: User; size?: number }) {
  const name = user?.name || '?'
  return <span className="avatar user" style={{ width: size, height: size, fontSize: size * 0.38 }} title={name}>{initials(name)}</span>
}

export function Markdown({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const html = DOMPurify.sanitize(marked.parse(text, { async: false }) as string, { ADD_ATTR: ['target'] })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.querySelectorAll('a').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer' })
    el.querySelectorAll('pre').forEach((pre) => {
      if (pre.querySelector('.copy-code')) return
      const b = document.createElement('button')
      b.className = 'copy-code'; b.textContent = 'Copy'
      b.onclick = () => { navigator.clipboard.writeText(pre.querySelector('code')?.textContent || pre.textContent || ''); b.textContent = 'Copied'; setTimeout(() => (b.textContent = 'Copy'), 1500) }
      pre.appendChild(b)
    })
  })
  return <div ref={ref} className="md" dangerouslySetInnerHTML={{ __html: html }} />
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    addEventListener('keydown', on)
    return () => removeEventListener('keydown', on)
  }, [onClose])
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={'modal' + (wide ? ' wide' : '')} role="dialog" aria-label={title}>
        <div className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return <div className="empty">{icon}<h3>{title}</h3>{children && <div className="muted">{children}</div>}</div>
}

export const GROUP_ORDER = ['Management', 'Department', 'Staff', 'Specialist', 'Other']

export function sizeText(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
