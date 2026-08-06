import { NavLink } from 'react-router-dom';

const destinations = [
  { to: '/dashboard', label: '今日', glyph: '今' },
  { to: '/library', label: '文献库', glyph: '库' },
  { to: '/reviews', label: '复习', glyph: '习' },
  { to: '/acquire', label: '采集', glyph: '采' },
  { to: '/jobs', label: '任务', glyph: '任' },
  { to: '/insights', label: '洞察', glyph: '析' },
  { to: '/settings', label: '设置', glyph: '设' },
] as const;

export function GlobalNavigation() {
  return (
    <nav className="global-nav" aria-label="全局导航">
      <div className="global-nav__brand" aria-label="Paper Study">
        <span className="global-nav__mark" aria-hidden="true">
          PS
        </span>
        <span className="global-nav__brand-copy">
          <strong>Paper Study</strong>
          <span>研究工作区</span>
        </span>
      </div>

      <div className="global-nav__destinations">
        {destinations.map((destination) => (
          <NavLink
            key={destination.to}
            to={destination.to}
            end={destination.to !== '/jobs'}
            aria-label={destination.label}
            className={({ isActive }) =>
              `global-nav__link${isActive ? ' global-nav__link--active' : ''}`
            }
          >
            <span className="global-nav__glyph" aria-hidden="true">
              {destination.glyph}
            </span>
            <span className="global-nav__label">{destination.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
