import { useEffect, useMemo, useRef, useState } from 'react';

import type { Paper } from '../api/types';
import { NAV_ITEMS, type PageId } from '../nav';
import { ArrowRightIcon, DocumentIcon, SearchIcon } from './Icons';

interface CommandPaletteProps {
  open: boolean;
  papers: Paper[];
  onClose: () => void;
  onNavigate: (page: PageId) => void;
  onOpenPaper: (id: string) => void;
}

interface PaletteItem {
  key: string;
  kind: 'page' | 'paper';
  title: string;
  subtitle: string;
  action: () => void;
}

export function CommandPalette({
  open,
  papers,
  onClose,
  onNavigate,
  onOpenPaper,
}: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo<PaletteItem[]>(() => {
    const q = query.trim().toLowerCase();
    const pages: PaletteItem[] = NAV_ITEMS.filter(
      (item) => !q || item.label.toLowerCase().includes(q) || item.hint.includes(query.trim()),
    ).map((item) => ({
      key: `page-${item.id}`,
      kind: 'page',
      title: item.label,
      subtitle: item.hint,
      action: () => onNavigate(item.id),
    }));
    const hits: PaletteItem[] = papers
      .filter(
        (paper) =>
          q &&
          ((paper.title ?? '').toLowerCase().includes(q) ||
            (paper.title_zh ?? '').includes(query.trim()) ||
            (paper.venue ?? '').toLowerCase().includes(q) ||
            (paper.topic ?? '').includes(query.trim()) ||
            (paper.id ?? '').toLowerCase().includes(q)),
      )
      .slice(0, 6)
      .map((paper) => ({
        key: `paper-${paper.id}`,
        kind: 'paper',
        title: paper.title_zh || paper.title || paper.id,
        subtitle: `${paper.venue ?? ''} ${paper.year ?? ''} · ${paper.topic ?? ''}`,
        action: () => onOpenPaper(paper.id),
      }));
    return [...pages, ...hits];
  }, [query, papers, onNavigate, onOpenPaper]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(0);
      window.setTimeout(() => inputRef.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  if (!open) return null;

  const run = (item: PaletteItem) => {
    item.action();
    onClose();
  };

  return (
    <div
      className="palette-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <div className="palette__input-row">
          <SearchIcon size={16} />
          <input
            ref={inputRef}
            className="palette__input"
            placeholder="输入页面名或论文关键词…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') onClose();
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setCursor((value) => Math.min(value + 1, items.length - 1));
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault();
                setCursor((value) => Math.max(value - 1, 0));
              }
              if (event.key === 'Enter' && items[cursor]) run(items[cursor]);
            }}
          />
          <span className="kbd">Esc</span>
        </div>

        <ul className="palette__list" role="listbox" aria-label="命令结果">
          {items.length === 0 && (
            <li className="palette__empty">没有匹配的命令或论文</li>
          )}
          {items.map((item, index) => (
            <li key={item.key}>
              <button
                type="button"
                role="option"
                aria-selected={index === cursor}
                className={`palette__item${index === cursor ? ' palette__item--active' : ''}`}
                onMouseEnter={() => setCursor(index)}
                onClick={() => run(item)}
              >
                {item.kind === 'page' ? (
                  <ArrowRightIcon size={14} />
                ) : (
                  <DocumentIcon size={14} />
                )}
                <span className="palette__item-copy">
                  <strong>{item.title}</strong>
                  <small>{item.subtitle}</small>
                </span>
                <span className="palette__item-kind">
                  {item.kind === 'page' ? '跳转' : '打开'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
