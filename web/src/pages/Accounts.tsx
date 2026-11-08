/** Who can sign in to HQ, and what each role can see and do. Every change asks for the password check first
 *  and the server logs it. */
import { useState } from 'react';
import type { FormEvent } from 'react';
import { api, useApi } from '../api/client';
import { resetMe } from '../api/me';
import { fmtRelative } from '../lib/format';
import { usePasswordCheck } from './AgentControl';
import './fleet.css';
import './control.css';
import './insights.css';
import './router.css';
import './accounts.css';

export type AccountUser = {
  username: string; name: string; role: string; disabled: boolean; created: number | string | null;
  last_sign_in: number | null; sessions: number; you: boolean;
};
export type AccountRole = { id: string; label: string; description: string; permissions: string[]; builtin: boolean; locked: boolean; users: number };
export type CatalogItem = { id: string; label: string; group: string; description: string };
export type AccountsData = { users: AccountUser[]; roles: AccountRole[]; catalog: CatalogItem[] };

export const MIN_PASSWORD = 12;
const USERNAME = /^[a-z][a-z0-9._-]{1,31}$/;

export function usernameProblem(name: string) {
  return USERNAME.test(name) ? null : 'Usernames are 2 to 32 characters: lowercase letters, digits, dots, dashes or underscores, starting with a letter.';
}
export function passwordProblem(password: string, repeat: string) {
  if (password.length < MIN_PASSWORD) return `Passwords need at least ${MIN_PASSWORD} characters.`;
  if (password !== repeat) return 'The two passwords don’t match.';
  return null;
}
/** Permission ids in the catalog's order, then any the catalog doesn't know (kept, not dropped). */
export function ordered(picked: Set<string>, catalog: CatalogItem[]) {
  const known = catalog.map((item) => item.id);
  return [...known.filter((id) => picked.has(id)), ...[...picked].filter((id) => !known.includes(id))];
}
const sameSet = (a: Set<string>, b: string[]) => a.size === new Set(b).size && b.every((id) => a.has(id));

type Write = (body: Record<string, unknown>, done: string) => Promise<boolean>;

function PermissionPicker({ catalog, value, onChange, locked, name }: { catalog: CatalogItem[]; value: Set<string>; onChange?: (next: Set<string>) => void; locked?: boolean; name: string }) {
  const groups = [...new Set(['Pages', 'Actions', ...catalog.map((item) => item.group)])].filter((group) => catalog.some((item) => item.group === group));
  const readOnly = locked || !onChange;
  const set = (ids: string[], on: boolean) => {
    const next = new Set(value);
    for (const id of ids) if (on) next.add(id); else next.delete(id);
    onChange?.(next);
  };
  return (
    <div className="acct-perms">
      {groups.map((group) => {
        const items = catalog.filter((item) => item.group === group);
        const all = items.every((item) => value.has(item.id));
        return (
          <fieldset key={group} className="acct-perm-group" aria-label={`${name}: ${group}`}>
            <legend>
              {group}
              {!readOnly && <button type="button" className="acct-link" onClick={() => set(items.map((item) => item.id), !all)}>{all ? 'None' : 'All'}</button>}
            </legend>
            {items.map((item) => (
              <label key={item.id} className="acct-perm" title={item.id}>
                <input type="checkbox" aria-label={item.label} checked={!!locked || value.has(item.id)} disabled={readOnly} onChange={(event) => set([item.id], event.target.checked)} />
                <span><span className="acct-perm-label">{item.label}</span>{item.description && <small className="muted">{item.description}</small>}</span>
              </label>
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}

function EditUser({ user, roles, write, onClose }: { user: AccountUser; roles: AccountRole[]; write: Write; onClose: () => void }) {
  const [name, setName] = useState(user.name);
  const [role, setRole] = useState(user.role);
  const [disabled, setDisabled] = useState(user.disabled);
  const [busy, setBusy] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const body: Record<string, unknown> = { action: 'update_user', username: user.username };
    if (name.trim() !== user.name) body.name = name.trim();
    if (role !== user.role) body.role = role;
    if (!user.you && disabled !== user.disabled) body.disabled = disabled;
    if (Object.keys(body).length === 2) return onClose();
    setBusy(true);
    const ok = await write(body, `${user.username}: saved.`);
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <form className="router-key-editor acct-editor" aria-label={`Edit ${user.username}`} onSubmit={save}>
      <div className="acct-fields">
        <label>Name <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} /></label>
        <label>Role
          <select aria-label="Role" value={role} onChange={(event) => setRole(event.target.value)}>
            {roles.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
      </div>
      {user.you
        ? <p className="muted router-small">You can’t disable your own account. Changing your own role takes effect straight away.</p>
        : <label className="watch-check router-inline"><input type="checkbox" checked={disabled} onChange={(event) => setDisabled(event.target.checked)} /> Disabled: can’t sign in, and is signed out everywhere</label>}
      <div className="control-buttons">
        <button type="submit" className="plain-button" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" className="plain-button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

function ResetPassword({ user, write, onClose }: { user: AccountUser; write: Write; onClose: () => void }) {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const wrong = passwordProblem(password, repeat);
    setProblem(wrong);
    if (wrong) return;
    setBusy(true);
    const ok = await write({ action: 'set_password', username: user.username, password }, `${user.username}: password changed.`);
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <form className="router-key-editor acct-editor" aria-label={`New password for ${user.username}`} onSubmit={save}>
      <div className="acct-fields">
        <label>New password <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <label>Repeat it <input type="password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} /></label>
      </div>
      <p className="muted router-small">At least {MIN_PASSWORD} characters. Tell {user.name || user.username} the new password in person or another safe way.</p>
      {problem && <p className="control-error" role="alert">{problem}</p>}
      <div className="control-buttons">
        <button type="submit" className="plain-button" disabled={busy}>{busy ? 'Saving…' : 'Set password'}</button>
        <button type="button" className="plain-button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

function UserRow({ user, roles, write, now }: { user: AccountUser; roles: AccountRole[]; write: Write; now: number }) {
  const [panel, setPanel] = useState<'edit' | 'password' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const role = roles.find((item) => item.id === user.role);
  const toggle = (next: typeof panel) => setPanel(panel === next ? null : next);
  const remove = async () => {
    setBusy(true);
    const ok = await write({ action: 'delete_user', username: user.username }, `${user.username} deleted.`);
    setBusy(false);
    if (ok) setPanel(null);
  };
  return (
    <div>
      <div className="mini-row" role="row" data-off={user.disabled || undefined}>
        <span role="cell"><b>{user.username}</b>{user.you && <span className="acct-you">you</span>}</span>
        <span role="cell">{user.name || <span className="muted">—</span>}</span>
        <span role="cell">{role?.label ?? user.role}</span>
        <span role="cell" className={user.disabled ? 'acct-disabled' : ''}>{user.disabled ? 'disabled' : 'active'}</span>
        <span role="cell" className="muted">{user.last_sign_in ? fmtRelative(user.last_sign_in, now) : 'never'}</span>
        <span role="cell" className="num">{user.sessions}</span>
        <span role="cell" className="router-key-actions">
          <button type="button" className="plain-button" aria-label={`Edit ${user.username}`} aria-expanded={panel === 'edit'} onClick={() => toggle('edit')}>{panel === 'edit' ? 'Close' : 'Edit'}</button>
          <button type="button" className="plain-button" aria-label={`Reset password for ${user.username}`} aria-expanded={panel === 'password'} onClick={() => toggle('password')}>{panel === 'password' ? 'Close' : 'Reset password'}</button>
          {!user.you && <button type="button" className="plain-button danger" aria-label={`Delete ${user.username}`} onClick={() => toggle('delete')}>Delete</button>}
        </span>
      </div>
      {panel === 'edit' && <EditUser user={user} roles={roles} write={write} onClose={() => setPanel(null)} />}
      {panel === 'password' && <ResetPassword user={user} write={write} onClose={() => setPanel(null)} />}
      {panel === 'delete' && (
        <div className="router-confirm" role="alertdialog" aria-label={`Delete ${user.username}?`}>
          <span>Delete <b>{user.username}</b>{user.name ? ` (${user.name})` : ''}? They’re signed out at once and can’t sign in again. Disable the account instead to keep it for later.</span>
          <button type="button" className="plain-button danger" disabled={busy} onClick={remove}>{busy ? 'Deleting…' : 'Delete user'}</button>
          <button type="button" className="plain-button" onClick={() => setPanel(null)}>Cancel</button>
        </div>
      )}
    </div>
  );
}

function AddUser({ roles, write, onClose }: { roles: AccountRole[]; write: Write; onClose: () => void }) {
  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState(() => (roles.find((item) => !item.locked) ?? roles[0])?.id ?? '');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    const login = username.trim().toLowerCase();
    const wrong = usernameProblem(login) ?? (role ? null : 'Choose a role.') ?? passwordProblem(password, repeat);
    setProblem(wrong);
    if (wrong) return;
    setBusy(true);
    const ok = await write({ action: 'create_user', username: login, name: name.trim(), role, password }, `${login} added. They can sign in now.`);
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <form className="router-key-editor acct-editor" aria-label="Add user" onSubmit={create}>
      <div className="acct-fields">
        <label>Username <input value={username} maxLength={32} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="sam" onChange={(event) => setUsername(event.target.value)} /></label>
        <label>Name <input value={name} maxLength={80} placeholder="Sam Lee" onChange={(event) => setName(event.target.value)} /></label>
        <label>Role
          <select aria-label="Role" value={role} onChange={(event) => setRole(event.target.value)}>
            {roles.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <label>Password <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <label>Repeat password <input type="password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} /></label>
      </div>
      <p className="muted router-small">Usernames: lowercase letters, digits, dots, dashes or underscores, starting with a letter. Passwords: at least {MIN_PASSWORD} characters.</p>
      {problem && <p className="control-error" role="alert">{problem}</p>}
      <div className="control-buttons">
        <button type="submit" className="plain-button" disabled={busy}>{busy ? 'Adding…' : 'Add user'}</button>
        <button type="button" className="plain-button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

function RoleCard({ role, catalog, write }: { role: AccountRole; catalog: CatalogItem[]; write: Write }) {
  const [label, setLabel] = useState(role.label);
  const [description, setDescription] = useState(role.description);
  const [perms, setPerms] = useState(() => new Set(role.permissions));
  const [renaming, setRenaming] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const users = `${role.users} ${role.users === 1 ? 'user' : 'users'}`;
  if (role.locked) {
    return (
      <section className="acct-role" aria-label={`${role.label} role`}>
        <div className="acct-role-head"><h3>{role.label}</h3><span className="muted">{users}</span><span className="chip">built in</span></div>
        {role.description && <p className="muted acct-role-text">{role.description}</p>}
        <PermissionPicker catalog={catalog} value={new Set(catalog.map((item) => item.id))} locked name={role.label} />
        <p className="note">Admin can do everything and can’t be changed.</p>
      </section>
    );
  }
  const changed = label.trim() !== role.label || description.trim() !== role.description || !sameSet(perms, role.permissions);
  const save = async () => {
    const body: Record<string, unknown> = { action: 'update_role', id: role.id };
    if (label.trim() !== role.label) body.label = label.trim();
    if (description.trim() !== role.description) body.description = description.trim();
    if (!sameSet(perms, role.permissions)) body.permissions = ordered(perms, catalog);
    if (!label.trim()) return;
    setBusy(true);
    const ok = await write(body, `${label.trim()}: saved.${role.users ? ` Applies to its ${users} straight away.` : ''}`);
    setBusy(false);
    if (ok) setRenaming(false);
  };
  const remove = async () => {
    setBusy(true);
    await write({ action: 'delete_role', id: role.id }, `${role.label} role deleted.`);
    setBusy(false);
  };
  const reset = () => { setLabel(role.label); setDescription(role.description); setPerms(new Set(role.permissions)); setRenaming(false); };
  return (
    <section className="acct-role" aria-label={`${role.label} role`}>
      <div className="acct-role-head">
        <h3>{role.label}</h3>
        <span className="muted">{users}</span>
        {role.builtin && <span className="chip">built in</span>}
        <button type="button" className="acct-link" aria-expanded={renaming} onClick={() => setRenaming(!renaming)}>{renaming ? 'Hide name' : 'Rename'}</button>
      </div>
      {renaming ? (
        <div className="router-key-editor acct-editor acct-rename">
          <div className="acct-fields">
            <label>Name <input value={label} maxLength={40} onChange={(event) => setLabel(event.target.value)} /></label>
            <label>Description <input value={description} maxLength={200} onChange={(event) => setDescription(event.target.value)} /></label>
          </div>
        </div>
      ) : role.description && <p className="muted acct-role-text">{role.description}</p>}
      <PermissionPicker catalog={catalog} value={perms} onChange={setPerms} name={role.label} />
      <div className="control-buttons">
        <button type="button" className="plain-button" disabled={!changed || busy || !label.trim()} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
        {changed && <button type="button" className="plain-button" onClick={reset}>Undo changes</button>}
        <button type="button" className="plain-button danger" aria-label={`Delete ${role.label} role`} disabled={role.users > 0 || busy} onClick={() => setConfirm(!confirm)}>Delete role</button>
        {role.users > 0 && <span className="muted router-small">In use by {users}. Move them to another role to delete it.</span>}
      </div>
      {confirm && role.users === 0 && (
        <div className="router-confirm" role="alertdialog" aria-label={`Delete ${role.label} role?`}>
          <span>Delete the <b>{role.label}</b> role? Nobody has it now.</span>
          <button type="button" className="plain-button danger" disabled={busy} onClick={remove}>{busy ? 'Deleting…' : 'Delete role'}</button>
          <button type="button" className="plain-button" onClick={() => setConfirm(false)}>Cancel</button>
        </div>
      )}
    </section>
  );
}

function NewRole({ roles, catalog, write, onClose }: { roles: AccountRole[]; catalog: CatalogItem[]; write: Write; onClose: () => void }) {
  const [label, setLabel] = useState('');
  const [description, setDescription] = useState('');
  const [from, setFrom] = useState('');
  const [perms, setPerms] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const start = (id: string) => {
    setFrom(id);
    const role = roles.find((item) => item.id === id);
    setPerms(new Set(role ? (role.locked ? catalog.map((item) => item.id) : role.permissions) : []));
  };
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!label.trim()) return;
    setBusy(true);
    const ok = await write({ action: 'create_role', label: label.trim(), description: description.trim(), permissions: ordered(perms, catalog) }, `${label.trim()} role created. Give it to people from the users table.`);
    setBusy(false);
    if (ok) onClose();
  };
  return (
    <form className="router-key-editor acct-editor" aria-label="New role" onSubmit={create}>
      <div className="acct-fields">
        <label>Name <input value={label} maxLength={40} placeholder="Support, Marketing lead…" onChange={(event) => setLabel(event.target.value)} /></label>
        <label>Description <input value={description} maxLength={200} placeholder="What people with this role do" onChange={(event) => setDescription(event.target.value)} /></label>
        <label>Start from
          <select aria-label="Start from" value={from} onChange={(event) => start(event.target.value)}>
            <option value="">Nothing ticked</option>
            {roles.map((item) => <option key={item.id} value={item.id}>A copy of {item.label}</option>)}
          </select>
        </label>
      </div>
      <PermissionPicker catalog={catalog} value={perms} onChange={setPerms} name="New role" />
      <div className="control-buttons">
        <button type="submit" className="plain-button" disabled={busy || !label.trim()}>{busy ? 'Creating…' : 'Create role'}</button>
        <button type="button" className="plain-button" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

export function Accounts() {
  const { data, error, reload } = useApi<AccountsData>('/api/accounts');
  const { data: control } = useApi<{ elevated_until?: number | null }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [adding, setAdding] = useState(false);
  const [creatingRole, setCreatingRole] = useState(false);
  const write: Write = async (body, done) => {
    if (!(await ensure())) return false;
    setNote(null);
    try {
      await api('/api/accounts', { method: 'POST', body: JSON.stringify(body) });
      setNote({ text: done });
      // Your own name or role changed: read who you are again so the sidebar and pages follow.
      if (data?.users.some((user) => user.you && user.username === body.username)) resetMe();
      reload();
      return true;
    } catch (problem) {
      setNote({ text: (problem as Error).message, bad: true });
      return false;
    }
  };
  const head = (text: string) => <div className="hq-page"><h1>Accounts</h1><p className="muted">{text}</p></div>;
  if (error && !data) return head(error.status === 403 ? error.message : 'Couldn’t load the accounts. Check that HQ is running, then refresh.');
  if (!data) return head('Loading accounts…');
  const now = Date.now() / 1000;
  const active = data.users.filter((user) => !user.disabled).length;
  return (
    <div className="hq-page accounts">
      <header className="page-head">
        <div>
          <h1>Accounts</h1>
          <p className="lede">Who can sign in to HQ, and what each role can see and do. Changes ask for your password and are logged.</p>
        </div>
      </header>
      {note && <p className={note.bad ? 'control-error' : 'control-ok'} role="status">{note.text}</p>}

      <section aria-label="Users">
        <div className="section-head">
          <h2>Users</h2>
          <span className="acct-head-side">
            <span className="muted">{data.users.length} users · {active} active</span>
            <button type="button" className="plain-button router-new" aria-expanded={adding} onClick={() => setAdding(!adding)}>{adding ? 'Close' : 'Add user'}</button>
          </span>
        </div>
        {adding && <AddUser roles={data.roles} write={write} onClose={() => setAdding(false)} />}
        <div className="mini-table acct-users" role="table" aria-label="Users">
          <div className="mini-row mini-headrow" role="row">
            <span role="columnheader">Username</span><span role="columnheader">Name</span><span role="columnheader">Role</span><span role="columnheader">Status</span><span role="columnheader">Last sign-in</span><span role="columnheader">Sessions</span><span role="columnheader" />
          </div>
          {data.users.map((user) => (
            <UserRow key={`${user.username}:${user.name}:${user.role}:${user.disabled}`} user={user} roles={data.roles} write={write} now={now} />
          ))}
        </div>
        <p className="note">Sessions are the browsers signed in as that user now. Disabling or deleting someone signs them out everywhere.</p>
      </section>

      <section aria-label="Roles" className="acct-roles-section">
        <div className="section-head">
          <h2>Roles</h2>
          <button type="button" className="plain-button router-new" aria-expanded={creatingRole} onClick={() => setCreatingRole(!creatingRole)}>{creatingRole ? 'Close' : 'New role'}</button>
        </div>
        {creatingRole && <NewRole roles={data.roles} catalog={data.catalog} write={write} onClose={() => setCreatingRole(false)} />}
        <div className="acct-roles">
          {data.roles.map((role) => (
            <RoleCard key={`${role.id}:${role.label}:${role.description}:${role.permissions.join(',')}:${role.users}`} role={role} catalog={data.catalog} write={write} />
          ))}
        </div>
        <p className="note">Pages decide what someone can open; Actions decide what they can change. A page someone can’t open is hidden from their sidebar, and HQ refuses its data too.</p>
      </section>
      {dialog}
    </div>
  );
}
