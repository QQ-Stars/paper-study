import { THEMES, type ThemeId } from '../themes';
import { CheckIcon, MoonIcon, SparkIcon } from './Icons';

function ThemePreview({ id, active, onSelect }: { id: ThemeId; active: boolean; onSelect: () => void }) {
  const definition = THEMES.find((item) => item.id === id) ?? THEMES[0];
  const night = id === 'night';
  return (
    <article className={`theme-card${active ? ' theme-card--active' : ''}`} data-theme-preview={id}>
      <div className="theme-card__chrome">
        <div className="theme-card__chrome-dot" />
        <span>Paper Study / 研究工作区</span>
        <span className="theme-card__chrome-state">{active ? '当前使用' : '预览'}</span>
      </div>
      <div className="theme-card__canvas">
        <aside className="theme-card__mini-sidebar">
          <div className="theme-card__mini-brand"><b>研</b><span>Paper Study</span></div>
          <div className="theme-card__mini-nav is-active"><i />今日</div>
          <div className="theme-card__mini-nav"><i />文献库 <em>252</em></div>
          <div className="theme-card__mini-nav"><i />论文复现</div>
          <div className="theme-card__mini-nav"><i />洞察</div>
          <div className="theme-card__mini-spacer" />
          <div className="theme-card__mini-nav"><i />主题</div>
        </aside>
        <div className="theme-card__workspace">
          <div className="theme-card__topline"><span>{night ? '8 月 27 日 · 夜读模式' : '8 月 27 日 · 今日研究'}</span><span className="theme-card__kbd">⌘ K</span></div>
          <div className="theme-card__greeting"><span className="eyebrow">{night ? 'FOCUS MODE' : 'RESEARCH DESK'}</span><h3>{night ? '安静下来，继续深入。' : '早上好，研究者。'}</h3><p>{night ? '暖白文字 · 夜墨底 · 亮朱砂' : '宣纸底 · 墨字 · 朱砂点缀'}</p></div>
          <div className="theme-card__metrics"><div><small>文献库</small><strong>252</strong></div><div><small>已掌握</small><strong>8</strong></div><div><small>今日待复习</small><strong>0</strong></div></div>
          <div className="theme-card__paper"><div className="theme-card__paper-mark">{night ? 'N' : 'A'}</div><div><strong>{night ? 'LLM-CAS: 面向实时幻觉纠正' : 'Attention Is All You Need'}</strong><span>{night ? '继续昨晚的阅读 · 24 min' : 'Transformer 架构 · 论文精读'}</span></div><button type="button" onClick={onSelect}>{active ? <CheckIcon size={13} /> : <ArrowGlyph />}{active ? '已应用' : '应用主题'}</button></div>
        </div>
      </div>
      <div className="theme-card__footer"><div><h2>{definition.label}</h2><p>{definition.description}</p></div><span className="theme-card__swatches"><i /><i /><i /></span></div>
    </article>
  );
}

function ArrowGlyph() { return <span aria-hidden="true">↗</span>; }

interface ThemePageProps {
  theme: ThemeId;
  onChange: (theme: ThemeId) => void;
}

export function ThemePage({ theme, onChange }: ThemePageProps) {

  return (
    <div className="page page-enter theme-page">
      <header className="theme-page__header">
        <div><span className="eyebrow">APPEARANCE / 01</span><h1 className="display-title">主题</h1><p>为长时间的阅读、复现与思考，选择一套顺手的研究界面。</p></div>
        <div className="theme-page__meta"><SparkIcon size={16} /><span>{theme === 'night' ? '夜间专注已开启' : '日间专注已开启'}</span></div>
      </header>
      <section className="theme-page__intro" aria-label="主题说明"><div><MoonIcon size={17} /><strong>每张卡片都是真实界面预览</strong></div><span>切换会立即应用，并保存在本机。</span></section>
      <div className="theme-page__grid">
        {THEMES.map((item) => <ThemePreview key={item.id} id={item.id} active={item.id === theme} onSelect={() => onChange(item.id)} />)}
      </div>
    </div>
  );
}
