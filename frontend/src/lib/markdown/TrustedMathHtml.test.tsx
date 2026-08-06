import { render } from '@testing-library/react';
import type { KatexOptions } from 'katex';
import { describe, expect, it, vi } from 'vitest';

import {
  renderMathToTrustedHtml,
  sanitizeKatexHtml,
} from './katexAllowlist';
import { TrustedMathHtml } from './TrustedMathHtml';

describe('the trusted KaTeX boundary', () => {
  it('always renders KaTeX with trust disabled and expansion bounded', () => {
    const renderer = vi.fn((formula: string, options: KatexOptions) => {
      expect(formula).toBe('x');
      expect(options).toEqual(expect.objectContaining({
        displayMode: true,
        maxExpand: 1_000,
        throwOnError: true,
        trust: false,
      }));
      return '<span class="katex"><span style="height:0.8141em;vertical-align:-0.1944em">x</span></span>';
    });

    const markup = renderMathToTrustedHtml('x', true, renderer);

    expect(markup).not.toBeNull();
    expect(renderer).toHaveBeenCalledTimes(1);
  });

  it('accepts only allowlisted KaTeX/MathML markup and safe inline styles', () => {
    const safe = sanitizeKatexHtml([
      '<span class="katex">',
      '<math xmlns="http://www.w3.org/1998/Math/MathML">',
      '<semantics><mrow><mi>x</mi></mrow>',
      '<annotation encoding="application/x-tex">x</annotation></semantics>',
      '</math>',
      '<span class="katex-html" aria-hidden="true">',
      '<span style="height:0.8141em;vertical-align:-0.1944em;margin-right:0.05em">x</span>',
      '</span></span>',
    ].join(''));

    expect(safe).not.toBeNull();
    expect(safe).toContain('height:0.8141em');
    expect(safe).toContain('vertical-align:-0.1944em');

    const hostileMarkup = [
      '<span class="katex"><script>alert(1)</script></span>',
      '<span class="katex" onclick="alert(1)">x</span>',
      '<span class="katex"><img src=x onerror=alert(1)></span>',
      '<span class="katex"><span style="background-image:url(javascript:alert(1))">x</span></span>',
      '<span class="katex"><span style="height:expression(alert(1))">x</span></span>',
      '<span class="katex"><math><mi href="javascript:alert(1)">x</mi></math></span>',
    ];

    for (const markup of hostileMarkup) {
      expect(sanitizeKatexHtml(markup), markup).toBeNull();
    }
  });

  it('renders ordinary formulas through the trusted sink', () => {
    const { container } = render(
      <TrustedMathHtml value={'\\frac{1}{2}'} display />,
    );

    expect(container.querySelector('.katex')).toBeInTheDocument();
    expect(container.querySelector('math')).not.toBeNull();
    expect(container.querySelector('script')).not.toBeInTheDocument();
  });

  it('falls back to inert React text when KaTeX rejects malformed math', () => {
    const formula = '\\frac{';
    const { container } = render(<TrustedMathHtml value={formula} />);

    expect(container.textContent).toBe(formula);
    expect(container.querySelector('.katex')).not.toBeInTheDocument();
  });
});
