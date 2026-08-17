import { NavLink } from 'react-router-dom';
import {
  HouseSimple,
  Books,
  GraduationCap,
  MagnifyingGlass,
  ListChecks,
  TrendUp,
  GearSix,
} from '@phosphor-icons/react';
import type { ComponentType } from 'react';

interface Destination {
  readonly to: string;
  readonly label: string;
  readonly Icon: ComponentType<{ size?: number | string; weight?: 'thin' | 'light' | 'regular' | 'bold' | 'fill' | 'duotone' }>;
}

const destinations: Destination[] = [
  { to: '/dashboard', label: '今日', Icon: HouseSimple },
  { to: '/library', label: '文献库', Icon: Books },
  { to: '/reviews', label: '复习', Icon: GraduationCap },
  { to: '/acquire', label: '采集', Icon: MagnifyingGlass },
  { to: '/jobs', label: '任务', Icon: ListChecks },
  { to: '/insights', label: '洞察', Icon: TrendUp },
  { to: '/settings', label: '设置', Icon: GearSix },
];

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
              <destination.Icon size={18} weight="regular" />
            </span>
            <span className="global-nav__label">{destination.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
