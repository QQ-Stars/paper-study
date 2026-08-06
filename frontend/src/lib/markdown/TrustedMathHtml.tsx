import 'katex/dist/katex.min.css';

import { renderMathToTrustedHtml } from './katexAllowlist';

export interface TrustedMathHtmlProps {
  value: string;
  display?: boolean;
  className?: string;
}

export function TrustedMathHtml({
  value,
  display = false,
  className,
}: TrustedMathHtmlProps) {
  const markup = renderMathToTrustedHtml(value, display);
  const Element = display ? 'div' : 'span';
  if (markup === null) {
    return (
      <Element className={className} data-math-fallback="true">
        {value}
      </Element>
    );
  }

  return (
    <Element
      className={className}
      data-math-display={display ? 'block' : 'inline'}
      dangerouslySetInnerHTML={{ __html: markup }}
    />
  );
}
