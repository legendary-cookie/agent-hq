/** Browse and edit an agent's memory files and skills, with each file's history. */
import { useEffect, useMemo, useState } from 'react';
import { api, useApi } from '../api/client';
import { useMe } from '../api/me';
import { fmtRelative } from '../lib/format';
import { usePasswordCheck } from './AgentControl';
import { Segmented } from '../components/Segmented';
import './control.css';
import './memory.css';

type Root = 'memory' | 'skills';
type FileRow = { path: string; size: number; modified: number };
type Listing = { root: string; git: boolean; files: FileRow[]; truncated: boolean };
type Version = { id: string; at: number; label: string };
type Opened = { path: string; text: string; saved: string; modified: number | null; history: Version[]; viewing: (Version & { text: string }) | null };

const q = (value: string) => encodeURIComponent(value);
const SKILL_TEMPLATE = (name: string) => `---\nname: ${name}\ndescription: What this skill does and when to use it.\n---\n\n# ${name}\n\nSteps the agent should follow.\n`;

export function hostFromHash(hash: string) {
  return new URLSearchParams(hash.split('?')[1] ?? '').get('host');
}

/** Files grouped by their folder, with skills shown by skill name. */
export function groupFiles(files: FileRow[], filter: string) {
  const text = filter.trim().toLowerCase();
  const groups = new Map<string, FileRow[]>();
  for (const file of files) {
    if (text && !file.path.toLowerCase().includes(text)) continue;
    const folder = file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/')) : '';
    groups.set(folder, [...(groups.get(folder) ?? []), file]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export function Memory() {
  const { data: fleet } = useApi<{ agents: { host: string; label: string }[] }>('/api/fleet?range=today', { live: false });
  const { data: control } = useApi<{ elevated_until?: number | null; managed?: string[] }>('/api/control', { live: false });
  const { ensure, dialog } = usePasswordCheck(control?.elevated_until);
  const mayEdit = useMe().can('memory.edit');
  const [host, setHost] = useState<string | null>(() => hostFromHash(location.hash));
  const [root, setRoot] = useState<Root>('memory');
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [opened, setOpened] = useState<Opened | null>(null);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const managed = new Set(control?.managed ?? []);
  const agents = (fleet?.agents ?? []).filter((agent) => !control?.managed || managed.has(agent.host)).sort((a, b) => a.label.localeCompare(b.label));
  const label = agents.find((agent) => agent.host === host)?.label ?? host;
  const dirty = !!opened && !opened.viewing && opened.text !== opened.saved;

  const load = async () => {
    if (!host) return;
    setListing(null); setError(null); setOpened(null);
    try { setListing(await api<Listing>(`/api/memory?host=${q(host)}&root=${root}`)); }
    catch (problem) { setError((problem as Error).message); }
  };
  useEffect(() => { void load(); }, [host, root]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const leave = () => !dirty || window.confirm('You have unsaved changes. Discard them?');
  const open = async (path: string) => {
    if (!host || !leave()) return;
    setNote(null);
    try {
      const file = await api<{ text: string; history: Version[]; modified: number }>(`/api/memory/file?host=${q(host)}&root=${root}&path=${q(path)}`);
      setOpened({ path, text: file.text, saved: file.text, modified: file.modified, history: file.history, viewing: null });
    } catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
  };
  const view = async (version: Version) => {
    if (!host || !opened) return;
    try {
      const old = await api<{ text: string }>(`/api/memory/version?host=${q(host)}&root=${root}&path=${q(opened.path)}&id=${q(version.id)}`);
      setOpened({ ...opened, viewing: { ...version, text: old.text } });
    } catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
  };
  const save = async (text: string, path = opened?.path, expect = opened?.modified ?? null, create = false) => {
    if (!host || !path) return;
    if (!(await ensure())) return;
    setBusy(true); setNote(null);
    try {
      const answer = await api<{ committed: boolean; created: boolean }>('/api/memory/file', { method: 'POST', body: JSON.stringify({ host, root, path, text, expect, create }) });
      setNote({ text: `${answer.created ? 'Created' : 'Saved'} ${path}${answer.committed ? ', recorded in the memory’s history' : root === 'skills' && !answer.created ? ', old text kept in history' : ''}. The agent reads it from its next session.` });
      const listed = await api<Listing>(`/api/memory?host=${q(host)}&root=${root}`).catch(() => null);
      if (listed) setListing(listed);
      await openAfterSave(path);
    } catch (problem) { setNote({ text: (problem as Error).message, bad: true }); }
    finally { setBusy(false); }
  };
  const openAfterSave = async (path: string) => {
    const file = await api<{ text: string; history: Version[]; modified: number }>(`/api/memory/file?host=${q(host!)}&root=${root}&path=${q(path)}`);
    setOpened({ path, text: file.text, saved: file.text, modified: file.modified, history: file.history, viewing: null });
  };
  const create = async () => {
    const asked = window.prompt(root === 'skills' ? 'Name of the new skill (letters, numbers and dashes):' : 'Path of the new memory file, such as reference/clients.md:');
    if (!asked) return;
    const name = asked.trim();
    if (root === 'skills') {
      if (!/^[a-z0-9][a-z0-9-]{1,40}$/.test(name)) return setNote({ text: 'Skill names are 2 to 41 lowercase letters, numbers or dashes.', bad: true });
      if (listing?.files.some((file) => file.path.startsWith(`${name}/`))) return setNote({ text: 'That skill already exists.', bad: true });
      await save(SKILL_TEMPLATE(name), `${name}/SKILL.md`, null, true);
    } else {
      if (!/^[A-Za-z0-9_\-/ .]{1,120}\.md$/.test(name) || name.split('/').includes('..') || name.startsWith('/')) return setNote({ text: 'Use a path ending in .md, inside the memory folder.', bad: true });
      if (listing?.files.some((file) => file.path === name)) return setNote({ text: 'That file already exists.', bad: true });
      await save(`# ${name.split('/').pop()!.replace(/\.md$/, '')}\n\n`, name, null, true);
    }
  };
  const groups = useMemo(() => groupFiles(listing?.files ?? [], filter), [listing, filter]);
  const now = Date.now() / 1000;

  return (
    <div className="hq-page memory-page">
      {dialog}
      <header className="page-head">
        <div><h1>Memory and skills</h1><p className="lede">Read and edit what an agent remembers and the skills it can use. Every save keeps the old text.</p></div>
      </header>
      <div className="tools">
        <select aria-label="Agent" value={host ?? ''} onChange={(event) => { if (leave()) { setHost(event.target.value || null); history.replaceState(null, '', `#/memory?host=${q(event.target.value)}`); } }}>
          <option value="">Choose an agent…</option>
          {agents.map((agent) => <option key={agent.host} value={agent.host}>{agent.label}</option>)}
        </select>
        <Segmented value={root} onChange={(value) => { if (leave()) setRoot(value); }} options={[{ value: 'memory', label: 'Memory' }, { value: 'skills', label: 'Skills' }]} />
        {listing && <input type="search" aria-label="Filter files" placeholder="Filter files" value={filter} onChange={(event) => setFilter(event.target.value)} />}
        {listing && mayEdit && <button type="button" className="memory-new" onClick={() => void create()}>{root === 'skills' ? 'New skill' : 'New file'}</button>}
      </div>
      {!host && <p className="muted">Choose an agent to see its {root}.</p>}
      {error && <p className="control-error">{error}</p>}
      {note && <p className={note.bad ? 'control-error' : 'memory-ok'} role="status">{note.text}</p>}
      {host && !listing && !error && <p className="muted">Loading {label}’s {root}…</p>}
      {listing && (
        <div className="memory-grid">
          <nav className="memory-files" aria-label="Files">
            {groups.length ? groups.map(([folder, files]) => (
              <div key={folder || '.'}>
                {folder && <div className="memory-folder">{folder}</div>}
                {files.map((file) => (
                  <button type="button" key={file.path} className="memory-file" aria-current={opened?.path === file.path ? 'true' : undefined} onClick={() => void open(file.path)}>
                    <span>{file.path.split('/').pop()}</span>
                    <small className="muted">{fmtRelative(file.modified, now)}</small>
                  </button>
                ))}
              </div>
            )) : <p className="muted">No files{filter ? ' match' : ''}.</p>}
            {listing.truncated && <p className="note">Only the first 3,000 files are listed.</p>}
          </nav>
          <section className="memory-editor" aria-label="Editor">
            {opened ? (
              <>
                <div className="memory-editor-head">
                  <strong>{opened.path}</strong>
                  {opened.viewing ? (
                    <span className="memory-buttons">
                      <span className="muted">Old version from {new Date(opened.viewing.at * 1000).toLocaleString()}</span>
                      {mayEdit && <button type="button" onClick={() => setOpened({ ...opened, text: opened.viewing!.text, viewing: null })}>Put this text back</button>}
                      <button type="button" onClick={() => setOpened({ ...opened, viewing: null })}>Back to current</button>
                    </span>
                  ) : !mayEdit ? (
                    <span className="memory-buttons"><span className="muted">Read only: your role can’t edit memory</span></span>
                  ) : (
                    <span className="memory-buttons">
                      {dirty && <span className="muted">Unsaved changes</span>}
                      <button type="button" disabled={!dirty || busy} onClick={() => setOpened({ ...opened, text: opened.saved })}>Undo changes</button>
                      <button type="button" className="primary" disabled={!dirty || busy} onClick={() => void save(opened.text)}>{busy ? 'Saving…' : 'Save'}</button>
                    </span>
                  )}
                </div>
                <textarea aria-label={`Text of ${opened.path}`} spellCheck={false} readOnly={!!opened.viewing || !mayEdit}
                  value={opened.viewing ? opened.viewing.text : opened.text} onChange={(event) => setOpened({ ...opened, text: event.target.value })} />
                <div className="memory-history">
                  <h2>History</h2>
                  {opened.history.length ? opened.history.map((version, index) => (
                    <button type="button" key={version.id} onClick={() => void view(version)}>
                      <span>{index === 0 && root === 'memory' ? 'Current: ' : ''}{version.label}</span>
                      <small className="muted">{fmtRelative(version.at, now)}</small>
                    </button>
                  )) : <p className="muted">{root === 'memory' && !listing.git ? 'This memory folder has no history, so old versions aren’t kept.' : 'No earlier versions yet.'}</p>}
                </div>
              </>
            ) : <p className="muted">Choose a file to read or edit it.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
