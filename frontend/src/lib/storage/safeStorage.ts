export interface SafeStorage {
  get(key: string): string | null;
  set(key: string, value: string): boolean;
  remove(key: string): boolean;
  getJson<T>(key: string, decode: (value: unknown) => T): T | null;
  setJson(key: string, value: unknown): boolean;
}

export interface SearchHistory {
  list(): string[];
  add(query: string): string[];
  clear(): void;
}

export function createSafeStorage(
  access: () => Storage = () => globalThis.localStorage,
): SafeStorage {
  const memory = new Map<string, string>();

  const get = (key: string): string | null => {
    try {
      const value = access().getItem(key);
      if (value !== null) {
        memory.set(key, value);
        return value;
      }
    } catch {
      // The in-memory mirror is the authoritative fallback.
    }
    return memory.get(key) ?? null;
  };

  const set = (key: string, value: string): boolean => {
    memory.set(key, value);
    try {
      access().setItem(key, value);
      return true;
    } catch {
      return false;
    }
  };

  const remove = (key: string): boolean => {
    memory.delete(key);
    try {
      access().removeItem(key);
      return true;
    } catch {
      return false;
    }
  };

  return {
    get,
    set,
    remove,
    getJson<T>(key: string, decode: (value: unknown) => T): T | null {
      const raw = get(key);
      if (raw === null) return null;
      try {
        return decode(JSON.parse(raw));
      } catch {
        remove(key);
        return null;
      }
    },
    setJson(key, value) {
      try {
        const serialized = JSON.stringify(value);
        if (typeof serialized !== 'string') return false;
        return set(key, serialized);
      } catch {
        return false;
      }
    },
  };
}

export function createSearchHistory(
  storage: SafeStorage,
  key = 'paper-study:search-history',
  limit = 12,
): SearchHistory {
  const maximum = Math.max(1, Math.min(12, Math.trunc(limit)));

  const normalize = (value: unknown): string[] => {
    if (!Array.isArray(value)) throw new TypeError('Search history must be an array');
    const result: string[] = [];
    const seen = new Set<string>();
    for (const item of value) {
      if (typeof item !== 'string') throw new TypeError('Search history entries must be strings');
      const query = item.trim();
      const identity = query.toLocaleLowerCase();
      if (!query || seen.has(identity)) continue;
      seen.add(identity);
      result.push(query);
      if (result.length === maximum) break;
    }
    return result;
  };

  const list = (): string[] => storage.getJson(key, normalize) ?? [];

  return {
    list,
    add(query) {
      const clean = query.trim();
      if (!clean) return list();
      const identity = clean.toLocaleLowerCase();
      const next = [clean, ...list().filter((item) => item.toLocaleLowerCase() !== identity)].slice(0, maximum);
      storage.setJson(key, next);
      return next;
    },
    clear() {
      storage.remove(key);
    },
  };
}
