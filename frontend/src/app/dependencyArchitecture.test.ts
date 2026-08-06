import { describe, expect, it } from 'vitest';

const sourceModules = import.meta.glob('../**/*.{ts,tsx}', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>;

function imports(source: string): string[] {
  return Array.from(source.matchAll(/(?:from\s+|import\s*\()(['"])([^'"]+)\1/g), (match) => match[2]);
}

function sourceName(path: string): string {
  if (path.startsWith('./')) return `app/${path.slice(2)}`;
  return path.replace(/^\.\.\//, '');
}

describe('workspace dependency architecture', () => {
  it('keeps features and shared components independent of app internals', () => {
    const violations = Object.entries(sourceModules)
      .filter(([path]) => !path.includes('.test.') && (
        sourceName(path).startsWith('features/')
        || sourceName(path).startsWith('components/')
      ))
      .flatMap(([path, source]) => imports(source)
        .filter((specifier) => specifier.includes('/app/') || specifier.startsWith('../../app'))
        .map((specifier) => `${sourceName(path)} -> ${specifier}`));

    expect(violations).toEqual([]);
  });

  it('loads every feature route through its public index interface', () => {
    const routerSource = Object.entries(sourceModules)
      .find(([path]) => sourceName(path) === 'app/router.tsx')?.[1];
    if (!routerSource) throw new Error('app/router.tsx is missing from the source graph');
    const routeImports = imports(routerSource).filter((specifier) => specifier.startsWith('../features/'));

    expect(routeImports).toEqual([
      '../features/dashboard',
      '../features/library',
      '../features/reader',
      '../features/reviews',
      '../features/acquire',
      '../features/jobs',
      '../features/insights',
      '../features/settings',
    ]);
  });

  it('keeps feature implementations from deep-importing sibling features', () => {
    const violations = Object.entries(sourceModules)
      .filter(([path]) => !path.includes('.test.') && sourceName(path).startsWith('features/'))
      .flatMap(([path, source]) => {
        const feature = sourceName(path).split('/')[1];
        return imports(source)
          .map((specifier) => ({
            specifier,
            sibling: /^\.\.\/(?!\.\.\/)([^/]+)/.exec(specifier)?.[1],
          }))
          .filter(({ sibling }) => sibling && sibling !== feature)
          .map(({ specifier }) => `${sourceName(path)} -> ${specifier}`);
      });

    expect(violations).toEqual([]);
  });
});
