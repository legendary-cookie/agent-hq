import '@testing-library/jest-dom/vitest';

// Node 25+ defines a global `localStorage` that is unusable without
// --localstorage-file, and it shadows jsdom's. Browsers are unaffected; give
// tests a working in-memory Storage when the global one is broken.
class MemoryStorage implements Storage {
  private items = new Map<string, string>();

  get length(): number {
    return this.items.size;
  }

  clear(): void {
    this.items.clear();
  }

  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }

  setItem(key: string, value: string): void {
    this.items.set(key, String(value));
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  let usable = false;
  try {
    usable = typeof globalThis[name]?.clear === 'function';
  } catch {
    usable = false;
  }
  if (!usable) {
    Object.defineProperty(globalThis, name, {
      value: new MemoryStorage(),
      configurable: true,
      writable: true,
    });
  }
}
