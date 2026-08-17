import { PlusIcon, SearchIcon } from './Icons';

interface TopbarProps {
  pageLabel: string;
  pageHint: string;
  onOpenPalette: () => void;
  onAcquire: () => void;
}

const TODAY = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date());

export function Topbar({ pageLabel, pageHint, onOpenPalette, onAcquire }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar__route">
        <span className="eyebrow">{TODAY}</span>
        <h1 className="topbar__title">
          {pageLabel}
          <span className="topbar__hint">{pageHint}</span>
        </h1>
      </div>

      <div className="topbar__actions">
        <button type="button" className="topbar__search" onClick={onOpenPalette}>
          <SearchIcon size={15} />
          <span>检索论文、跳转页面…</span>
          <span className="kbd">Ctrl</span>
          <span className="kbd">K</span>
        </button>
        <button type="button" className="btn btn--primary" onClick={onAcquire}>
          <PlusIcon size={15} />
          采集新论文
        </button>
      </div>
    </header>
  );
}
