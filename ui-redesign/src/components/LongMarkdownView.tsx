import { useEffect, useMemo, useRef, useState } from 'react';

import { MarkdownView } from './MarkdownView';
import { paginateMarkdown } from './longDocument';

interface LongMarkdownViewProps {
  source: string;
  label?: string;
}

export function LongMarkdownView({ source, label = '长文内容' }: LongMarkdownViewProps) {
  const pages = useMemo(() => paginateMarkdown(source), [source]);
  const [pageIndex, setPageIndex] = useState(0);
  const rootRef = useRef<HTMLElement>(null);
  const safePageIndex = Math.min(pageIndex, Math.max(0, pages.length - 1));

  useEffect(() => {
    setPageIndex(0);
  }, [source]);

  if (pages.length === 1) return <MarkdownView source={pages[0]} />;

  const changePage = (nextPage: number) => {
    const bounded = Math.max(0, Math.min(pages.length - 1, nextPage));
    setPageIndex(bounded);
    window.requestAnimationFrame(() => {
      rootRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
    });
  };

  const controls = (position: 'top' | 'bottom') => (
    <nav className={`long-document__controls long-document__controls--${position}`} aria-label={`${label}页码`}>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        disabled={safePageIndex === 0}
        onClick={() => changePage(safePageIndex - 1)}
      >
        ← 上一页
      </button>
      <label className="long-document__page-picker">
        <span className="sr-only">选择{label}页码</span>
        <select
          className="input input--sm"
          value={safePageIndex}
          onChange={(event) => changePage(Number(event.target.value))}
        >
          {pages.map((_, index) => (
            <option key={index} value={index}>
              第 {index + 1} / {pages.length} 页
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        disabled={safePageIndex >= pages.length - 1}
        onClick={() => changePage(safePageIndex + 1)}
      >
        下一页 →
      </button>
    </nav>
  );

  return (
    <section ref={rootRef} className="long-document" aria-label={`${label}长文模式`}>
      <header className="long-document__bar">
        <div className="long-document__summary">
          <span className="eyebrow">长文模式</span>
          <span>共 {source.length.toLocaleString()} 字符 · 仅渲染当前页以保持流畅</span>
        </div>
        {controls('top')}
        <span className="sr-only" role="status" aria-live="polite">
          当前显示{label}第 {safePageIndex + 1} 页，共 {pages.length} 页
        </span>
      </header>
      <div className="long-document__page" aria-label={`${label}第 ${safePageIndex + 1} 页`}>
        <MarkdownView source={pages[safePageIndex]} />
      </div>
      <footer className="long-document__footer">
        <span>第 {safePageIndex + 1} / {pages.length} 页</span>
        {controls('bottom')}
      </footer>
    </section>
  );
}
