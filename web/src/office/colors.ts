export const HARNESS_COLORS: Record<string, string> = {
  claude_code: '#c96442',
  codex: '#3a7bd5',
  antigravity: '#7d5ce6',
  gemini_cli: '#4fa8d8',
  opencode: '#2bb3a3',
  hermes: '#b08968',
  unknown: '#8e8e93',
};
export const STATUS_COLORS = {
  working: '#34c759',
  waiting: '#ff9f0a',
  error: '#ff3b30',
  idle: '#aeaeb2',
};

const LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  codex: 'Codex',
  antigravity: 'Antigravity',
  gemini_cli: 'Gemini CLI',
  opencode: 'opencode',
  hermes: 'Hermes',
  break: 'Break room',
};

export function harnessLabel(id: string): string {
  return LABELS[id] ?? id;
}
