import { describe, expect, it } from 'vitest';

import { createSafeStorage, createSearchHistory } from './safeStorage';

function throwingStorage(overrides: Partial<Storage> = {}): Storage {
  return new class implements Storage {
    get length() {
      if (overrides.length !== undefined) return overrides.length;
      throw new DOMException('denied', 'SecurityError');
    }
    clear() {
      if (overrides.clear) return overrides.clear();
      throw new DOMException('denied', 'SecurityError');
    }
    getItem(key: string) {
      if (overrides.getItem) return overrides.getItem(key);
      throw new DOMException('denied', 'SecurityError');
    }
    key(index: number) {
      if (overrides.key) return overrides.key(index);
      throw new DOMException('denied', 'SecurityError');
    }
    removeItem(key: string) {
      if (overrides.removeItem) return overrides.removeItem(key);
      throw new DOMException('denied', 'SecurityError');
    }
    setItem(key: string, value: string) {
      if (overrides.setItem) return overrides.setItem(key, value);
      throw new DOMException('full', 'QuotaExceededError');
    }
  }();
}

describe('SafeStorage', () => {
  it('falls back to memory when acquiring browser storage throws', () => {
    const storage = createSafeStorage(() => { throw new DOMException('denied', 'SecurityError'); });

    expect(storage.set('density', 'compact')).toBe(false);
    expect(storage.get('density')).toBe('compact');
  });

  it('falls back to memory when reads or writes throw', () => {
    const storage = createSafeStorage(() => throwingStorage());

    expect(storage.set('theme', 'dark')).toBe(false);
    expect(storage.get('theme')).toBe('dark');
    expect(storage.remove('theme')).toBe(false);
    expect(storage.get('theme')).toBeNull();
  });

  it('keeps at most twelve unique recent searches in newest-first order', () => {
    const storage = createSafeStorage(() => throwingStorage());
    const history = createSearchHistory(storage, 'searches');
    for (let index = 1; index <= 14; index += 1) history.add(`query ${index}`);
    history.add('query 9');

    expect(history.list()).toHaveLength(12);
    expect(history.list()[0]).toBe('query 9');
    expect(history.list()).not.toContain('query 1');
    expect(history.list()).not.toContain('query 2');
  });

  it('recovers from corrupt persisted history', () => {
    const storage = createSafeStorage(() => throwingStorage({ getItem: () => '{bad json' }));

    expect(createSearchHistory(storage, 'searches').list()).toEqual([]);
  });

  it('contains JSON serialization failures without corrupting the prior value', () => {
    const storage = createSafeStorage(() => throwingStorage());
    storage.setJson('settings', { density: 'compact' });
    const circular: { self?: unknown } = {};
    circular.self = circular;

    expect(storage.setJson('settings', circular)).toBe(false);
    expect(storage.getJson('settings', (value) => value)).toEqual({ density: 'compact' });

    expect(storage.setJson('settings', undefined)).toBe(false);
    expect(storage.getJson('settings', (value) => value)).toEqual({ density: 'compact' });
  });
});
