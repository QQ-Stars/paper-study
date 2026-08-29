import { NAV_ITEMS, type PageId } from '../nav';
import { THEMES, type ThemeId } from '../themes';
import { MoonIcon, PanelLeftIcon, PanelRightIcon } from './Icons';

interface SidebarProps {
  page: PageId;
  dueCount: number;
  libraryCount: number;
  theme: ThemeId;
  onNavigate: (page: PageId) => void;
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ page, dueCount, libraryCount, theme, onNavigate, collapsed, onToggle }: SidebarProps) {
  const currentTheme = THEMES.find((item) => item.id === theme) ?? THEMES[0];

  return (
    <aside className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`} aria-label="Paper Study 全局导航">
      <div className="sidebar__brand">
        <span className="sidebar__seal" aria-hidden="true">
          研
        </span>
        <span className="sidebar__brand-copy">
          <strong>Paper Study</strong>
          <span>个人研究工作区</span>
        </span>
        <button
          type="button"
          className="sidebar__collapse"
          aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
          aria-controls="global-nav"
          aria-expanded={!collapsed}
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
          onClick={onToggle}
        >
          {collapsed ? <PanelRightIcon size={16} /> : <PanelLeftIcon size={16} />}
        </button>
      </div>

      <nav className="sidebar__nav" id="global-nav" aria-label="全局导航">
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
        <div className="sidebar__theme">
          <button
            type="button"
            className={`sidebar__link${page === 'themes' ? ' sidebar__link--active' : ''}`}
            aria-label="主题"
            title={`主题 · 当前 ${currentTheme.label}`}
            aria-current={page === 'themes' ? 'page' : undefined}
            onClick={() => onNavigate('themes')}
          >
            <MoonIcon size={17} />
            <span className="sidebar__label">主题</span>
            <span className="sidebar__theme-current">{currentTheme.label}</span>
          </button>
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
