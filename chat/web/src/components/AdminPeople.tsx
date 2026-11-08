import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Eye, Plus, RefreshCw, Search, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import { Modal, UserAvatar } from './bits'

// Admin → People and Roles: search, Management / Staff sections, one-time password copy,
// roles as permission checkboxes, and per-person changes on top of their role.

export type Person = {
  id: number; username: string; name: string; role: string; disabled: boolean; team: string
  overrides: Record<string, boolean>; has_password_copy: boolean; permissions: string[]
}
export type Role = { id: string; label: string; description: string; permissions: string[]; builtin: boolean; locked: boolean }
export type Perm = { id: string; group: string; label: string; description: string }
type Roles = { roles: Role[]; catalog: Perm[] }

const TEAMS = [{ id: 'Management', label: 'Management' }, { id: 'Staff', label: 'Staff' }, { id: '', label: 'Others' }]

async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text) } catch {
    const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove()
  }
}

export function People({ query }: { query: string }) {
  const { s, reload } = useStore()
  const [people, setPeople] = useState<Person[]>([])
  const [roles, setRoles] = useState<Roles>({ roles: [], catalog: [] })
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)
  const [shown, setShown] = useState<Record<number, string>>({})
  const [copied, setCopied] = useState<number | null>(null)
  const load = () => Promise.all([api.get<Person[]>('/api/admin/users').then(setPeople), api.get<Roles>('/api/admin/roles').then(setRoles)])
  useEffect(() => { load() }, [])
  const roleLabel = (id: string) => roles.roles.find((r) => r.id === id)?.label || id
  const patch = async (p: Person, body: object) => {
    try { await api.patch(`/api/admin/users/${p.id}`, body); await load(); await reload() } catch (e) { alert((e as Error).message) }
  }

  const ownAgents = (p: Person) => Object.values(s.agents).filter((a) => a.owner_user_id === p.id).map((a) => a.name)
  const q = query.trim().toLowerCase()
  const match = (p: Person) => !q || [p.name, p.username, roleLabel(p.role), p.team, ...ownAgents(p)].some((x) => x.toLowerCase().includes(q))
  const visible = people.filter(match)

  const getPassword = async (p: Person) => shown[p.id] || null
  const show = async (p: Person) => {
    if (shown[p.id]) { const n = { ...shown }; delete n[p.id]; setShown(n); return }
    const pw = await getPassword(p)
    if (pw) setShown({ ...shown, [p.id]: pw })
  }
  const copy = async (p: Person) => {
    const pw = shown[p.id] || await getPassword(p)
    if (!pw) return
    await copyText(pw)
    setCopied(p.id); setTimeout(() => setCopied(null), 1500)
  }
  const regenerate = async (p: Person) => {
    if (!confirm(`Make a new password for ${p.name}? The old one stops working and they are signed out.`)) return
    try {
      const r = await api.post<{ password: string }>(`/api/admin/users/${p.id}/new-password`)
      setShown({ ...shown, [p.id]: r.password }); await load()
    } catch (e) { alert((e as Error).message) }
  }
  const copyAll = async () => {
    const lines: string[] = []
    for (const p of visible.filter((x) => !x.disabled && shown[x.id])) {
      const pw = shown[p.id] || await getPassword(p)
      if (pw) lines.push(`${p.name}\t${p.username}\t${pw}`)
    }
    await copyText(lines.join('\n'))
    alert(`Copied ${lines.length} logins (name, username, password), one per line. Paste them somewhere private.`)
  }
  return (
    <>
      <div className="toolbar">
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} /> Add person</button>
        <button className="btn" onClick={copyAll} title="Name, username and password of everyone shown, one per line"><Copy size={15} /> Copy all logins</button>
        <span className="muted small">{visible.length} of {people.length}</span>
      </div>
      {TEAMS.map((t) => {
        const list = visible.filter((p) => (p.team || '') === t.id)
        if (!list.length) return null
        return (
          <section key={t.id} className="admin-section">
            <h3>{t.label} <span className="muted small">{list.length}</span></h3>
            <table className="table">
              <thead><tr><th>Name</th><th>Username</th><th>Password</th><th>Role</th><th>Agents</th><th>Status</th><th /></tr></thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.id} className={p.disabled ? 'dim' : ''}>
                    <td><span className="cell-user"><UserAvatar user={p} size={26} /> {p.name}</span></td>
                    <td>@{p.username}</td>
                    <td className="pw-cell">
                      {shown[p.id] ? <>
                        <code className="pw">{shown[p.id] || '••••••••••••'}</code>
                        <button className="icon-btn small" onClick={() => show(p)} title={shown[p.id] ? 'Hide' : 'Show'} aria-label="Show password"><Eye size={14} /></button>
                        <button className="icon-btn small" onClick={() => copy(p)} title="Copy password" aria-label="Copy password">{copied === p.id ? <Check size={14} /> : <Copy size={14} />}</button>
                      </> : <span className="muted small">not stored</span>}
                      <button className="icon-btn small" onClick={() => regenerate(p)} title="Make a new password" aria-label="New password"><RefreshCw size={14} /></button>
                    </td>
                    <td>
                      <select value={p.role} onChange={(e) => patch(p, { role: e.target.value })}>
                        {roles.roles.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                      </select>
                      {Object.keys(p.overrides).length > 0 && <span className="tag" title="Has their own permission changes">+{Object.keys(p.overrides).length}</span>}
                    </td>
                    <td className="small">{ownAgents(p).join(', ') || <span className="muted">—</span>}</td>
                    <td>{p.disabled ? 'Disabled' : 'Active'}</td>
                    <td className="right nowrap">
                      <button className="link-btn" onClick={() => setEditing(p)}><ShieldCheck size={14} /> Permissions</button>
                      <button className="link-btn" onClick={() => patch(p, { team: p.team === 'Management' ? 'Staff' : 'Management' })}>
                        Move to {p.team === 'Management' ? 'Staff' : 'Management'}</button>
                      <button className="link-btn" onClick={() => patch(p, { disabled: !p.disabled })}>{p.disabled ? 'Enable' : 'Disable'}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )
      })}
      {!visible.length && <p className="muted center pad">Nobody matches “{query}”.</p>}
      <p className="muted small">New passwords appear only when created or reset. Existing passwords cannot be retrieved.
        Making a new password signs that person out. People can change their own password from their name at the bottom left.</p>
      {adding && <AddPerson roles={roles.roles} onClose={() => setAdding(false)} onDone={(p, pw) => { load(); reload(); setShown((x) => ({ ...x, [p]: pw })) }} />}
      {editing && <PersonPermissions person={editing} roles={roles} onClose={() => setEditing(null)} onDone={() => { load(); reload() }} />}
    </>
  )
}

function AddPerson({ roles, onClose, onDone }: { roles: Role[]; onClose: () => void; onDone: (id: number, pw: string) => void }) {
  const [f, setF] = useState({ username: '', name: '', role: 'staff', team: 'Staff' })
  const [error, setError] = useState('')
  const [made, setMade] = useState<{ username: string; password: string } | null>(null)
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const r = await api.post<Person & { password: string }>('/api/admin/users', f)
      setMade({ username: r.username, password: r.password }); onDone(r.id, r.password)
    } catch (err) { setError((err as Error).message) }
  }
  if (made) return (
    <Modal title="Person added" onClose={onClose}>
      <p>Username <b>{made.username}</b>, password <code className="pw">{made.password}</code></p>
      <div className="form-actions"><button className="btn" onClick={() => copyText(made.password)}><Copy size={15} /> Copy password</button>
        <button className="btn primary" onClick={onClose}>Done</button></div>
    </Modal>
  )
  return (
    <Modal title="Add person" onClose={onClose}>
      <form className="form" onSubmit={save}>
        <label>Full name<input autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label>Username<input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.toLowerCase() })} placeholder="e.g. worker" /></label>
        <label>Group<select value={f.team} onChange={(e) => setF({ ...f, team: e.target.value })}><option value="Management">Management</option><option value="Staff">Staff</option></select></label>
        <label>Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{roles.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
        <p className="muted small">A password is made for them; copy it now; it cannot be retrieved later.</p>
        {error && <div className="error-text">{error}</div>}
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Add</button></div>
      </form>
    </Modal>
  )
}

function PermissionList({ catalog, value, base, onChange, disabled }: {
  catalog: Perm[]; value: (id: string) => boolean | 'role'; base?: (id: string) => boolean; onChange: (id: string, v: boolean | null) => void; disabled?: boolean
}) {
  const groups = [...new Set(catalog.map((p) => p.group))]
  return (
    <div className="perm-list">
      {groups.map((g) => (
        <fieldset key={g}><legend>{g}</legend>
          {catalog.filter((p) => p.group === g).map((p) => {
            const v = value(p.id)
            const on = v === 'role' ? !!base?.(p.id) : v
            return (
              <label key={p.id} className={'perm' + (v !== 'role' && base ? ' changed' : '')}>
                <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => onChange(p.id, e.target.checked)} />
                <span><b>{p.label}</b><small className="muted">{p.description}</small></span>
                {base && v !== 'role' && <button type="button" className="link-btn small" onClick={() => onChange(p.id, null)} title="Use the role's setting">reset</button>}
              </label>
            )
          })}
        </fieldset>
      ))}
    </div>
  )
}

function PersonPermissions({ person, roles, onClose, onDone }: { person: Person; roles: Roles; onClose: () => void; onDone: () => void }) {
  const [role, setRole] = useState(person.role)
  const [over, setOver] = useState<Record<string, boolean>>(person.overrides)
  const [error, setError] = useState('')
  const r = roles.roles.find((x) => x.id === role)
  const admin = role === 'admin'
  const save = async () => {
    try { await api.patch(`/api/admin/users/${person.id}`, { role, overrides: admin ? {} : over }); onDone(); onClose() } catch (e) { setError((e as Error).message) }
  }
  return (
    <Modal title={`${person.name}: permissions`} onClose={onClose} wide>
      <label className="inline-label">Role <select value={role} onChange={(e) => setRole(e.target.value)}>{roles.roles.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}</select>
        <span className="muted small">{r?.description}</span></label>
      {admin ? <p className="muted">Admins have every permission and the Admin page.</p> : <>
        <p className="muted small">Ticks come from the role. Change one to make an exception just for {person.name}; “reset” goes back to the role.</p>
        <PermissionList catalog={roles.catalog} base={(id) => !!r?.permissions.includes(id)}
          value={(id) => (id in over ? over[id] : 'role')}
          onChange={(id, v) => { const n = { ...over }; if (v === null || v === r?.permissions.includes(id)) delete n[id]; else n[id] = v; setOver(n) }} />
      </>}
      {error && <div className="error-text">{error}</div>}
      <div className="form-actions"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></div>
    </Modal>
  )
}

export function RolesTab({ query }: { query: string }) {
  const [data, setData] = useState<Roles>({ roles: [], catalog: [] })
  const [people, setPeople] = useState<Person[]>([])
  const [edit, setEdit] = useState<Role | 'new' | null>(null)
  const load = () => Promise.all([api.get<Roles>('/api/admin/roles').then(setData), api.get<Person[]>('/api/admin/users').then(setPeople)])
  useEffect(() => { load() }, [])
  const q = query.trim().toLowerCase()
  const list = useMemo(() => data.roles.filter((r) => !q || (r.label + ' ' + r.description).toLowerCase().includes(q)), [data, q])
  const remove = async (r: Role) => {
    if (!confirm(`Delete the role ${r.label}?`)) return
    try { await api.del(`/api/admin/roles/${r.id}`); load() } catch (e) { alert((e as Error).message) }
  }
  return (
    <>
      <div className="toolbar"><button className="btn primary" onClick={() => setEdit('new')}><Plus size={16} /> Add role</button></div>
      <table className="table">
        <thead><tr><th>Role</th><th>What it can do</th><th>People</th><th /></tr></thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.id}>
              <td><b>{r.label}</b><div className="muted small">{r.description}</div></td>
              <td className="small">{r.locked ? 'Everything' : r.permissions.map((p) => data.catalog.find((c) => c.id === p)?.label).filter(Boolean).join(', ') || <span className="muted">Nothing</span>}</td>
              <td className="small">{people.filter((p) => p.role === r.id).map((p) => p.name).join(', ') || <span className="muted">—</span>}</td>
              <td className="right nowrap">
                {!r.locked && <button className="link-btn" onClick={() => setEdit(r)}>Edit</button>}
                {!r.builtin && <button className="link-btn" onClick={() => remove(r)}><Trash2 size={14} /></button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit && <EditRole role={edit === 'new' ? null : edit} catalog={data.catalog} onClose={() => setEdit(null)} onDone={load} />}
    </>
  )
}

function EditRole({ role, catalog, onClose, onDone }: { role: Role | null; catalog: Perm[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ id: role?.id || '', label: role?.label || '', description: role?.description || '', permissions: role?.permissions || [] })
  const [error, setError] = useState('')
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const id = role ? role.id : f.id || f.label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
    try { await api.put(`/api/admin/roles/${id}`, f); onDone(); onClose() } catch (err) { setError((err as Error).message) }
  }
  return (
    <Modal title={role ? `Role: ${role.label}` : 'New role'} onClose={onClose} wide>
      <form className="form" onSubmit={save}>
        <label>Name<input autoFocus value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></label>
        <label>Description<input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <PermissionList catalog={catalog} value={(id) => f.permissions.includes(id)}
          onChange={(id, v) => setF({ ...f, permissions: v ? [...f.permissions, id] : f.permissions.filter((x) => x !== id) })} />
        <p className="muted small">Everyone always keeps their own agents. The Admin page is only for the Admin role.</p>
        {error && <div className="error-text">{error}</div>}
        <div className="form-actions"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Save</button></div>
      </form>
    </Modal>
  )
}

export function AdminSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="admin-search"><Search size={15} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="Search people, roles, agents…" aria-label="Search the Admin page" />
    </div>
  )
}
