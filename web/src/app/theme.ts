const KEY = 'hq-theme';

export function applySavedTheme() {
  const saved = localStorage.getItem(KEY);
  if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
}
export function isDark() {
  const set = document.documentElement.dataset.theme;
  return set ? set === 'dark' : matchMedia?.('(prefers-color-scheme: dark)').matches;
}
export function toggleTheme() {
  const next = isDark() ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem(KEY, next);
}
