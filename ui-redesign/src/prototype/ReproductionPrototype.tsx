import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { MarkdownView } from '../components/MarkdownView';
import {
  ArrowRightIcon,
  BooksIcon,
  CheckIcon,
  CycleIcon,
  DocumentIcon,
  GearIcon,
  SearchIcon,
  SparkIcon,
} from '../components/Icons';
import { NAV_ITEMS } from '../nav';

import './reproductionPrototype.css';

type PrototypeVariant = 'a' | 'b' | 'c';
type EditorMode = 'edit' | 'preview' | 'split';

interface PrototypeProps {
  initialVariant: PrototypeVariant;
}

interface Section {
  id: string;
  label: string;
  state: 'done' | 'active' | 'pending';
}

const VARIANTS: Array<{ id: PrototypeVariant; label: string; hint: string }> = [
  { id: 'a', label: '方案 A', hint: '章节树 · Markdown · 大纲' },
  { id: 'b', label: '方案 B', hint: '专注编辑 · 按需检查器' },
  { id: 'c', label: '方案 C', hint: '项目主从 · 实验优先' },
];

const MARKDOWN = `# 复现目标

验证论文方法在公开数据集上的可复现程度，记录环境差异、实验结果与下一步排查计划。

## 原论文方法

论文提出一种分阶段训练策略，在相同数据切分下比较基线与改进方法。

## 环境与依赖

\`Python 3.11\` · \`PyTorch 2.4\` · CUDA 12.1 · 固定随机种子 \`42\`

## 实验配置

| 配置 | 本次复现 | 原论文 |
| --- | --- | --- |
| 数据集 | ImageNet-1K | ImageNet-1K |
| Epochs | 90 | 90 |
| Batch size | 256 | 256 |

## 执行记录

- 2026-08-25：完成依赖安装与数据校验
- 2026-08-26：完成第一轮基线运行

## 结果对照

第一轮 Top-1 为 **74.2%**，较论文报告值低 0.8 个百分点，待检查数据预处理与学习率调度。

## 偏差与问题

当前差异尚未归因，下一步复核增强策略和 checkpoint 选择。

## 结论与下一步

基线运行链路可复现，改进方法需要补充两轮对照实验。`;

const SECTIONS: Section[] = [
  { id: 'goal', label: '复现目标', state: 'done' },
  { id: 'method', label: '原论文方法', state: 'done' },
  { id: 'environment', label: '环境与依赖', state: 'active' },
  { id: 'config', label: '实验配置', state: 'pending' },
  { id: 'records', label: '执行记录', state: 'pending' },
  { id: 'results', label: '结果对照', state: 'pending' },
  { id: 'gaps', label: '偏差与问题', state: 'pending' },
  { id: 'next', label: '结论与下一步', state: 'pending' },
];

const OUTLINE = [
  { id: 'goal', label: '复现目标', level: 1 },
  { id: 'method', label: '原论文方法', level: 1 },
  { id: 'environment', label: '环境与依赖', level: 1 },
  { id: 'config', label: '实验配置', level: 1 },
  { id: 'records', label: '执行记录', level: 1 },
  { id: 'results', label: '结果对照', level: 1 },
  { id: 'gaps', label: '偏差与问题', level: 1 },
  { id: 'next', label: '结论与下一步', level: 1 },
];

const PROJECTS = [
  { title: 'ViT 分阶段训练复现', paper: 'An Image is Worth 16x16 Words', status: '准备中', updated: '今天 09:42', active: true },
  { title: '扩散模型基线', paper: 'Denoising Diffusion Probabilistic Models', status: '计划中', updated: '昨天 18:20', active: false },
  { title: 'LoRA 微调对照', paper: 'LoRA: Low-Rank Adaptation', status: '已完成', updated: '8 月 21 日', active: false },
];

const RUNS = [
  { name: '基线运行 #02', status: 'running', duration: '00:38:21', metric: 'Top-1 74.2%', detail: '4 / 6 checkpoints' },
  { name: '数据校验', status: 'done', duration: '00:04:12', metric: '通过', detail: 'ImageNet-1K · 1,281,167 samples' },
  { name: '基线运行 #01', status: 'failed', duration: '00:12:08', metric: '中止', detail: 'CUDA out of memory' },
];

function PrototypeNav() {
  const prototypeItems = [
    ...NAV_ITEMS.slice(0, 2),
    { id: 'reproduction', label: '论文复现', hint: '记录实验、结果与复现笔记', Icon: DocumentIcon },
    ...NAV_ITEMS.slice(2),
  ];
  return (
    <aside className="sidebar prototype-sidebar" aria-label="全局导航（原型）">
      <div className="sidebar__brand">
        <span className="sidebar__seal" aria-hidden="true">研</span>
        <span className="sidebar__brand-copy">
          <strong>Paper Study</strong>
          <span>个人研究工作区</span>
        </span>
      </div>
      <nav className="sidebar__nav">
        {prototypeItems.map((item) => {
          const active = item.id === 'reproduction';
          return (
            <button
              key={item.id}
              type="button"
              className={`sidebar__link${active ? ' sidebar__link--active' : ''}`}
              aria-current={active ? 'page' : undefined}
              aria-label={item.label}
              title={`${item.label} · ${item.hint}`}
            >
              <item.Icon size={17} />
              <span className="sidebar__label">{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="sidebar__foot">
        <div className="sidebar__locality">
          <span className="sidebar__locality-dot" aria-hidden="true" />
          <p>原型模式 · 数据仅在内存中</p>
        </div>
      </div>
    </aside>
  );
}

function PrototypeHeader({ variant }: { variant: PrototypeVariant }) {
  const current = VARIANTS.find((item) => item.id === variant) ?? VARIANTS[0];
  return (
    <header className="prototype-header">
      <div>
        <span className="eyebrow">PAPER REPRODUCTION · PROTOTYPE</span>
        <h1>论文复现</h1>
      </div>
      <div className="prototype-header__context">
        <span className="status-dot status-dot--active" aria-hidden="true" />
        <span>{current.label} · {current.hint}</span>
        <span className="prototype-header__revision">v0 · 未连接后端</span>
      </div>
    </header>
  );
}

function ProjectBadge({ status }: { status: string }) {
  const state = status === '已完成' ? 'done' : status === '运行中' ? 'running' : 'planned';
  return <span className={`repro-badge repro-badge--${state}`}>{status}</span>;
}

function SectionTree({ selected, onSelect }: { selected: string; onSelect: (id: string) => void }) {
  return (
    <section className="repro-panel repro-tree" aria-labelledby="repro-tree-title">
      <div className="repro-panel__heading">
        <div>
          <span className="eyebrow">PROJECT</span>
          <h2 id="repro-tree-title">ViT 分阶段训练复现</h2>
        </div>
        <button className="icon-button" type="button" aria-label="项目设置" title="项目设置"><GearIcon size={15} /></button>
      </div>
      <p className="repro-tree__paper">↳ An Image is Worth 16x16 Words</p>
      <div className="repro-tree__meta"><ProjectBadge status="准备中" /><span>更新于今天 09:42</span></div>
      <div className="repro-tree__rule" />
      <span className="repro-tree__label">复现笔记</span>
      <ol className="repro-tree__list">
        {SECTIONS.map((section, index) => (
          <li key={section.id}>
            <button
              type="button"
              className={`repro-tree__item${selected === section.id ? ' repro-tree__item--active' : ''}`}
              onClick={() => onSelect(section.id)}
            >
              <span className={`repro-tree__index repro-tree__index--${section.state}`}>{section.state === 'done' ? <CheckIcon size={11} /> : index + 1}</span>
              <span>{section.label}</span>
            </button>
          </li>
        ))}
      </ol>
      <button className="btn btn--ghost btn--sm repro-tree__add" type="button"><DocumentIcon size={14} /> 新增章节</button>
    </section>
  );
}

function Outline({ selected, onSelect }: { selected: string; onSelect: (id: string) => void }) {
  return (
    <section className="repro-panel repro-outline" aria-labelledby="repro-outline-title">
      <div className="repro-panel__heading">
        <div>
          <span className="eyebrow">OUTLINE</span>
          <h2 id="repro-outline-title">文档大纲</h2>
        </div>
        <span className="repro-outline__count">8 节</span>
      </div>
      <nav aria-label="复现文档大纲">
        <ol className="repro-outline__list">
          {OUTLINE.map((item) => (
            <li key={item.id}>
              <button type="button" className={selected === item.id ? 'repro-outline__item repro-outline__item--active' : 'repro-outline__item'} onClick={() => onSelect(item.id)}>
                <span>{item.label}</span><ArrowRightIcon size={12} />
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div className="repro-outline__summary">
        <span className="eyebrow">REPRODUCTION SUMMARY</span>
        <strong>基线可复现，改进方法待排查</strong>
        <p>当前结果较原论文低 0.8 个百分点，优先检查预处理与学习率调度。</p>
      </div>
    </section>
  );
}

function Editor({ mode, onModeChange, focused = false }: { mode: EditorMode; onModeChange: (mode: EditorMode) => void; focused?: boolean }) {
  const [draft, setDraft] = useState(MARKDOWN);
  return (
    <section className={`repro-editor${focused ? ' repro-editor--focused' : ''}`} aria-labelledby="repro-editor-title">
      <div className="repro-editor__bar">
        <div>
          <span className="eyebrow">DOCUMENT · REVISION 4</span>
          <h2 id="repro-editor-title">复现笔记.md</h2>
        </div>
        <div className="repro-editor__actions">
          <span className="save-state"><span className="save-state__dot" aria-hidden="true" />已保存</span>
          <div className="repro-segment" role="group" aria-label="文档模式">
            {([['edit', '编辑'], ['preview', '预览'], ['split', '分屏']] as const).map(([value, label]) => (
              <button key={value} type="button" className={mode === value ? 'repro-segment__item repro-segment__item--active' : 'repro-segment__item'} onClick={() => onModeChange(value)}>{label}</button>
            ))}
          </div>
        </div>
      </div>
      <div className={`repro-editor__body repro-editor__body--${mode}`}>
        {(mode === 'edit' || mode === 'split') && (
          <textarea aria-label="复现 Markdown 正文" value={draft} onChange={(event) => setDraft(event.target.value)} />
        )}
        {(mode === 'preview' || mode === 'split') && (
          <div className="repro-editor__preview"><MarkdownView source={draft} /></div>
        )}
      </div>
      <footer className="repro-editor__footer">
        <span>Markdown · KaTeX · 自动保存开启</span>
        <span>⌘S 保存 · 1,248 字</span>
      </footer>
    </section>
  );
}

function ArtifactRow({ icon, title, meta }: { icon: ReactNode; title: string; meta: string }) {
  return <li className="artifact-row"><span className="artifact-row__icon" aria-hidden="true">{icon}</span><span className="artifact-row__copy"><strong>{title}</strong><small>{meta}</small></span><ArrowRightIcon size={13} /></li>;
}

function RunsPanel({ compact = false }: { compact?: boolean }) {
  return (
    <section className={`repro-panel repro-runs${compact ? ' repro-runs--compact' : ''}`} aria-labelledby="repro-runs-title">
      <div className="repro-panel__heading">
        <div><span className="eyebrow">EXPERIMENTS</span><h2 id="repro-runs-title">实验运行</h2></div>
        <button className="btn btn--primary btn--sm" type="button"><SparkIcon size={14} /> 记录运行</button>
      </div>
      <div className="repro-runs__summary"><strong>2 / 3</strong><span>运行已记录</span><span className="repro-runs__metric">最佳 Top-1 <b>74.2%</b></span></div>
      <ol className="run-list">
        {RUNS.map((run) => (
          <li className="run-row" key={run.name}>
            <span className={`run-row__status run-row__status--${run.status}`} aria-hidden="true" />
            <span className="run-row__copy"><strong>{run.name}</strong><small>{run.detail}</small></span>
            <span className="run-row__result"><b>{run.status === 'running' ? '运行中' : run.status === 'done' ? '已完成' : '失败'}</b><span>{run.metric}</span><small>{run.duration}</small></span>
          </li>
        ))}
      </ol>
      <button className="btn btn--ghost btn--sm repro-panel__full-link" type="button">查看全部运行 <ArrowRightIcon size={13} /></button>
    </section>
  );
}

function VariantA() {
  const [selected, setSelected] = useState('environment');
  const [mode, setMode] = useState<EditorMode>('split');
  return (
    <div className="repro-workspace repro-workspace--a">
      <aside className="repro-rail"><SectionTree selected={selected} onSelect={setSelected} /></aside>
      <Editor mode={mode} onModeChange={setMode} />
      <aside className="repro-inspector"><Outline selected={selected} onSelect={setSelected} /><RunsPanel compact /></aside>
    </div>
  );
}

function VariantB() {
  const [mode, setMode] = useState<EditorMode>('edit');
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [selected, setSelected] = useState('environment');
  return (
    <div className="repro-workspace repro-workspace--b">
      <aside className="repro-project-rail" aria-label="项目切换">
        <button type="button" className="repro-project-rail__toggle" aria-label="展开项目栏"><BooksIcon size={16} /></button>
        <span className="repro-project-rail__current">V</span>
        <div className="repro-project-rail__dots"><span /><span /><span /></div>
      </aside>
      <div className="repro-focus">
        <div className="repro-focus__crumb"><span>我的复现项目</span><ArrowRightIcon size={12} /><strong>ViT 分阶段训练复现</strong><ProjectBadge status="准备中" /></div>
        <Editor mode={mode} onModeChange={setMode} focused />
        <div className="repro-focus__footer"><span><CycleIcon size={14} /> 上次保存今天 09:42</span><button className="btn btn--ghost btn--sm" type="button" onClick={() => setInspectorOpen((open) => !open)}>{inspectorOpen ? '隐藏检查器' : '打开检查器'} <ArrowRightIcon size={13} /></button></div>
      </div>
      {inspectorOpen && <aside className="repro-drawer"><div className="repro-drawer__head"><div><span className="eyebrow">INSPECTOR</span><h2>项目检查器</h2></div><button type="button" className="icon-button" aria-label="关闭检查器" onClick={() => setInspectorOpen(false)}>×</button></div><div className="repro-drawer__section"><span className="eyebrow">STATUS</span><ProjectBadge status="准备中" /><p>先完成环境与依赖记录，再开始下一轮实验。</p></div><div className="repro-drawer__section"><span className="eyebrow">CHAPTERS</span><div className="repro-drawer__chapters">{SECTIONS.slice(0, 5).map((section) => <button type="button" key={section.id} className={selected === section.id ? 'active' : ''} onClick={() => setSelected(section.id)}>{section.label}<span>{section.state === 'done' ? '✓' : '·'}</span></button>)}</div></div><RunsPanel compact /></aside>}
    </div>
  );
}

function VariantC() {
  const [selectedProject, setSelectedProject] = useState(0);
  return (
    <div className="repro-workspace repro-workspace--c">
      <aside className="repro-project-list" aria-labelledby="repro-project-list-title">
        <div className="repro-project-list__head"><div><span className="eyebrow">REPRODUCTIONS</span><h2 id="repro-project-list-title">复现项目</h2></div><button className="icon-button" type="button" aria-label="搜索项目"><SearchIcon size={15} /></button></div>
        <button type="button" className="btn btn--primary repro-project-list__new"><SparkIcon size={14} /> 新建复现</button>
        <div className="repro-project-list__filter"><span>全部项目</span><span>3</span></div>
        <ul>{PROJECTS.map((project, index) => <li key={project.title}><button type="button" className={index === selectedProject ? 'project-card project-card--active' : 'project-card'} onClick={() => setSelectedProject(index)}><span className="project-card__glyph">{index === 0 ? 'V' : index === 1 ? 'D' : 'L'}</span><span className="project-card__copy"><strong>{project.title}</strong><small>{project.paper}</small><span><ProjectBadge status={project.status} /><em>{project.updated}</em></span></span></button></li>)}</ul>
      </aside>
      <main className="repro-dashboard">
        <div className="repro-dashboard__head"><div><span className="eyebrow">PROJECT · 01</span><h2>ViT 分阶段训练复现</h2><p>关联论文：An Image is Worth 16x16 Words</p></div><div className="repro-dashboard__actions"><ProjectBadge status="准备中" /><button className="btn btn--primary" type="button"><SparkIcon size={14} /> 记录实验运行</button></div></div>
        <div className="repro-kpis"><div><span className="eyebrow">PROGRESS</span><strong>62%</strong><small>5 / 8 章节完成</small></div><div><span className="eyebrow">BEST RESULT</span><strong>74.2%</strong><small>Top-1 · 基线运行 #02</small></div><div><span className="eyebrow">LAST ACTIVE</span><strong>09:42</strong><small>今天 · 自动保存</small></div></div>
        <div className="repro-dashboard__grid"><RunsPanel /><section className="repro-panel repro-artifacts" aria-labelledby="repro-artifacts-title"><div className="repro-panel__heading"><div><span className="eyebrow">ARTIFACTS</span><h2 id="repro-artifacts-title">附件与结果</h2></div><button className="btn btn--ghost btn--sm" type="button">上传附件</button></div><ul><ArtifactRow icon={<DocumentIcon size={15} />} title="baseline-02.log" meta="运行日志 · 2.4 MB · 今天" /><ArtifactRow icon={<DocumentIcon size={15} />} title="results.csv" meta="指标表格 · 18 KB · 今天" /><ArtifactRow icon={<BooksIcon size={15} />} title="confusion-matrix.png" meta="结果图片 · 248 KB · 昨天" /></ul></section></div>
        <section className="repro-panel repro-dashboard__document"><div className="repro-panel__heading"><div><span className="eyebrow">DOCUMENT</span><h2>复现笔记</h2></div><button className="btn btn--ghost btn--sm" type="button">打开编辑器 <ArrowRightIcon size={13} /></button></div><p>记录复现目标、实验配置、结果对照和偏差分析。当前文档已完成 5 个章节，下一步聚焦学习率调度。</p><div className="document-progress"><span className="document-progress__fill" /><b>62%</b></div></section>
      </main>
    </div>
  );
}

function PrototypeSwitcher({ variant, onVariantChange }: { variant: PrototypeVariant; onVariantChange: (variant: PrototypeVariant) => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const current = VARIANTS.findIndex((item) => item.id === variant);
      const delta = event.key === 'ArrowRight' ? 1 : -1;
      onVariantChange(VARIANTS[(current + delta + VARIANTS.length) % VARIANTS.length].id);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onVariantChange, variant]);

  const switchVariant = (next: PrototypeVariant) => {
    const url = new URL(window.location.href);
    url.searchParams.set('repro-prototype', next);
    window.history.replaceState({}, '', url);
    onVariantChange(next);
  };
  const index = VARIANTS.findIndex((item) => item.id === variant);
  return (
    <div className="prototype-switcher" aria-label="原型方案切换器">
      <button type="button" aria-label="上一个方案" onClick={() => switchVariant(VARIANTS[(index - 1 + VARIANTS.length) % VARIANTS.length].id)}><ArrowRightIcon className="prototype-switcher__prev" size={14} /></button>
      <span><strong>{VARIANTS[index].label}</strong><small>{VARIANTS[index].hint}</small></span>
      <button type="button" aria-label="下一个方案" onClick={() => switchVariant(VARIANTS[(index + 1) % VARIANTS.length].id)}><ArrowRightIcon size={14} /></button>
    </div>
  );
}

export function ReproductionPrototype({ initialVariant }: PrototypeProps) {
  const [variant, setVariant] = useState<PrototypeVariant>(initialVariant);
  const content = useMemo(() => {
    if (variant === 'a') return <VariantA />;
    if (variant === 'b') return <VariantB />;
    return <VariantC />;
  }, [variant]);
  return (
    <div className="app prototype-app">
      <PrototypeNav />
      <div className="main prototype-main">
        <PrototypeHeader variant={variant} />
        <main id="prototype-page-root">{content}</main>
      </div>
      <PrototypeSwitcher variant={variant} onVariantChange={setVariant} />
    </div>
  );
}
