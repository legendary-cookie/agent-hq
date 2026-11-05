export type Step = { id: string; title: string; prompt: string; kind: string; tool: string | null; depends_on: string[]; level?: string };
export function validatePlan(steps: Step[]): string {
  if (!steps.length) return 'Add at least one step.';
  if (steps.length > 10) return 'At most 10 steps are allowed.';
  const ids = new Set(steps.map(s => s.id));
  if (ids.size !== steps.length || steps.some(s => !/^s[0-9]{1,2}$/.test(s.id))) return 'Step IDs must be unique.';
  if (steps.some(s => !s.title.trim() || s.title.length > 120 || !s.prompt.trim() || s.prompt.length > 20000)) return 'Each step needs a title and prompt within the length limits.';
  const visited = new Set<string>(), visiting = new Set<string>();
  function visit(id: string): boolean {
    if (visiting.has(id)) return false;
    if (visited.has(id)) return true;
    const step = steps.find(s => s.id === id);
    if (!step) return false;
    visiting.add(id);
    if (!step.depends_on.every(visit)) return false;
    visiting.delete(id); visited.add(id); return true;
  }
  return steps.every(s => visit(s.id)) ? '' : 'Dependencies must exist and cannot form a cycle.';
}
export function PlanEditor({ steps, onChange, onStart, onSave }: { steps: Step[]; onChange: (steps: Step[]) => void; onStart?: () => void; onSave?: () => void }) {
  const error = validatePlan(steps);
  const update = (index: number, change: Partial<Step>) => onChange(steps.map((step, i) => i === index ? { ...step, ...change } : step));
  return <div className="plan-editor">{steps.map((step, i) => <fieldset key={step.id}><legend>{step.id}</legend>
    <label>Title <input value={step.title} onChange={e => update(i, {title:e.target.value})} /></label>
    <label>Prompt <textarea rows={5} value={step.prompt} onChange={e => update(i, {prompt:e.target.value})} /></label>
    <label>Kind <select value={step.kind} onChange={e => update(i, {kind:e.target.value})}><option value="code">Code</option><option value="review">Review</option><option value="research">Research</option></select></label>
    <label>Tool <select value={step.tool ?? ''} onChange={e => update(i, {tool:e.target.value || null})}><option value="">Auto</option>{['claude','codex','opencode','agy'].map(t => <option key={t}>{t}</option>)}</select></label>
    <label>Dependencies (comma separated) <input value={step.depends_on.join(', ')} onChange={e => update(i, {depends_on:e.target.value.split(',').map(x => x.trim()).filter(Boolean)})} /></label>
    <button onClick={() => onChange(steps.filter((_, n) => n !== i))}>Delete step</button></fieldset>)}
    {error && <p role="alert">{error}</p>}<div className="goal-actions">{onStart && <button className="run-primary" disabled={!!error} onClick={onStart}>Start plan</button>}{onSave && <button disabled={!!error} onClick={onSave}>Save plan</button>}</div></div>;
}
