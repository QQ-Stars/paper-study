import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';

import { NAV_ITEMS, type PageId } from '../nav';
import { applyTheme, readStoredTheme, THEMES, type ThemeId } from '../themes';
import { CheckIcon, MoonIcon } from './Icons';

interface SidebarProps {
  page: PageId;
  dueCount: number;
  libraryCount: number;
  onNavigate: (page: PageId) => void;
}

export function Sidebar({ page, dueCount, libraryCount, onNavigate }: SidebarProps) {
  const [theme, setTheme] = useState<ThemeId>(() => readStoredTheme());
  const [themeOpen, setThemeOpen] = useState(false);
  const themeRootRef = useRef<HTMLDivElement>(null);
  const themeToggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  /* 面板打开：外部点击关闭、Escape 关闭并归还焦点、焦点落入当前选中项 */
  useEffect(() => {
    if (!themeOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!themeRootRef.current?.contains(event.target as Node)) setThemeOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setThemeOpen(false);
      themeToggleRef.current?.focus();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    const initial =
      panelRef.current?.querySelector<HTMLElement>('[aria-checked="true"]') ??
      panelRef.current?.querySelector<HTMLElement>('[role="radio"]');
    initial?.focus();
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [themeOpen]);

  const onPanelKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const items = [...(panelRef.current?.querySelectorAll<HTMLElement>('[role="radio"]') ?? [])];
    if (items.length === 0) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === 'ArrowDown'
        ? (index + 1) % items.length
        : (index - 1 + items.length) % items.length;
    items[next]?.focus();
  };

  const currentTheme = THEMES.find((item) => item.id === theme) ?? THEMES[0];

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__seal" aria-hidden="true">
          研
        </span>
        <span className="sidebar__brand-copy">
          <strong>Paper Study</strong>
          <span>个人研究工作区</span>
        </span>
      </div>

      <nav className="sidebar__nav" aria-label="全局导航">
        {NAV_ITEMS.map((item) => {
          const active = item.id === page;
          const count =
            item.id === 'reviews' ? dueCount : item.id === 'library' ? libraryCount : 0;
          return (
            <button
              key={item.id}
              type="button"
              className={`sidebar__link${active ? ' sidebar__link--active' : ''}`}
              aria-label={item.label}
              aria-current={active ? 'page' : undefined}
              title={`${item.label} · ${item.hint}`}
              onClick={() => onNavigate(item.id)}
            >
              <item.Icon size={17} />
              <span className="sidebar__label">{item.label}</span>
              {count > 0 && (
                <span
                  className={`sidebar__count${item.id === 'reviews' ? ' sidebar__count--due' : ''}`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="sidebar__foot">
        <div className="sidebar__theme" ref={themeRootRef}>
          <button
            ref={themeToggleRef}
            type="button"
            className="sidebar__link"
            aria-label="主题"
            title={`主题 · 当前 ${currentTheme.label}`}
            aria-haspopup="true"
            aria-expanded={themeOpen}
            aria-controls="sidebar-theme-panel"
            onClick={() => setThemeOpen((open) => !open)}
          >
            <MoonIcon size={17} />
            <span className="sidebar__label">主题</span>
            <span className="sidebar__theme-current">{currentTheme.label}</span>
          </button>
          {themeOpen && (
            <div
              ref={panelRef}
              id="sidebar-theme-panel"
              className="sidebar__theme-panel"
              role="radiogroup"
              aria-label="选择主题"
              onKeyDown={onPanelKeyDown}
            >
              {THEMES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="radio"
                  aria-checked={item.id === theme}
                  className={`sidebar__theme-option${item.id === theme ? ' is-active' : ''}`}
                  onClick={() => setTheme(item.id)}
                >
                  <span className="sidebar__theme-swatch" data-swatch={item.id} aria-hidden="true" />
                  <span className="sidebar__theme-copy">
                    <strong>{item.label}</strong>
                    <small>{item.description}</small>
                  </span>
                  {item.id === theme && <CheckIcon size={14} />}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="sidebar__locality">
          <span className="sidebar__locality-dot" aria-hidden="true" />
          {/* 文献库计数已在上方「文献库」导航徽章展示，此处不再重复 */}
          <p>本地运行 · 数据仅保存在本机</p>
        </div>
      </div>
    </aside>
  );
}
