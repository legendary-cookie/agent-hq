export function DiffView({ diff, truncated }: { diff: string; truncated: boolean }) {
  return <div className="diff-view"><pre>{diff.split('\n').map((line, i) => <span className={line.startsWith('+') ? 'diff-add' : line.startsWith('-') ? 'diff-remove' : ''} key={i}>{line}{'\n'}</span>)}</pre>{truncated && <p>Diff truncated</p>}</div>;
}
