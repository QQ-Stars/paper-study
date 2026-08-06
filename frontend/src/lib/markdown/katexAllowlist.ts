import katex from 'katex';
import type { KatexOptions } from 'katex';

declare const trustedMathMarkup: unique symbol;

export type TrustedMathMarkup = string & {
  readonly [trustedMathMarkup]: true;
};

const htmlTags = new Set(['span']);
const mathMlTags = new Set([
  'annotation',
  'math',
  'menclose',
  'merror',
  'mfrac',
  'mglyph',
  'mi',
  'mmultiscripts',
  'mn',
  'mo',
  'mover',
  'mpadded',
  'mphantom',
  'mprescripts',
  'mroot',
  'mrow',
  'ms',
  'mspace',
  'msqrt',
  'mstyle',
  'msub',
  'msubsup',
  'msup',
  'mtable',
  'mtd',
  'mtext',
  'mtr',
  'munder',
  'munderover',
  'none',
  'semantics',
]);
const svgTags = new Set(['path', 'svg']);

const attributesByTag: Readonly<Record<string, ReadonlySet<string>>> = {
  annotation: new Set(['encoding']),
  math: new Set(['display', 'xmlns']),
  menclose: new Set(['notation']),
  mfrac: new Set(['linethickness']),
  mi: new Set(['mathvariant']),
  mn: new Set(['mathvariant']),
  mo: new Set([
    'accent',
    'fence',
    'form',
    'largeop',
    'lspace',
    'maxsize',
    'minsize',
    'movablelimits',
    'rspace',
    'separator',
    'stretchy',
  ]),
  mover: new Set(['accent']),
  mpadded: new Set(['depth', 'height', 'lspace', 'voffset', 'width']),
  mspace: new Set(['depth', 'height', 'linebreak', 'width']),
  mstyle: new Set([
    'displaystyle',
    'mathbackground',
    'mathcolor',
    'mathvariant',
    'scriptlevel',
  ]),
  mtable: new Set(['columnalign', 'columnspacing', 'rowalign', 'rowspacing']),
  mtd: new Set(['columnalign', 'rowalign']),
  mtr: new Set(['rowalign']),
  munder: new Set(['accentunder']),
  munderover: new Set(['accent', 'accentunder']),
  path: new Set(['d', 'fill', 'stroke', 'stroke-width']),
  span: new Set(['aria-hidden', 'class', 'style']),
  svg: new Set([
    'aria-hidden',
    'height',
    'preserveaspectratio',
    'style',
    'viewbox',
    'width',
    'xmlns',
  ]),
};

const numericCssValue = /^-?(?:\d+(?:\.\d+)?|\.\d+)(?:em|ex|px|pt|rem|%)?$/i;
const colorCssValue = /^(?:#[0-9a-f]{3,8}|[a-z]{1,32}|rgba?\([0-9.% ,/+-]+\)|hsla?\([0-9.% ,/+-]+\))$/i;
const dangerousValue = /(?:url\s*\(|expression\s*\(|javascript\s*:|data\s*:|@import|\\)/i;

const numericStyleProperties = new Set([
  'border-bottom-width',
  'border-left-width',
  'border-right-width',
  'border-top-width',
  'border-width',
  'bottom',
  'font-size',
  'height',
  'left',
  'margin',
  'margin-bottom',
  'margin-left',
  'margin-right',
  'margin-top',
  'max-height',
  'max-width',
  'min-height',
  'min-width',
  'padding',
  'padding-bottom',
  'padding-left',
  'padding-right',
  'padding-top',
  'right',
  'top',
  'vertical-align',
  'width',
]);
const colorStyleProperties = new Set([
  'background-color',
  'border-color',
  'color',
]);

function safeStyleValue(property: string, value: string): boolean {
  if (!value || value.length > 128 || dangerousValue.test(value)) return false;
  if (numericStyleProperties.has(property)) return numericCssValue.test(value);
  if (colorStyleProperties.has(property)) return colorCssValue.test(value);
  if (property === 'border-style') return value === 'solid' || value === 'none';
  if (property === 'box-sizing') return value === 'border-box' || value === 'content-box';
  if (property === 'display') return /^(?:block|inline|inline-block|none)$/.test(value);
  if (property === 'opacity') return /^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(value);
  if (property === 'overflow') return /^(?:hidden|visible)$/.test(value);
  if (property === 'position') return /^(?:absolute|relative)$/.test(value);
  if (property === 'text-align') return /^(?:center|left|right)$/.test(value);
  if (property === 'white-space') return /^(?:normal|nowrap)$/.test(value);
  return false;
}

function sanitizeStyle(value: string): string | null {
  if (!value || value.length > 4_096 || dangerousValue.test(value)) return null;
  const declarations: string[] = [];
  const seen = new Set<string>();

  for (const rawDeclaration of value.split(';')) {
    const declaration = rawDeclaration.trim();
    if (!declaration) continue;
    const separator = declaration.indexOf(':');
    if (separator < 1) return null;
    const property = declaration.slice(0, separator).trim().toLowerCase();
    const styleValue = declaration.slice(separator + 1).trim();
    if (!/^[a-z-]+$/.test(property) || seen.has(property)) return null;
    if (!safeStyleValue(property, styleValue)) return null;
    seen.add(property);
    declarations.push(`${property}:${styleValue}`);
  }

  return declarations.length ? `${declarations.join(';')};` : null;
}

function allowedAttributes(tag: string): ReadonlySet<string> {
  return attributesByTag[tag] ?? new Set<string>();
}

function safeAttributeValue(tag: string, name: string, value: string): boolean {
  if (!value || value.length > 8_192 || dangerousValue.test(value)) return false;
  if (name === 'aria-hidden') return value === 'true';
  if (name === 'class') {
    return value.length <= 1_024
      && /^[A-Za-z0-9_-]+(?:\s+[A-Za-z0-9_-]+)*$/.test(value);
  }
  if (name === 'encoding') return value === 'application/x-tex';
  if (name === 'xmlns') {
    return value === 'http://www.w3.org/1998/Math/MathML'
      || (tag === 'svg' && value === 'http://www.w3.org/2000/svg');
  }
  if (name === 'd') return /^[MmZzLlHhVvCcSsQqTtAaEe0-9.,+\-\s]+$/.test(value);
  if (name === 'fill' || name === 'stroke') {
    return /^(?:currentColor|none|#[0-9a-f]{3,8})$/i.test(value);
  }
  return /^[A-Za-z0-9 .,+%#()/_-]+$/.test(value);
}

function sanitizeElement(element: Element): boolean {
  const tag = element.localName.toLowerCase();
  if (!htmlTags.has(tag) && !mathMlTags.has(tag) && !svgTags.has(tag)) return false;

  const allowed = allowedAttributes(tag);
  for (const attribute of [...element.attributes]) {
    const name = attribute.localName.toLowerCase();
    if (!allowed.has(name)) return false;
    if (name === 'style') {
      const style = sanitizeStyle(attribute.value);
      if (style === null) return false;
      element.setAttribute(attribute.name, style);
      continue;
    }
    if (!safeAttributeValue(tag, name, attribute.value)) return false;
  }

  for (const child of [...element.childNodes]) {
    if (child.nodeType === 3) continue;
    if (child.nodeType !== 1 || !sanitizeElement(child as Element)) return false;
  }
  return true;
}

export function sanitizeKatexHtml(markup: string): TrustedMathMarkup | null {
  if (typeof DOMParser === 'undefined' || !markup || markup.length > 500_000) return null;

  const parsed = new DOMParser().parseFromString(markup, 'text/html');
  const children = [...parsed.body.childNodes];
  if (children.length !== 1 || children[0]?.nodeType !== 1) return null;

  const root = children[0] as Element;
  if (root.localName.toLowerCase() !== 'span') return null;
  const rootClasses = root.getAttribute('class')?.split(/\s+/) ?? [];
  if (!rootClasses.includes('katex') && !rootClasses.includes('katex-display')) return null;
  if (!sanitizeElement(root)) return null;

  return parsed.body.innerHTML as TrustedMathMarkup;
}

export type KatexStringRenderer = (
  formula: string,
  options: KatexOptions,
) => string;

const maximumFormulaLength = 20_000;

export function renderMathToTrustedHtml(
  formula: string,
  display: boolean,
  renderer: KatexStringRenderer = katex.renderToString,
): TrustedMathMarkup | null {
  if (!formula || formula.length > maximumFormulaLength) return null;

  const options = {
    displayMode: display,
    maxExpand: 1_000,
    output: 'htmlAndMathml',
    strict: 'error',
    throwOnError: true,
    trust: false,
  } satisfies KatexOptions;

  try {
    return sanitizeKatexHtml(renderer(formula, options));
  } catch {
    return null;
  }
}
