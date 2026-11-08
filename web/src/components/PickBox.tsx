/** A checkbox for a set of items: ticked when all are picked, half-ticked when some are. */
import { useEffect, useRef } from 'react';

export function pickState(hosts: string[], picked: Set<string>) {
  const count = hosts.filter((host) => picked.has(host)).length;
  return { all: hosts.length > 0 && count === hosts.length, some: count > 0 && count < hosts.length };
}

/** Adds or removes the whole set: removes it if every one is already picked, otherwise adds the rest. */
export function togglePicks(hosts: string[], picked: Set<string>) {
  const next = new Set(picked);
  if (pickState(hosts, picked).all) hosts.forEach((host) => next.delete(host));
  else hosts.forEach((host) => next.add(host));
  return next;
}

export function PickBox({ hosts, picked, onChange, label, disabled }: { hosts: string[]; picked: Set<string>; onChange: (next: Set<string>) => void; label: string; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const { all, some } = pickState(hosts, picked);
  useEffect(() => { if (ref.current) ref.current.indeterminate = some; }, [some]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={all} disabled={disabled || !hosts.length} onChange={() => onChange(togglePicks(hosts, picked))} />;
}
