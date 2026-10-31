import './components.css';

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <div>{title}</div>
      {hint && <p>{hint}</p>}
    </div>
  );
}
