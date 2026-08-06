import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

import { describe, expect, it } from 'vitest';

const tokens = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8');
const globalStyles = readFileSync(resolve(process.cwd(), 'src/styles/global.css'), 'utf8');

function customProperty(name: string): string {
  const value = new RegExp(`${name}:\\s*([^;]+);`).exec(tokens)?.[1]?.trim();
  if (!value) throw new Error(`Missing design token: ${name}`);
  return value;
}

function relativeLuminance(hex: string): number {
  const match = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(hex);
  if (!match) throw new Error(`Expected a six-digit hex color, received ${hex}`);
  const channels = match.slice(1).map((channel) => Number.parseInt(channel, 16) / 255);
  const linear = channels.map((channel) => (
    channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * (linear[0] ?? 0)) + (0.7152 * (linear[1] ?? 0)) + (0.0722 * (linear[2] ?? 0));
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  const lighter = Math.max(foregroundLuminance, backgroundLuminance);
  const darker = Math.min(foregroundLuminance, backgroundLuminance);
  return (lighter + 0.05) / (darker + 0.05);
}

describe('workspace accessibility design contract', () => {
  it('keeps weak small text at WCAG AA contrast on both workspace dark surfaces', () => {
    const weakText = customProperty('--color-text-weak');

    expect(contrastRatio(weakText, '#050706')).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(weakText, '#0e1210')).toBeGreaterThanOrEqual(4.5);
  });

  it('gives native choice controls emerald state, disabled feedback, and forced-color fallbacks', () => {
    expect(globalStyles).toMatch(
      /input\[type=['"]checkbox['"]\],[\s\S]*?input\[type=['"]radio['"]\]\s*\{[^}]*accent-color:\s*var\(--color-accent\)/,
    );
    expect(globalStyles).toMatch(
      /input\[type=['"]checkbox['"]\]:disabled,[\s\S]*?input\[type=['"]radio['"]\]:disabled\s*\{[^}]*cursor:\s*not-allowed[^}]*opacity:/,
    );
    expect(globalStyles).toMatch(
      /:where\([^)]*input[^)]*\):focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-accent\)/,
    );
    expect(globalStyles).toMatch(
      /@media\s*\(forced-colors:\s*active\)\s*\{[\s\S]*?input\[type=['"]checkbox['"]\],[\s\S]*?input\[type=['"]radio['"]\][\s\S]*?forced-color-adjust:\s*auto[\s\S]*?accent-color:\s*auto/,
    );
  });
});
