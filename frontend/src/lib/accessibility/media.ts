import { useCallback, useSyncExternalStore } from 'react';

function fallbackQueryMatch(query: string): boolean {
  const maxWidth = query.match(/max-width:\s*(\d+)px/);
  if (maxWidth) {
    return window.innerWidth <= Number(maxWidth[1]);
  }
  return false;
}

function mediaQueryMatches(query: string): boolean {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia(query).matches
    : fallbackQueryMatch(query);
}

export function useWorkspaceMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      if (typeof window.matchMedia !== 'function') {
        window.addEventListener('resize', notify);
        return () => window.removeEventListener('resize', notify);
      }

      const mediaQuery = window.matchMedia(query);
      mediaQuery.addEventListener('change', notify);
      return () => mediaQuery.removeEventListener('change', notify);
    },
    [query],
  );
  const getSnapshot = useCallback(() => mediaQueryMatches(query), [query]);
  const getServerSnapshot = useCallback(() => false, []);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
