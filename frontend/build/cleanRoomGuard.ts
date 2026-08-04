import path from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import type { Plugin } from 'vite';

const HTML_SINK = 'dangerouslySetInnerHTML';
const SOURCE_EXTENSION = /\.[cm]?[jt]sx?$/i;
const CSS_EXTENSION = /\.css$/i;
const CSS_DEPENDENCY = /(?:@import\s+(?:url\(\s*)?|url\(\s*)(?:["']([^"']+)["']|([^'"\s);]+))/giu;

export interface CleanRoomGuardOptions {
  repositoryRoot: string;
  trustedMathHtmlPath?: string;
}

function withoutQuery(id: string) {
  return id.replace(/[?#].*$/u, '');
}

function localPath(id: string) {
  const clean = withoutQuery(id);
  if (!clean || clean.startsWith('\0')) return null;
  if (clean.startsWith('file://')) return fileURLToPath(clean);
  if (clean.startsWith('/@fs/')) return clean.slice('/@fs/'.length);
  return path.resolve(clean);
}

function isInside(root: string, target: string) {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function cleanRoomError(message: string, id: string): never {
  throw new Error(`[clean-room] ${message}: ${withoutQuery(id)}`);
}

export function assertCleanRoomModulePath(id: string, repositoryRoot: string) {
  const target = localPath(id);
  if (!target) return;

  for (const directory of ['public', 'legacy']) {
    const forbiddenRoot = path.join(repositoryRoot, directory);
    if (isInside(forbiddenRoot, target)) {
      cleanRoomError(`React cannot load the legacy ${directory}/ tree`, id);
    }
  }
}

function candidateForSpecifier(specifier: string, importer: string, repositoryRoot: string) {
  const cleanSpecifier = withoutQuery(specifier).replaceAll('\\', '/');
  if (/^(?:data:|https?:|node:)/iu.test(cleanSpecifier)) return null;
  if (cleanSpecifier === '/public' || cleanSpecifier.startsWith('/public/')) {
    return path.join(repositoryRoot, cleanSpecifier.slice(1));
  }
  if (cleanSpecifier === '/legacy' || cleanSpecifier.startsWith('/legacy/')) {
    return path.join(repositoryRoot, cleanSpecifier.slice(1));
  }
  if (path.isAbsolute(cleanSpecifier)) return cleanSpecifier;
  if (!cleanSpecifier.startsWith('.')) return null;

  const importerPath = localPath(importer);
  return importerPath ? path.resolve(path.dirname(importerPath), cleanSpecifier) : null;
}

function assertCleanRoomSpecifier(specifier: string, importer: string, repositoryRoot: string) {
  const candidate = candidateForSpecifier(specifier, importer, repositoryRoot);
  if (candidate) assertCleanRoomModulePath(candidate, repositoryRoot);
}

function scriptKind(id: string) {
  if (/\.tsx$/iu.test(id)) return ts.ScriptKind.TSX;
  if (/\.jsx$/iu.test(id)) return ts.ScriptKind.JSX;
  if (/\.[cm]?ts$/iu.test(id)) return ts.ScriptKind.TS;
  return ts.ScriptKind.JS;
}

function inspectScript(code: string, id: string, repositoryRoot: string, trustedMathHtmlPath: string) {
  const sourceFile = ts.createSourceFile(id, code, ts.ScriptTarget.Latest, true, scriptKind(id));
  const target = localPath(id);
  const trustedSink = target !== null && path.resolve(target) === path.resolve(trustedMathHtmlPath);

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      assertCleanRoomSpecifier(node.moduleSpecifier.text, id, repositoryRoot);
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      assertCleanRoomSpecifier(node.moduleSpecifier.text, id, repositoryRoot);
    }
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const [source] = node.arguments;
      if (!source || !ts.isStringLiteral(source)) {
        cleanRoomError('Dynamic imports must use a string literal', id);
      }
      assertCleanRoomSpecifier(source.text, id, repositoryRoot);
    }
    if (!trustedSink) {
      const namesHtmlSink =
        (ts.isIdentifier(node) && node.text === HTML_SINK) ||
        ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && node.text === HTML_SINK);
      if (namesHtmlSink) {
        cleanRoomError(`${HTML_SINK} is restricted to TrustedMathHtml`, id);
      }
    }
    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
}

function inspectCss(code: string, id: string, repositoryRoot: string) {
  for (const match of code.matchAll(CSS_DEPENDENCY)) {
    const specifier = match[1] ?? match[2];
    if (specifier) assertCleanRoomSpecifier(specifier, id, repositoryRoot);
  }
}

export function inspectCleanRoomSource(code: string, id: string, options: CleanRoomGuardOptions) {
  const repositoryRoot = path.resolve(options.repositoryRoot);
  const trustedMathHtmlPath = options.trustedMathHtmlPath ?? path.join(
    repositoryRoot,
    'frontend',
    'src',
    'lib',
    'markdown',
    'TrustedMathHtml.tsx',
  );

  assertCleanRoomModulePath(id, repositoryRoot);
  if (SOURCE_EXTENSION.test(withoutQuery(id))) {
    inspectScript(code, id, repositoryRoot, trustedMathHtmlPath);
  } else if (CSS_EXTENSION.test(withoutQuery(id))) {
    inspectCss(code, id, repositoryRoot);
  }
}

export function cleanRoomGuard(options: CleanRoomGuardOptions): Plugin {
  const repositoryRoot = path.resolve(options.repositoryRoot);
  const sourceRoot = path.join(repositoryRoot, 'frontend', 'src');

  return {
    name: 'paper-study-clean-room',
    enforce: 'pre',
    load(id) {
      assertCleanRoomModulePath(id, repositoryRoot);
      return null;
    },
    transform(code, id) {
      assertCleanRoomModulePath(id, repositoryRoot);
      const target = localPath(id);
      if (target && isInside(sourceRoot, target)) {
        inspectCleanRoomSource(code, id, options);
      }
      return null;
    },
  };
}
