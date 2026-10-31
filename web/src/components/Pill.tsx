import type { ReactNode } from 'react';
import './components.css';

export function Pill({ color, children }: { color?: string; children: ReactNode }) {
  return (
    <span className="pill">
      {color && <span className="status-dot" style={{ backgroundColor: color }} />}
      {children}
    </span>
  );
}
