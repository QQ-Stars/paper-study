import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

import type { Paper } from '../api/types';
import { filterProjectPapers, paperDisplayTitle } from './projectCreation';
import { CloseIcon, SearchIcon } from './Icons';

interface ProjectPaperPickerProps {
  papers: Paper[];
  value: string;
  query: string;
  activeIndex: number;
  onChange: (paperId: string) => void;
  onQueryChange: (query: string) => void;
  onActiveIndexChange: (index: number) => void;
  optional?: boolean;
}

function paperMeta(paper: Paper): string {
  return [paper.year, paper.venue, paper.topic].filter(Boolean).join(' · ');
}

export function ProjectPaperPicker({
  papers,
  value,
  query,
  activeIndex,
  onChange,
  onQueryChange,
  onActiveIndexChange,
  optional = false,
}: ProjectPaperPickerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => papers.find((paper) => paper.id === value), [papers, value]);
  const result = useMemo(() => filterProjectPapers(papers, query), [papers, query]);
  const safeActiveIndex = result.items.length === 0
    ? 0
    : Math.min(Math.max(activeIndex, 0), result.items.length - 1);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, []);

  useEffect(() => {
    if (!open || result.items.length === 0) return;
    const active = rootRef.current?.querySelector<HTMLElement>('[data-paper-index="' + safeActiveIndex + '"]');
    active?.scrollIntoView({ block: 'nearest' });
  }, [open, result.items.length, safeActiveIndex]);

  const choose = (paper: Paper) => {
    onChange(paper.id);
    onQueryChange('');
    setOpen(false);
    inputRef.current?.focus();
  };

  const clear = () => {
    onChange('');
    onQueryChange('');
    onActiveIndexChange(0);
    setOpen(true);
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      onActiveIndexChange(result.items.length === 0 ? 0 : Math.min(safeActiveIndex + 1, result.items.length - 1));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      onActiveIndexChange(Math.max(0, safeActiveIndex - 1));
      return;
    }
    if (event.key === 'Enter' && open && result.items[safeActiveIndex]) {
      event.preventDefault();
      choose(result.items[safeActiveIndex]);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div className="project-paper-picker" ref={rootRef}>
      <div className="project-paper-picker__control">
        <SearchIcon size={15} aria-hidden="true" />
        <input
          ref={inputRef}
          className="input project-paper-picker__input"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? listboxId : undefined}
          aria-autocomplete="list"
          aria-activedescendant={open && result.items[safeActiveIndex] ? listboxId + '-' + safeActiveIndex : undefined}
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            onQueryChange(event.target.value);
            onActiveIndexChange(0);
            setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          placeholder={optional ? '搜索并关联论文（可选）' : '搜索论文标题、作者、ID、年份…'}
        />
        {(query || value) && (
          <button type="button" className="project-paper-picker__clear" aria-label="清除论文选择" title="清除选择" onClick={clear}>
            <CloseIcon size={14} />
          </button>
        )}
      </div>

      {selected && !open && (
        <button
          type="button"
          className="project-paper-picker__selected"
          title={paperDisplayTitle(selected)}
          onClick={() => { setOpen(true); inputRef.current?.focus(); }}
        >
          <span className="project-paper-picker__selected-copy">
            <strong>{paperDisplayTitle(selected)}</strong>
            <small>{paperMeta(selected) || selected.id}</small>
          </span>
          <span className="project-paper-picker__selected-mark">已选择</span>
        </button>
      )}

      {open && (
        <div className="project-paper-picker__menu">
          <div className="project-paper-picker__menu-head">
            <span>{result.total === 0 ? '没有匹配论文' : result.total + ' 篇匹配论文'}</span>
            {result.total > result.items.length && <span>显示前 {result.items.length} 条</span>}
          </div>
          <div id={listboxId} role="listbox" aria-label="论文搜索结果" className="project-paper-picker__results">
            {result.items.length === 0 ? (
              <div className="project-paper-picker__empty">
                <strong>没有找到论文</strong>
                <span>尝试输入标题关键词、论文 ID、会议或年份。</span>
              </div>
            ) : result.items.map((paper, index) => (
              <button
                key={paper.id}
                id={listboxId + '-' + index}
                type="button"
                role="option"
                aria-selected={paper.id === value}
                data-paper-index={index}
                className={'project-paper-picker__option' + (index === safeActiveIndex ? ' is-active' : '') + (paper.id === value ? ' is-selected' : '')}
                title={paperDisplayTitle(paper)}
                onMouseEnter={() => onActiveIndexChange(index)}
                onClick={() => choose(paper)}
              >
                <span className="project-paper-picker__option-copy">
                  <strong>{paperDisplayTitle(paper)}</strong>
                  <small>{paperMeta(paper) || paper.id}</small>
                </span>
                <span className="project-paper-picker__option-id">{paper.id}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

