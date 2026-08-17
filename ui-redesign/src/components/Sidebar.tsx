import { NAV_ITEMS, type PageId } from '../nav';

interface SidebarProps {
  page: PageId;
  dueCount: number;
  libraryCount: number;
  onNavigate: (page: PageId) => void;
}

export function Sidebar({ page, dueCount, libraryCount, onNavigate }: SidebarProps) {
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
              aria-current={active ? 'page' : undefined}
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
        <div className="sidebar__locality">
          <span className="sidebar__locality-dot" aria-hidden="true" />
          <p>
            本地运行 · 数据仅保存在本机
            <br />
            文献库共 {libraryCount} 篇
          </p>
        </div>
      </div>
    </aside>
  );
}
