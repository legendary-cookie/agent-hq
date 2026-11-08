import { useEffect, useMemo, useRef, useState } from 'react';
import { useApi } from '../api/client';
import type { Fleet } from '../api/types';
import { AGENT_EVENT } from '../pages/AgentSheet';
import './search.css';

type Item = { label: string; note: string; go: () => void };
const go = (hash: string) => () => { location.hash = `/${hash}`; };
const PAGES: Item[] = [
  { label: 'Overview', note: 'Page', go: go('overview') },
  { label: 'Agents', note: 'Page', go: go('agents') },
  { label: 'Usage', note: 'Page', go: go('usage') },
  { label: 'Router', note: '9router accounts, limits and keys', go: go('router') },
  { label: 'Office', note: 'Page', go: go('office') },
  { label: 'Settings', note: 'Page', go: go('settings') },
  { label: 'Sessions', note: 'Every session', go: go('sessions') },
  { label: 'Dashboard', note: 'Detailed charts', go: go('dashboard') },
  { label: 'Limits', note: 'Plan limits', go: go('limits') },
];

export function Search({ onClose }: { onClose: () => void }) {
  const { data } = useApi<Fleet>('/api/fleet?range=week', { live: false });
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const results = useMemo(() => {
    const agents: Item[] = (data?.agents ?? []).map((agent) => ({
      label: agent.label,
      note: agent.owner ?? agent.host,
      go: () => {
        if (!location.hash.startsWith('#/agents') && !location.hash.startsWith('#/overview') && location.hash) location.hash = '/agents';
        // A short wait, so the page has appeared if this search changed the address.
        setTimeout(() => window.dispatchEvent(new CustomEvent(AGENT_EVENT, { detail: agent.host })), 80);
      },
    }));
    const text = query.trim().toLowerCase();
    return [...agents, ...PAGES].filter((item) => !text || item.label.toLowerCase().includes(text) || item.note.toLowerCase().includes(text)).slice(0, 8);
  }, [data, query]);
  const choose = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    item.go();
  };
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setCursor((cursor + 1) % Math.max(results.length, 1)); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setCursor((cursor - 1 + results.length) % Math.max(results.length, 1)); }
    if (event.key === 'Enter') { event.preventDefault(); choose(results[cursor]); }
    if (event.key === 'Escape') onClose();
  };
  return (
    <div className="search-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="search-box" role="dialog" aria-label="Search">
        <input ref={input} aria-label="Search agents, people or pages" placeholder="Search agents, people or pages" autoComplete="off" value={query} onChange={(event) => { setQuery(event.target.value); setCursor(0); }} onKeyDown={onKey} />
        {results.length ? (
          <ul>
            {results.map((item, index) => (
              <li key={`${item.label}-${item.note}`}>
                <button type="button" className={index === cursor ? 'current' : ''} onMouseEnter={() => setCursor(index)} onClick={() => choose(item)}>
                  <span>{item.label}</span>
                  <em>{item.note}</em>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>No results. Try a different name.</p>
        )}
      </div>
    </div>
  );
}
