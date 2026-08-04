import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { assertCleanRoomModulePath, inspectCleanRoomSource } from './cleanRoomGuard';

const repositoryRoot = path.resolve('C:/paper-study');
const options = { repositoryRoot };
const source = (...segments: string[]) => path.join(repositoryRoot, 'frontend', 'src', ...segments);

describe('clean-room module boundary', () => {
  it('rejects modules resolved inside the legacy public tree', () => {
    expect(() => assertCleanRoomModulePath(path.join(repositoryRoot, 'public', 'app.js'), repositoryRoot))
      .toThrow(/cannot load the legacy public/u);
  });

  it('rejects a static relative import that reaches public', () => {
    expect(() => inspectCleanRoomSource(
      "import '../../../../public/app.js';",
      source('features', 'reader', 'Reader.tsx'),
      options,
    )).toThrow(/cannot load the legacy public/u);
  });

  it('rejects non-literal dynamic imports before Vite can leave them unresolved', () => {
    expect(() => inspectCleanRoomSource(
      'const moduleName = "app"; import(`../../public/${moduleName}.js`);',
      source('app', 'App.tsx'),
      options,
    )).toThrow(/Dynamic imports must use a string literal/u);
  });

  it('rejects CSS dependencies that reach public', () => {
    expect(() => inspectCleanRoomSource(
      '@import "../../../public/style.css";',
      source('styles', 'workspace.css'),
      options,
    )).toThrow(/cannot load the legacy public/u);
  });
});

describe('trusted HTML boundary', () => {
  it('rejects direct and spread-based sinks outside TrustedMathHtml', () => {
    expect(() => inspectCleanRoomSource(
      'export const Unsafe = () => <div dangerouslySetInnerHTML={{ __html: "x" }} />;',
      source('components', 'Unsafe.tsx'),
      options,
    )).toThrow(/restricted to TrustedMathHtml/u);

    expect(() => inspectCleanRoomSource(
      'const props = { dangerouslySetInnerHTML: { __html: "x" } }; export const Unsafe = () => <div {...props} />;',
      source('components', 'SpreadUnsafe.tsx'),
      options,
    )).toThrow(/restricted to TrustedMathHtml/u);
  });

  it('allows the single TrustedMathHtml adapter path', () => {
    expect(() => inspectCleanRoomSource(
      'export const TrustedMathHtml = ({ html }) => <span dangerouslySetInnerHTML={{ __html: html }} />;',
      source('lib', 'markdown', 'TrustedMathHtml.tsx'),
      options,
    )).not.toThrow();
  });
});
