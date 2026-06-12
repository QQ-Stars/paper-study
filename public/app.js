// ====== 状态 ======
let PAPERS = [];
let current = null;
let curNoteText = '';
let curHasExplainer = false;
let curHasTranslation = false;
let simCands = [];           // 当前论文的「相似论文」候选
let simSeedId = null;        // 这批相似论文对应的种子论文 id
let semActive = false;       // 语义检索开关
let semRank = null;          // Map(paper_id → 相似度分)，null=未检索
let semBusy = false;
let yearFilter = 'all';
let favOnly = false;
let q = '';
let currentView = 'home';
let homeSort = { key: 'year', dir: 1 };
let manageSrc = 'all';
let chProgress = null, chDir = null, chVenue = null;
let chTrend = null, chCite = null;     // 洞察：趋势 / 引用图

const $ = (s) => document.querySelector(s);
const md = (t) => (window.marked ? window.marked.parse(t || '') :
  '<pre>' + (t || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) + '</pre>');
const esc = (s) => (s == null ? '' : String(s)).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const normTitle = (s) => (s || '').toLowerCase().replace(/[^a-z0-9一-龥]+/g, '');  // 同 db.title_norm
// 渲染 markdown 到元素，并用 KaTeX 把 $...$ / $$...$$ 公式排版出来（讲解/译文/笔记共用）
function renderMd(el, text) {
  el.innerHTML = md(text);
  if (window.renderMathInElement) {
    try {
      renderMathInElement(el, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '\\[', right: '\\]', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\(', right: '\\)', display: false }
        ],
        throwOnError: false, ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
      });
    } catch (e) {}
  }
}
const EMPTY_HTML = '<div id="viewerEmpty" class="empty"><div class="empty-ico">📄</div><div class="empty-title">从左侧选择一篇论文</div><div class="empty-sub">建议从 ① HallusionBench 开始</div></div>';

// 会议名归一化：统一大小写/常见别名，避免 NeurIPS 与 NEURIPS、arXiv 与 arXiv.org 被当成两个会议
const VENUE_CANON = { neurips: 'NeurIPS', nips: 'NeurIPS', cvpr: 'CVPR', iccv: 'ICCV', eccv: 'ECCV', wacv: 'WACV', icml: 'ICML', iclr: 'ICLR', aaai: 'AAAI', ijcai: 'IJCAI', acl: 'ACL', emnlp: 'EMNLP', naacl: 'NAACL', coling: 'COLING', tmlr: 'TMLR', tpami: 'TPAMI', corr: 'arXiv' };
function normVenue(v) {
  if (!v) return v;
  const s = String(v).trim();
  const k = s.toLowerCase();
  if (VENUE_CANON[k]) return VENUE_CANON[k];
  if (k.startsWith('arxiv')) return 'arXiv';                 // arXiv / arXiv.org / arXiv preprint…
  return s;
}
const normPapers = (arr) => { (arr || []).forEach(p => { p.venue = normVenue(p.venue) || '—'; p.type = p.type || ''; p.year = p.year || ''; p.topic = p.topic || ''; }); return arr || []; };

// ====== PDF.js ======
if (window.pdfjsLib) pdfjsLib.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.min.js';
let pdfDoc = null, baseW = 600, zoomFactor = 1, renderToken = 0, io = null;

// ====== 初始化 ======
init();
async function init() {
  PAPERS = normPapers(await (await fetch('/api/papers')).json());
  applyTheme(localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  if (localStorage.getItem('hide-left') === '1') $('#layout').classList.add('hide-left');
  if (localStorage.getItem('hide-right') === '1') $('#layout').classList.add('hide-right');
  buildYearFilters();
  renderSidebar();
  buildDashShell();
  renderHome();
  bindUI();
  initResizers();
  showView('home');
}
function applyTheme(t) { document.documentElement.setAttribute('data-theme', t); const b = $('#themeBtn'); if (b) b.textContent = t === 'dark' ? '☀️' : '🌙'; localStorage.setItem('theme', t); }
function toggleTheme() { applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); renderHome(); if (currentView === 'insights') renderInsights(); }
function togglePane(cls) { const L = $('#layout'); L.classList.toggle(cls); localStorage.setItem(cls, L.classList.contains(cls) ? '1' : '0'); if (pdfDoc && currentView === 'read') setTimeout(() => layoutPages(++renderToken), 240); }
const MIN_VIEWER = 320; // 中间 PDF 区最小宽度，任何时候都保留
function initResizers() {
  const layout = $('#layout');
  const apply = (k, v) => document.documentElement.style.setProperty(k, v + 'px');
  // 载入时校验持久化宽度：单边夹值 + 保证两侧之和给中间 PDF 留 ≥MIN_VIEWER，否则整体复位默认（修复历史异常拖拽值把布局挤坏的问题）
  let lw = parseInt(localStorage.getItem('left-w'), 10) || 0;
  let rw = parseInt(localStorage.getItem('right-w'), 10) || 0;
  if (lw) lw = Math.max(160, lw);
  if (rw) rw = Math.max(220, rw);
  if ((lw || 300) + (rw || 420) > window.innerWidth - MIN_VIEWER) {
    localStorage.removeItem('left-w'); localStorage.removeItem('right-w');
    document.documentElement.style.removeProperty('--left-w'); document.documentElement.style.removeProperty('--right-w');
  } else {
    if (lw) { apply('--left-w', lw); localStorage.setItem('left-w', lw); }
    if (rw) { apply('--right-w', rw); localStorage.setItem('right-w', rw); }
  }
  document.querySelectorAll('.gutter').forEach(g => {
    const side = g.dataset.side;
    const key = side === 'left' ? 'left-w' : 'right-w';
    const cssVar = side === 'left' ? '--left-w' : '--right-w';
    g.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      const rect = layout.getBoundingClientRect();
      const otherEl = side === 'left' ? $('#panel') : $('#sidebar');
      const otherW = otherEl ? otherEl.getBoundingClientRect().width : 0;
      const maxW = Math.max(200, Math.round(rect.width - otherW - MIN_VIEWER)); // 保证中间 PDF 不被挤没
      g.classList.add('dragging'); layout.classList.add('resizing');
      const move = (ev) => {
        const raw = side === 'left' ? (ev.clientX - rect.left) : (rect.right - ev.clientX);
        const w = Math.max(side === 'left' ? 160 : 220, Math.min(Math.round(raw), maxW));
        apply(cssVar, w); localStorage.setItem(key, w);
      };
      const up = () => {
        g.classList.remove('dragging'); layout.classList.remove('resizing');
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
        if (pdfDoc && currentView === 'read') layoutPages(++renderToken);
      };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    });
    g.addEventListener('dblclick', () => { // 双击复位默认宽度
      document.documentElement.style.removeProperty(cssVar);
      localStorage.removeItem(key);
      if (pdfDoc && currentView === 'read') layoutPages(++renderToken);
    });
  });
}

function buildYearFilters() {
  const box = $('#yearFilters'); box.innerHTML = '';
  // 动态从库内论文取年份；只保留合法四位年份（丢掉空/异常），升序
  const years = [...new Set(PAPERS.map(p => p.year))].filter(y => /^\d{4}$/.test(y)).sort();
  if (years.length <= 6) {
    // 年份不多：chips（保持原样，且保留当前选中）
    ['all', ...years].forEach(y => {
      const b = document.createElement('button');
      b.className = 'chip-btn' + (yearFilter === y ? ' active' : '');
      b.textContent = y === 'all' ? '全部' : y;
      b.onclick = () => { yearFilter = y; [...box.children].forEach(c => c.classList.remove('active')); b.classList.add('active'); refresh(); };
      box.appendChild(b);
    });
  } else {
    // 年份过多：折叠成下拉，永不超出导航栏（全部 + 年份新→旧）
    const sel = document.createElement('select');
    sel.className = 'year-select';
    sel.innerHTML = ['all', ...years.slice().reverse()]
      .map(y => `<option value="${y}" ${yearFilter === y ? 'selected' : ''}>${y === 'all' ? '全部年份' : y + ' 年'}</option>`).join('');
    sel.onchange = () => { yearFilter = sel.value; refresh(); };
    box.appendChild(sel);
  }
}

function updateSummary() {
  const done = PAPERS.filter(p => p.status === '已理解').length;
  const ing = PAPERS.filter(p => p.status === '学习中').length;
  $('#progressSummary').textContent = `已理解 ${done} · 学习中 ${ing} · 共 ${PAPERS.length}`;
  $('#progressBar').style.width = (PAPERS.length ? Math.round(done / PAPERS.length * 100) : 0) + '%';
}
function refresh() { renderSidebar(); renderHome(); }

// ====== 视图切换 ======
function showView(v) {
  currentView = v;
  document.querySelectorAll('.viewnav button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $('#home').classList.toggle('hidden', v !== 'home');
  $('#manage').classList.toggle('hidden', v !== 'manage');
  $('#insights').classList.toggle('hidden', v !== 'insights');
  $('#layout').classList.toggle('hidden', v !== 'read');
  if (v === 'home') renderHome();
  if (v === 'manage') renderManage();
  if (v === 'insights') renderInsights();
  if (v === 'read' && !current) { $('#pdfScroll').innerHTML = EMPTY_HTML; }
}
function fmtTime(s) {
  if (!s) return '—';
  const d = new Date(String(s).replace(' ', 'T') + 'Z');
  return isNaN(d) ? s : d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// ====== 总览 Dashboard ======
function buildDashShell() {
  $('#dash').innerHTML = `
    <div class="chart-card kpi">
      <div class="chart-title">概况</div>
      <div class="kpi-big" id="kpiTotal">0</div>
      <div class="kpi-sub" id="kpiSub">篇论文</div>
      <div class="kpi-rows">
        <div><span>顶会发表</span><b id="kpiPub">0</b></div>
        <div><span>arXiv 预印</span><b id="kpiArxiv">0</b></div>
        <div><span>★ 收藏</span><b id="kpiFav">0</b></div>
        <div><span>2024 / 25 / 26</span><b id="kpiYears">0 / 0 / 0</b></div>
      </div>
    </div>
    <div class="chart-card"><div class="chart-title">学习进度</div><div id="chartProgress" class="echart"></div></div>
    <div class="chart-card"><div class="chart-title">研究方向分布</div><div id="chartDir" class="echart"></div></div>
    <div class="chart-card"><div class="chart-title">会议分布</div><div id="chartVenue" class="echart"></div></div>`;
  if (window.echarts) {
    chProgress = echarts.init($('#chartProgress'));
    chDir = echarts.init($('#chartDir'));
    chVenue = echarts.init($('#chartVenue'));
  }
}
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function renderHome() {
  let list = PAPERS.filter(p => (yearFilter === 'all' || p.year === yearFilter) && (!favOnly || p.favorite));
  const sem = semActive && semRank;
  if (sem) list = list.filter(p => semRank.has(p.id));
  else if (q) { const k = q.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + p.type + ' ' + (p.topic || '')).toLowerCase().includes(k)); }

  // 表格（语义检索时按相似度排序，否则按列排序）
  if (sem) list.sort((a, b) => semRank.get(b.id) - semRank.get(a.id));
  else list.sort(cmpHome);
  const emptyMsg = sem ? '语义检索没有命中（试试换种说法）。' : (favOnly ? '还没有收藏的论文。在阅读界面点「☆ 收藏」即可。' : '没有匹配的论文。');
  $('#homeBody').innerHTML = list.map((p, i) => rowHTML(p, i + 1)).join('') || `<tr><td colspan="9" class="empty-row">${emptyMsg}</td></tr>`;
  document.querySelectorAll('#homeBody tr[data-id]').forEach(tr => tr.onclick = () => openPaper(PAPERS.find(x => x.id === tr.dataset.id)));
  document.querySelectorAll('#homeBody .fav-star').forEach(s => s.onclick = (e) => { e.stopPropagation(); toggleFavorite(s.dataset.id); });
  document.querySelectorAll('#homeTable th[data-sort]').forEach(th => {
    const base = th.dataset.label || th.textContent.replace(/[▲▼]/g, '').trim();
    th.dataset.label = base;
    th.innerHTML = base + (homeSort.key === th.dataset.sort ? ` <span class="arrow">${homeSort.dir > 0 ? '▲' : '▼'}</span>` : '');
  });

  // 看板（KPI + ECharts 图表）
  if (document.getElementById('kpiTotal')) {
    const total = list.length;
    const done = list.filter(p => p.status === '已理解').length;
    const ing = list.filter(p => p.status === '学习中').length;
    const idle = total - done - ing;
    const pub = list.filter(p => p.venue !== 'arXiv').length;
    const yc = (yr) => list.filter(p => p.year === yr).length;
    const pct = total ? Math.round(done / total * 100) : 0;
    $('#kpiTotal').textContent = total;
    $('#kpiSub').textContent = '篇论文 ' + (favOnly ? '· ★收藏' : (yearFilter === 'all' ? '· 全部' : '· ' + yearFilter));
    $('#kpiPub').textContent = pub;
    $('#kpiArxiv').textContent = total - pub;
    $('#kpiFav').textContent = list.filter(p => p.favorite).length;
    $('#kpiYears').textContent = `${yc('2024')} / ${yc('2025')} / ${yc('2026')}`;

    // 数据驱动：按库中实际的研究方向(type 的顶层，"缓解·解码"归"缓解")与实际会议分组，与领域无关
    const dirItems = topGroups(list, p => (p.type || '').split('·')[0], 7);
    const vItems = topGroups(list, p => p.venue, 7);

    updateCharts({ done, ing, idle, pct, dirItems, vItems });
  }
  updateSummary();
}

function updateCharts(d) {
  if (!window.echarts || !chProgress) return;
  const text = cssVar('--ink'), t2 = cssVar('--ink-2'), t3 = cssVar('--ink-3');
  const surf = cssVar('--surface'), ok = cssVar('--ok'), warn = cssVar('--warn'), idle = cssVar('--idle');
  chProgress.setOption({
    animationDuration: 750, animationDurationUpdate: 600, animationEasing: 'cubicOut',
    title: {
      text: d.pct + '%', subtext: '已理解', left: '33%', top: 'center', textAlign: 'center', itemGap: 4,
      textStyle: { fontSize: 26, fontWeight: 700, color: text }, subtextStyle: { fontSize: 10.5, color: t3 }
    },
    tooltip: { trigger: 'item', formatter: '{b}：{c} 篇 ({d}%)' },
    legend: {
      orient: 'vertical', right: '4%', top: 'center', itemWidth: 9, itemHeight: 9, itemGap: 13,
      icon: 'roundRect', textStyle: { color: t2, fontSize: 11.5 },
      formatter: (name) => { const m = { '已理解': d.done, '学习中': d.ing, '未开始': d.idle }; return `${name}  ${m[name] || 0}`; }
    },
    series: [{
      type: 'pie', radius: ['56%', '80%'], center: ['33%', '50%'], avoidLabelOverlap: false,
      itemStyle: { borderColor: surf, borderWidth: 2, borderRadius: 5 },
      label: { show: false }, labelLine: { show: false }, emphasis: { scale: true, scaleSize: 5 },
      data: [
        { value: d.done, name: '已理解', itemStyle: { color: ok } },
        { value: d.ing, name: '学习中', itemStyle: { color: warn } },
        { value: d.idle, name: '未开始', itemStyle: { color: idle } }
      ]
    }]
  });
  chDir.setOption(barOption(d.dirItems, t2, t3));
  chVenue.setOption(barOption(d.vItems, t2, t3));
}

// 把任意维度按出现次数分组，取前 max-1 个，其余并入「其他」（始终末位、灰色）。与具体领域无关。
// 「其他」条带 breakdown：被并进去的具体类别明细（供悬停 tooltip 展开）。
function topGroups(list, keyFn, max = 7) {
  const counts = {};
  list.forEach(p => { const k = ((keyFn(p) || '') + '').trim() || '其他'; counts[k] = (counts[k] || 0) + 1; });
  const literalOther = counts['其他'] || 0; delete counts['其他'];
  let entries = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const cap = literalOther ? max - 1 : max;
  let merged = [];
  if (entries.length > cap) { merged = entries.slice(cap - 1); entries = entries.slice(0, cap - 1); }
  const palette = ['#bd5b3a', '#3f7d5b', '#5b7387', '#b07a2e', '#8a5a5a', '#6f8f6a', '#9a8b72'];
  const out = entries.map(([name, value], i) => ({ name, value, color: palette[i % palette.length] }));
  const otherTotal = literalOther + merged.reduce((s, [, v]) => s + v, 0);
  if (otherTotal) {
    const breakdown = merged.map(([name, value]) => ({ name, value }));
    if (literalOther) breakdown.push({ name: '（未细分）', value: literalOther });
    breakdown.sort((a, b) => b.value - a.value);
    out.push({ name: '其他', value: otherTotal, color: '#9a8b72', breakdown });
  }
  return out;
}
function barOption(items, t2, t3) {
  const rail = cssVar('--surface-3');
  const byName = {}; items.forEach(it => { byName[it.name] = it; });
  const labels = items.map(i => i.name).reverse();
  const data = items.map(i => ({ value: i.value, itemStyle: { color: i.color, borderRadius: [0, 6, 6, 0] } })).reverse();
  return {
    animationDuration: 750, animationDurationUpdate: 600, animationEasing: 'cubicOut',
    grid: { left: 4, right: 30, top: 8, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true,
      formatter: (p) => {
        const name = p[0].name, val = p[0].value, it = byName[name];
        if (it && it.breakdown && it.breakdown.length) {
          const rows = it.breakdown.slice(0, 12).map(b => `· ${b.name}　${b.value} 篇`).join('<br>');
          const more = it.breakdown.length > 12 ? `<br>…等共 ${it.breakdown.length} 类` : '';
          return `「其他」共 ${val} 篇，含：<br>${rows}${more}`;
        }
        return `${name}：${val} 篇`;
      }
    },
    xAxis: { type: 'value', max: 'dataMax', axisLabel: { show: false }, splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'category', data: labels, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t2, fontSize: 12 } },
    series: [{
      type: 'bar', data, barWidth: '56%', barMinHeight: 3,
      showBackground: true, backgroundStyle: { color: rail, borderRadius: 6 },
      label: { show: true, position: 'right', color: t2, fontSize: 11, fontWeight: 600, formatter: '{c}' }
    }]
  };
}
// ====== 洞察：研究趋势 + 引用关系图 ======
function buildInsightsShell() {
  if (chTrend || !window.echarts) return;
  chTrend = echarts.init($('#chartTrend'));
  chCite = echarts.init($('#chartCite'));
  chCite.on('click', (params) => {
    if (params.dataType === 'node') { const p = PAPERS.find(x => x.id === params.data.id); if (p) openPaper(p); }
  });
}
async function renderInsights() {
  buildInsightsShell();
  renderTrend();
  try {
    const g = await (await fetch('/api/citegraph')).json();
    if (g && g.edgeCount > 0) renderCite(g); else showCitePrompt();
  } catch (e) { showCitePrompt(); }
}
function renderTrend() {
  if (!chTrend) return;
  const t2 = cssVar('--ink-2'), t3 = cssVar('--ink-3');
  const years = [...new Set(PAPERS.map(p => p.year).filter(y => /^\d{4}$/.test(y)))].sort();
  const dirItems = topGroups(PAPERS, p => (p.type || '').split('·')[0], 6);
  const topNames = new Set(dirItems.filter(d => d.name !== '其他').map(d => d.name));
  const bucket = (p) => { const k = (p.type || '').split('·')[0] || '其他'; return topNames.has(k) ? k : '其他'; };
  const series = dirItems.map(d => ({
    name: d.name, type: 'bar', stack: 'total', barWidth: '50%',
    itemStyle: { color: d.color }, emphasis: { focus: 'series' },
    data: years.map(y => PAPERS.filter(p => p.year === y && bucket(p) === d.name).length)
  }));
  chTrend.setOption({
    animationDuration: 700, animationEasing: 'cubicOut',
    legend: { top: 2, textStyle: { color: t2, fontSize: 11 }, itemWidth: 11, itemHeight: 11, itemGap: 12 },
    grid: { left: 6, right: 16, top: 40, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: years, axisTick: { show: false }, axisLine: { lineStyle: { color: cssVar('--border') } }, axisLabel: { color: t2, fontSize: 12 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: cssVar('--surface-3') } }, axisLabel: { color: t3, fontSize: 11 } },
    series
  });
}
function renderCite(g) {
  if (!chCite) return;
  chCite.clear();
  $('#citeHint').textContent = `${g.nodes.length} 篇 · ${g.edgeCount} 条引用 · 节点越大＝被库内引用越多`;
  const t2 = cssVar('--ink-2');
  // 节点按方向分桶（与看板/趋势一致，避免图例过多、配色重复）
  const dirItems = topGroups(g.nodes, n => (n.type || '').split('·')[0], 6);
  const topNames = new Set(dirItems.filter(d => d.name !== '其他').map(d => d.name));
  const bucket = (n) => { const k = (n.type || '').split('·')[0] || '其他'; return topNames.has(k) ? k : '其他'; };
  const cats = dirItems.map(d => d.name);
  const colorOf = {}; dirItems.forEach(d => { colorOf[d.name] = d.color; });
  const maxIn = Math.max(1, ...g.nodes.map(n => n.indeg));
  const data = g.nodes.map(n => ({
    id: n.id, name: n.title,
    symbolSize: 8 + (n.indeg / maxIn) * 28,
    category: cats.indexOf(bucket(n)), value: n.indeg,
    label: { show: n.indeg >= 3 }
  }));
  chCite.setOption({
    tooltip: { confine: true, formatter: (p) => p.dataType === 'node' ? `${esc(p.data.name)}<br>被库内 <b>${p.data.value}</b> 篇引用` : '' },
    legend: [{ data: cats, top: 2, textStyle: { color: t2, fontSize: 11 }, itemWidth: 11, itemHeight: 11, itemGap: 12 }],
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true,
      categories: cats.map(c => ({ name: c, itemStyle: { color: colorOf[c] } })),
      force: { repulsion: 120, edgeLength: [50, 150], gravity: 0.06, friction: 0.18 },
      data, links: g.links.map(l => ({ source: l.source, target: l.target })),
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 5,
      lineStyle: { color: 'source', opacity: 0.26, width: 1, curveness: 0.08 },
      label: { position: 'right', fontSize: 10, color: t2, formatter: (p) => p.data.name.length > 16 ? p.data.name.slice(0, 16) + '…' : p.data.name },
      emphasis: { focus: 'adjacency', lineStyle: { width: 2, opacity: 0.6 }, label: { show: true } }
    }]
  });
}
function showCitePrompt() {
  if (!chCite) return;
  chCite.clear();
  $('#citeHint').textContent = '';
  chCite.setOption({ graphic: { type: 'text', left: 'center', top: 'center', style: { text: '还没有引用图\n点上方「⟳ 构建 / 刷新引用图」（抓取参考文献，约 1~2 分钟）', fill: cssVar('--ink-3'), fontSize: 13, lineHeight: 24, textAlign: 'center' } } });
}
async function buildCite() {
  const btn = $('#citeBuildBtn'), hint = $('#citeHint');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '构建中…';
  hint.textContent = '抓取参考文献中（约 1~2 分钟，有 S2 key 更快）…';
  try {
    await streamNDJSON('/api/cite-build', {}, (ev) => {
      if (ev.type === 'progress') { const m = /^PROG::(\d+)::(\d+)/.exec(ev.line); if (m) hint.textContent = `抓取参考文献 ${m[1]} / ${m[2]}…`; }
      else if (ev.type === 'result') { hint.textContent = ev.ok ? `✅ 已建 ${ev.edges} 条引用` : ('失败：' + (ev.error || '未知')); }
    });
    const g = await (await fetch('/api/citegraph')).json();
    if (g && g.edgeCount > 0) renderCite(g); else showCitePrompt();
  } catch (e) { hint.textContent = '失败：' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}

function cmpHome(a, b) {
  const k = homeSort.key, d = homeSort.dir;
  let va, vb;
  if (k === 'status') { const m = { '未开始': 0, '学习中': 1, '已理解': 2 }; va = m[a.status]; vb = m[b.status]; }
  else { va = (a[k] || '') + ''; vb = (b[k] || '') + ''; }
  if (va < vb) return -d; if (va > vb) return d;
  return (a.year + '').localeCompare(b.year + '') || ((a.order || 99) - (b.order || 99));
}
function semScoreBadge(id) {
  if (!(semActive && semRank && semRank.has(id))) return '';
  const pct = Math.max(0, Math.min(100, Math.round(semRank.get(id) * 100)));
  return `<span class="sem-score" title="语义相关度 ${pct}%">${pct}</span>`;
}
function rowHTML(p, idx) {
  return `<tr data-id="${p.id}">
    <td><span class="ht-idx">${idx}</span></td>
    <td class="ht-title">${semScoreBadge(p.id)}<span class="fav-star ${p.favorite ? 'on' : ''}" data-id="${p.id}" title="${p.favorite ? '取消收藏' : '收藏'}">${p.favorite ? '★' : '☆'}</span>${p.title}</td>
    <td><span class="venue v-${p.venue}">${p.venue}</span></td>
    <td>${p.year}</td>
    <td>${p.type}</td>
    <td>${p.topic || ''}</td>
    <td class="ht-time">${fmtTime(p.created_at)}</td>
    <td><span class="ht-status ${p.status}">${p.status}</span></td>
    <td class="ht-note">${p.hasNote ? '✍️' : '<span style="color:var(--ink-3)">·</span>'}</td>
  </tr>`;
}

// ====== 阅读视图：左侧列表 ======
function renderSidebar() {
  const side = $('#sidebar'); side.innerHTML = '';
  let list = PAPERS.filter(p => (yearFilter === 'all' || p.year === yearFilter) && (!favOnly || p.favorite));
  const sem = semActive && semRank;
  if (sem) list = list.filter(p => semRank.has(p.id));
  else if (q) { const k = q.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + (p.topic || '') + ' ' + (p.type || '')).toLowerCase().includes(k)); }
  if (sem) {
    // 语义检索：扁平单组，按相似度降序
    list.sort((a, b) => semRank.get(b.id) - semRank.get(a.id));
    const g = document.createElement('div'); g.className = 'year-group';
    const h = document.createElement('div'); h.className = 'year-head';
    h.textContent = '🔮 语义结果 · ' + list.length + ' 篇';
    g.appendChild(h);
    list.forEach(p => g.appendChild(paperItem(p)));
    side.appendChild(g);
  } else {
    const years = [...new Set(list.map(p => p.year))].sort();
    years.forEach(y => {
      const g = document.createElement('div'); g.className = 'year-group';
      const h = document.createElement('div'); h.className = 'year-head';
      h.textContent = y + ' · ' + list.filter(p => p.year === y).length + ' 篇';
      g.appendChild(h);
      list.filter(p => p.year === y)
        .sort((a, b) => (a.order || 99) - (b.order || 99) || (a.venue || '').localeCompare(b.venue || ''))
        .forEach(p => g.appendChild(paperItem(p)));
      side.appendChild(g);
    });
  }
  updateSummary();
}
function paperItem(p) {
  const d = document.createElement('div');
  d.className = 'paper-item' + (current && current.id === p.id ? ' active' : '');
  d.onclick = () => openPaper(p);
  const order = p.order ? `<span class="order-badge">${p.order}</span>` : '';
  d.innerHTML =
    `<div class="pi-top">${order}<div class="pi-title">${p.title}</div><span class="fav-star ${p.favorite ? 'on' : ''}" title="${p.favorite ? '取消收藏' : '收藏'}">${p.favorite ? '★' : '☆'}</span><span class="status-dot ${p.status}" title="${p.status}"></span></div>
     <div class="pi-meta"><span class="venue v-${p.venue}">${p.venue} ${p.year}</span><span class="dir">${p.type}${p.topic ? ' · ' + p.topic : ''}</span>${semScoreBadge(p.id)}</div>`;
  d.querySelector('.fav-star').onclick = (e) => { e.stopPropagation(); toggleFavorite(p.id); };
  return d;
}

// ====== 打开论文 ======
async function openPaper(p) {
  if (!p) return;
  current = p;
  showView('read');
  renderSidebar();
  $('#paperTitle').textContent = `${p.title} — ${p.venue} ${p.year}`;
  $('#pdfDocTitle').textContent = `${p.title} · ${p.venue} ${p.year}`;
  $('#pdfOpen').href = p.pdf_url || ('/papers/' + encodeURIComponent(p.file));
  setStatusUI(p.status || '未开始');
  setFavoriteUI(p);
  // 讲解
  const ex = await (await fetch('/api/explainer?id=' + encodeURIComponent(p.id))).text();
  setExplainer(ex);
  // 译文
  const tr = await (await fetch('/api/translation?id=' + encodeURIComponent(p.id))).text();
  setTranslation(tr);
  // 笔记
  curNoteText = await (await fetch('/api/note?id=' + encodeURIComponent(p.id))).text();
  $('#noteEdit').value = curNoteText;
  if (curNoteText.trim()) renderMd($('#notePreview'), curNoteText);
  else $('#notePreview').innerHTML = '<div class="placeholder">还没有笔记。点「编辑」开始记，或在对话里让我「记录」。</div>';
  showNoteMode('preview');
  // 相似论文（按需查找，切论文时清空）
  resetSimilar();
  // PDF
  renderPdf(p.id);
}

// ====== 论文讲解（载入 + LLM 自动生成）======
const EX_EMPTY = '*(暂无讲解)*';
function setExplainer(text) {
  const real = text && text.trim() && text.trim() !== EX_EMPTY;
  curHasExplainer = !!real;
  if (real) renderMd($('#explainerView'), text);
  else $('#explainerView').innerHTML = '<div class="placeholder">这篇还没有讲解。点上方「✨ 生成讲解」，让大模型精读后写一份结构化讲解。</div>';
  $('#explainerView').scrollTop = 0;
  const btn = $('#genExplainerBtn');
  if (btn) { btn.disabled = false; btn.textContent = real ? '✨ 重新生成' : '✨ 生成讲解'; }
  const hint = $('#genHint'); if (hint) hint.textContent = '';
}
async function generateExplainer() {
  if (!current) { alert('请先在左侧选择一篇论文'); return; }
  if (curHasExplainer && !confirm('已有讲解，重新生成会覆盖当前内容（手写讲解也会被替换）。确定继续？')) return;
  const btn = $('#genExplainerBtn'), view = $('#explainerView'), hint = $('#genHint');
  const deep = $('#genDeep').checked, pid = current.id;
  btn.disabled = true; const old = btn.textContent; btn.textContent = '生成中…';
  hint.textContent = deep ? '通读 PDF 全文，约 30~90 秒…' : '约 10~40 秒…';
  view.innerHTML = '<div class="ex-progress"><span class="ex-spinner"></span><span class="ex-log" id="exLog">正在准备…</span></div>';
  const STAGE = { load: '读取论文信息', pdf: '读取 PDF 全文', generate: '大模型撰写讲解中' };
  const setLog = (t) => { const el = document.getElementById('exLog'); if (el) el.textContent = t; };
  const fail = (msg) => { view.innerHTML = '<div class="placeholder">生成失败：' + msg + '<br>可在顶栏 ⚙ 检查模型与密钥后重试。</div>'; btn.disabled = false; btn.textContent = old; hint.textContent = ''; };
  try {
    await streamNDJSON('/api/explain', { id: pid, deep }, (ev) => {
      if (ev.type === 'progress') {
        const m = /^STAGE::(\w+)/.exec(ev.line);
        if (m && STAGE[m[1]]) setLog(STAGE[m[1]] + '…');
        else if (ev.line.startsWith('PDFMISS::')) setLog('未找到本地 PDF，改用摘要生成…');
        else if (ev.line.startsWith('PDFERR::')) setLog('PDF 读取失败，改用摘要…');
      } else if (ev.type === 'result') {
        if (!current || current.id !== pid) return;       // 用户已切换论文，丢弃
        if (ev.ok && ev.markdown && ev.markdown.trim()) {
          setExplainer(ev.markdown);
          hint.textContent = '✅ 已生成并保存';
          setTimeout(() => { const h = $('#genHint'); if (h && h.textContent.startsWith('✅')) h.textContent = ''; }, 4000);
        } else { fail(ev.error || '模型返回为空'); }
      }
    });
  } catch (e) { if (current && current.id === pid) fail(String(e)); }
}

// ====== 全文翻译（载入 + LLM 分段翻译）======
function setTranslation(text) {
  const real = text && text.trim();
  curHasTranslation = !!real;
  if (real) renderMd($('#transView'), text);
  else $('#transView').innerHTML = '<div class="placeholder">选择论文后，点「🌐 翻译全文」生成中文翻译——读取 PDF 全文、自动跳过参考文献，分段翻译（较慢，约 1~3 分钟）。</div>';
  $('#transView').scrollTop = 0;
  const btn = $('#genTransBtn');
  if (btn) { btn.disabled = false; btn.textContent = real ? '🌐 重新翻译' : '🌐 翻译全文'; }
  const h = $('#transHint'); if (h) h.textContent = '';
}
async function generateTranslation() {
  if (!current) { alert('请先在左侧选择一篇论文'); return; }
  if (curHasTranslation && !confirm('已有翻译，重新翻译会覆盖当前内容。确定继续？')) return;
  const btn = $('#genTransBtn'), view = $('#transView'), hint = $('#transHint'), pid = current.id;
  btn.disabled = true; const old = btn.textContent; btn.textContent = '翻译中…';
  hint.textContent = '全文翻译较慢，请耐心等待…';
  view.innerHTML = '<div class="ex-progress"><span class="ex-spinner"></span><span class="ex-log" id="transLog">正在准备…</span></div>';
  const setLog = (t) => { const el = document.getElementById('transLog'); if (el) el.textContent = t; };
  const fail = (msg) => { view.innerHTML = '<div class="placeholder">翻译失败：' + msg + '<br>需要本篇有本地 PDF；可在顶栏 ⚙ 检查模型与密钥后重试。</div>'; btn.disabled = false; btn.textContent = old; hint.textContent = ''; };
  try {
    await streamNDJSON('/api/translate', { id: pid }, (ev) => {
      if (ev.type === 'progress') {
        const ln = ev.line;
        if (ln.startsWith('STAGE::pdf')) setLog('读取 PDF 全文…');
        else if (ln.startsWith('STRIP::')) setLog('已跳过参考文献，准备分段…');
        else if (ln.startsWith('PDFMISS::')) setLog('无本地 PDF，改用摘要翻译…');
        else { const t = /^TOTAL::(\d+)/.exec(ln); if (t) setLog(`共 ${t[1]} 段，翻译中…`); const c = /^CHUNK::(\d+)::(\d+)/.exec(ln); if (c) { setLog(`翻译中… ${c[1]} / ${c[2]} 段`); hint.textContent = `${Math.round(c[1] / c[2] * 100)}%`; } }
      } else if (ev.type === 'result') {
        if (!current || current.id !== pid) return;       // 已切换论文，丢弃
        if (ev.ok && ev.markdown && ev.markdown.trim()) {
          setTranslation(ev.markdown);
          hint.textContent = '✅ 翻译完成并保存';
          setTimeout(() => { const h = $('#transHint'); if (h && h.textContent.startsWith('✅')) h.textContent = ''; }, 4000);
        } else { fail(ev.error || '返回为空'); }
      }
    });
  } catch (e) { if (current && current.id === pid) fail(String(e)); }
}

// ====== 相似论文推荐（S2 Recommendations）======
function resetSimilar() {
  simCands = []; simSeedId = null;
  const v = $('#simView'); if (v) v.innerHTML = '<div class="placeholder">点上方「🔗 找相似论文」，按 Semantic Scholar 的内容相似度，找出与这篇最相近的一批论文——可一键收录入库。</div>';
  const btn = $('#findSimBtn'); if (btn) { btn.disabled = false; btn.textContent = '🔗 找相似论文'; }
  const h = $('#simHint'); if (h) h.textContent = '';
}
async function findSimilar() {
  if (!current) { alert('请先在左侧选择一篇论文'); return; }
  const btn = $('#findSimBtn'), view = $('#simView'), hint = $('#simHint'), pid = current.id;
  btn.disabled = true; btn.textContent = '查找中…'; hint.textContent = '正在向 Semantic Scholar 查询…';
  view.innerHTML = '<div class="ex-progress"><span class="ex-spinner"></span><span class="ex-log" id="simLog">正在准备…</span></div>';
  const setLog = (t) => { const el = document.getElementById('simLog'); if (el) el.textContent = t; };
  try {
    await streamNDJSON('/api/recommend', { id: pid, limit: 16 }, (ev) => {
      if (ev.type === 'progress') {
        const ln = ev.line;
        if (ln.startsWith('STAGE::resolve')) setLog('定位这篇论文…');
        else if (ln.startsWith('STAGE::recommend')) setLog('查询相似论文…');
        else { const f = /^FOUND::(\d+)/.exec(ln); if (f) setLog(`找到 ${f[1]} 篇，整理中…`); }
      } else if (ev.type === 'result') {
        if (!current || current.id !== pid) return;          // 用户已切换论文，丢弃
        hint.textContent = '';
        if (ev.ok && (ev.candidates || []).length) {
          simCands = ev.candidates; simSeedId = pid; renderSimList();
          btn.disabled = false; btn.textContent = '🔗 重新查找';
        } else {
          btn.disabled = false; btn.textContent = '🔗 重新查找';
          const msg = !ev.ok
            ? (ev.error === 'no_s2_id' ? '这篇在 Semantic Scholar 上没有匹配记录（多为预印本或手动添加）。' : ('查询失败：' + (ev.error || '未知')))
            : 'Semantic Scholar 暂时没有给出相似论文（可能这篇太新或尚未被收录）。';
          view.innerHTML = '<div class="placeholder">' + esc(msg) + '</div>';
        }
      }
    });
  } catch (e) {
    if (current && current.id === pid) { view.innerHTML = '<div class="placeholder">查询失败：' + esc(String(e)) + '</div>'; btn.disabled = false; btn.textContent = '🔗 找相似论文'; hint.textContent = ''; }
  }
}
function renderSimList() {
  const view = $('#simView');
  const fresh = simCands.filter(c => !c.in_library).length;
  const head = `<div class="sim-head">为本篇找到 <b>${simCands.length}</b> 篇相似论文 · ${fresh} 篇不在库 <span class="sim-src">来源 Semantic Scholar</span></div>`;
  view.innerHTML = head + simCands.map((c, i) => {
    const cite = c.citations != null ? `${c.citations} 引` : '';
    const snip = c.tldr || c.abstract || '';
    const tl = snip ? `<div class="sim-tldr">${esc(snip)}</div>` : '';
    const act = c.in_library
      ? `<button class="sim-btn open" data-i="${i}">在库·打开</button>`
      : `<button class="sim-btn add" data-i="${i}">+ 收录</button>`;
    const link = c.url ? `<a class="sim-ext" href="${esc(c.url)}" target="_blank" rel="noopener" title="在 Semantic Scholar 打开">↗</a>` : '';
    return `<div class="sim-item ${c.in_library ? 'in-lib' : ''}">
      <div class="sim-main">
        <div class="sim-title">${esc(c.title)}</div>
        <div class="sim-meta"><span class="venue v-${esc(c.venue || '')}">${esc(c.venue || '—')} ${esc(c.year || '')}</span>${cite ? ' · ' + cite : ''}${c.in_library ? ' · <b class="inlib-tag">已在库</b>' : ''}</div>
        ${tl}
      </div>
      <div class="sim-actions">${act}${link}</div>
    </div>`;
  }).join('');
  view.querySelectorAll('.sim-btn.add').forEach(b => b.onclick = () => addSimPaper(+b.dataset.i, b));
  view.querySelectorAll('.sim-btn.open').forEach(b => b.onclick = () => {
    const c = simCands[+b.dataset.i];
    const pp = PAPERS.find(x => (c.arxiv_id && x.arxiv_id === c.arxiv_id) || normTitle(x.title) === normTitle(c.title));
    if (pp) openPaper(pp); else alert('这篇已在库，但未能定位到列表项，试试刷新。');
  });
}
async function addSimPaper(i, btn) {
  const c = simCands[i]; if (!c) return;
  btn.disabled = true; const old = btn.textContent; btn.textContent = '收录中…';
  try {
    await streamNDJSON('/api/ingest-selected', { candidates: [c], deep: false }, (ev) => {
      if (ev.type === 'progress' && /^CLASSIFIED::/.test(ev.line)) btn.textContent = '分类中…';
    });
    c.in_library = true;
    await reloadPapers();
    if (current && simSeedId === current.id) renderSimList();   // in_library 状态刷新
  } catch (e) { btn.disabled = false; btn.textContent = old; alert('收录失败: ' + e); }
}

// ====== PDF.js 渲染（懒加载 + 缩放）======
async function renderPdf(id) {
  const token = ++renderToken;
  const scroll = $('#pdfScroll');
  scroll.innerHTML = '<div class="pdf-loading">加载 PDF 中…</div>';
  if (!window.pdfjsLib) { scroll.innerHTML = '<div class="pdf-loading">PDF.js 未加载</div>'; return; }
  try {
    const resp = await fetch('/pdfbytes?id=' + encodeURIComponent(id));
    if (resp.status !== 200) {
      throw new Error('服务器返回 ' + resp.status + (resp.status === 204
        ? '（仍被下载器/迅雷拦截：请在迅雷设置→“监视设置”里把 localhost 设为不接管，或临时禁用迅雷的浏览器扩展后刷新）' : ''));
    }
    const data = await resp.arrayBuffer();
    if (token !== renderToken) return;
    const doc = await pdfjsLib.getDocument({ data }).promise;
    if (token !== renderToken) return;
    pdfDoc = doc;
    baseW = (await doc.getPage(1)).getViewport({ scale: 1 }).width;
    await layoutPages(token);
  } catch (e) {
    scroll.innerHTML = '<div class="pdf-loading">PDF 加载失败：' + e.message + '</div>';
  }
}
function curScale() {
  const avail = $('#pdfScroll').clientWidth - 36;
  return Math.max(0.3, (avail / baseW) * zoomFactor);
}
async function layoutPages(token) {
  const scroll = $('#pdfScroll'); scroll.innerHTML = '';
  if (io) io.disconnect();
  io = new IntersectionObserver((ents) => ents.forEach(e => { if (e.isIntersecting) { renderPage(e.target); io.unobserve(e.target); } }),
    { root: scroll, rootMargin: '800px 0px' });
  const scale = curScale();
  const vp1 = (await pdfDoc.getPage(1)).getViewport({ scale });
  for (let n = 1; n <= pdfDoc.numPages; n++) {
    if (token !== renderToken) return;
    const holder = document.createElement('div');
    holder.className = 'pdf-page';
    holder.style.width = vp1.width + 'px'; holder.style.height = vp1.height + 'px';
    holder.dataset.page = n;
    scroll.appendChild(holder); io.observe(holder);
  }
  $('#pdfPages').textContent = '共 ' + pdfDoc.numPages + ' 页';
  $('#zoomVal').textContent = Math.round(zoomFactor * 100) + '%';
}
async function renderPage(holder) {
  const n = +holder.dataset.page;
  const page = await pdfDoc.getPage(n);
  const vp = page.getViewport({ scale: curScale() });
  holder.style.width = vp.width + 'px'; holder.style.height = vp.height + 'px';
  const dpr = window.devicePixelRatio || 1;
  const cv = document.createElement('canvas');
  cv.width = Math.floor(vp.width * dpr); cv.height = Math.floor(vp.height * dpr);
  cv.style.width = vp.width + 'px'; cv.style.height = vp.height + 'px';
  holder.appendChild(cv);
  await page.render({ canvasContext: cv.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
}
function setZoom(f) { zoomFactor = Math.min(3, Math.max(0.5, f)); if (pdfDoc) layoutPages(++renderToken); }

// ====== 交互绑定 ======
function bindUI() {
  $('#search').oninput = (e) => { q = e.target.value.trim(); if (semActive) { if (!q) { semRank = null; refresh(); } } else refresh(); };
  $('#search').onkeydown = (e) => { if (e.key === 'Enter' && semActive) { e.preventDefault(); runSemSearch(q); } };
  $('#semToggle').onclick = toggleSem;
  document.querySelectorAll('.viewnav button').forEach(b => b.onclick = () => showView(b.dataset.view));
  document.querySelectorAll('#homeTable th[data-sort]').forEach(th => th.onclick = () => {
    const k = th.dataset.sort; if (homeSort.key === k) homeSort.dir *= -1; else homeSort = { key: k, dir: 1 }; renderHome();
  });
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));
  $('#genExplainerBtn').onclick = generateExplainer;
  $('#genTransBtn').onclick = generateTranslation;
  $('#findSimBtn').onclick = findSimilar;
  $('#btnEdit').onclick = () => { setSegActive('#tab-note .seg-sm', $('#btnEdit')); showNoteMode('edit'); $('#noteEdit').focus(); };
  $('#btnPreview').onclick = () => { renderMd($('#notePreview'), $('#noteEdit').value); setSegActive('#tab-note .seg-sm', $('#btnPreview')); showNoteMode('preview'); };
  $('#btnSave').onclick = saveNote;
  $('#noteEdit').onblur = () => { if ($('#noteEdit').value !== curNoteText) saveNote(); };
  document.querySelectorAll('#statusSeg button').forEach(b => b.onclick = () => saveStatus(b.dataset.st));
  $('#favBtn').onclick = () => { if (current) toggleFavorite(current.id); };
  $('#favFilter').onclick = toggleFavFilter;
  $('#zoomIn').onclick = () => setZoom(zoomFactor + 0.15);
  $('#zoomOut').onclick = () => setZoom(zoomFactor - 0.15);
  $('#ingSearchBtn').onclick = () => runSearch(null);
  $('#ingEditBtn').onclick = editQueries;
  $('#ingSearchWithBtn').onclick = () => runSearch(currentQueries());
  $('#ingQueryAdd').onkeydown = (e) => { if (e.key === 'Enter' && e.target.value.trim()) { const a = currentQueries() || []; a.push(e.target.value.trim()); e.target.value = ''; renderQueryChips(a); } };
  $('#ingestSelBtn').onclick = ingestSelected;
  $('#verifyVenueBtn').onclick = verifyVenues;
  $('#impScanBtn').onclick = scanPdfs;
  $('#impImportBtn').onclick = importPdfs;
  $('#citeBuildBtn').onclick = buildCite;
  $('#impDir').onkeydown = (e) => { if (e.key === 'Enter') scanPdfs(); };
  document.querySelectorAll('#candPanel .vsrc-chip').forEach(c => c.onclick = () => c.classList.toggle('active'));
  $('#mSearch').oninput = renderManage;
  $('#mSort').onchange = renderManage;
  $('#manualAddBtn').onclick = () => openPaperModal(null);
  $('#pmClose').onclick = closePaperModal;
  $('#pmCancel').onclick = closePaperModal;
  $('#pmSave').onclick = savePaperModal;
  $('#paperModal').onclick = (e) => { if (e.target.id === 'paperModal') closePaperModal(); };
  $('#setSaveBtn').onclick = saveSettings;
  $('#setTestBtn').onclick = testLLM;
  $('#reindexBtn').onclick = reindexAll;
  $('#settingsBtn').onclick = openSettingsModal;
  $('#setClose').onclick = closeSettingsModal;
  $('#settingsModal').onclick = (e) => { if (e.target.id === 'settingsModal') closeSettingsModal(); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeSettingsModal(); closePaperModal(); } });
  document.querySelectorAll('.ib-opts .src-chip').forEach(c => c.onclick = () => c.classList.toggle('active'));
  document.querySelectorAll('#libSrcFilter .fchip').forEach(c => c.onclick = () => {
    document.querySelectorAll('#libSrcFilter .fchip').forEach(x => x.classList.remove('active'));
    c.classList.add('active'); manageSrc = c.dataset.src; renderManage();
  });
  $('#themeBtn').onclick = toggleTheme;
  $('#toggleLeft').onclick = () => togglePane('hide-left');
  $('#toggleRight').onclick = () => togglePane('hide-right');
  let rzT; window.addEventListener('resize', () => { clearTimeout(rzT); rzT = setTimeout(() => { if (pdfDoc && currentView === 'read') layoutPages(++renderToken); [chProgress, chDir, chVenue, chTrend, chCite].forEach(c => c && c.resize()); }, 200); });
}
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(tp => tp.classList.remove('active'));
  $('#tab-' + name).classList.add('active');
}
function showNoteMode(mode) {
  $('#noteEdit').style.display = mode === 'edit' ? 'block' : 'none';
  $('#notePreview').style.display = mode === 'edit' ? 'none' : 'block';
}
function setSegActive(scope, btn) { document.querySelectorAll(scope + ' button').forEach(b => b.classList.remove('active')); btn.classList.add('active'); }
function setStatusUI(status) {
  $('#statusSel').value = status;
  document.querySelectorAll('#statusSeg button').forEach(b => b.classList.toggle('active', b.dataset.st === status));
}
async function saveNote() {
  if (!current) return;
  const content = $('#noteEdit').value;
  await fetch('/api/note', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: current.id, content }) });
  curNoteText = content;
  current.hasNote = !!content.trim();
  const pp = PAPERS.find(x => x.id === current.id); if (pp) pp.hasNote = current.hasNote;
  if (content.trim()) renderMd($('#notePreview'), content); else $('#notePreview').innerHTML = '<div class="placeholder">（空）</div>';
  const h = $('#saveHint'); h.textContent = '已保存 ✓ ' + new Date().toLocaleTimeString(); setTimeout(() => h.textContent = '', 2500);
}
async function saveStatus(status) {
  setStatusUI(status);
  if (!current) return;
  await fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: current.id, status }) });
  current.status = status;
  const p = PAPERS.find(x => x.id === current.id); if (p) p.status = status;
  renderSidebar();
}

// ====== 管理页 ======
function renderManage() {
  let list = PAPERS.slice();
  const kw = (($('#mSearch') && $('#mSearch').value) || '').trim().toLowerCase();
  if (kw) list = list.filter(p => (p.title + ' ' + p.venue + ' ' + p.type + ' ' + (p.topic || '')).toLowerCase().includes(kw));
  if (manageSrc === 'collected') list = list.filter(p => p.source !== 'seed');
  else if (manageSrc === 'seed') list = list.filter(p => p.source === 'seed');
  const sort = ($('#mSort') && $('#mSort').value) || 'added';
  const cmp = {
    added: (a, b) => (b.created_at || '').localeCompare(a.created_at || ''),
    relevance: (a, b) => (b.relevance || 0) - (a.relevance || 0),
    year: (a, b) => (b.year || '').localeCompare(a.year || ''),
    citations: (a, b) => (b.citations || 0) - (a.citations || 0),
    title: (a, b) => (a.title || '').localeCompare(b.title || '')
  }[sort] || (() => 0);
  list.sort(cmp);
  $('#mCount').textContent = `共 ${list.length}`;
  $('#mList').innerHTML = list.map(p => {
    const meta = [`<span class="venue v-${p.venue}">${p.venue} ${p.year}</span>`, p.type];
    if (p.topic) meta.push(p.topic);
    if (p.relevance != null) meta.push('rel ' + p.relevance);
    if (p.citations != null) meta.push(p.citations + ' cite');
    meta.push(fmtTime(p.created_at));
    return `<div class="m-item">
      <span class="mi-status status-dot ${p.status}" data-id="${p.id}" title="点击切换学习状态（当前：${p.status}）"></span>
      <div class="m-item-main" data-id="${p.id}">
        <div class="m-item-title">${p.title}</div>
        <div class="m-item-meta">${meta.join(' · ')}${p.source === 'manual' ? ' <span class="m-tag manual">手动</span>' : (p.source === 'localpdf' ? ' <span class="m-tag local">本地PDF</span>' : (p.source !== 'seed' ? ' <span class="m-tag">采集</span>' : ''))}</div>
      </div>
      <button class="m-fav ${p.favorite ? 'on' : ''}" data-id="${p.id}" title="${p.favorite ? '取消收藏' : '收藏'}">${p.favorite ? '★' : '☆'}</button>
      <button class="m-edit" data-id="${p.id}" title="编辑">✎</button>
      <button class="m-del" data-id="${p.id}" title="删除">🗑</button>
    </div>`;
  }).join('') || '<div class="placeholder">没有匹配的论文。</div>';
  document.querySelectorAll('#mList .m-item-main').forEach(el => el.onclick = () => openPaper(PAPERS.find(x => x.id === el.dataset.id)));
  document.querySelectorAll('#mList .m-fav').forEach(b => b.onclick = () => toggleFavorite(b.dataset.id));
  document.querySelectorAll('#mList .m-edit').forEach(b => b.onclick = () => openPaperModal(b.dataset.id));
  document.querySelectorAll('#mList .m-del').forEach(b => b.onclick = () => deletePaper(b.dataset.id));
  document.querySelectorAll('#mList .mi-status').forEach(s => s.onclick = () => cyclePaperStatus(s.dataset.id));
}
const STATUS_NEXT = { '未开始': '学习中', '学习中': '已理解', '已理解': '未开始' };
async function cyclePaperStatus(id) {
  const p = PAPERS.find(x => x.id === id); if (!p) return;
  const next = STATUS_NEXT[p.status] || '学习中';
  await fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, status: next }) });
  p.status = next;
  if (current && current.id === id) setStatusUI(next);
  renderManage(); renderSidebar(); updateSummary();
}

// ====== 收藏 ======
function setFavoriteUI(p) {
  const btn = $('#favBtn'); if (!btn) return;
  const on = !!(p && p.favorite);
  btn.classList.toggle('on', on);
  btn.textContent = on ? '★ 已收藏' : '☆ 收藏';
}
async function toggleFavorite(id) {
  const p = PAPERS.find(x => x.id === id); if (!p) return;
  const next = p.favorite ? 0 : 1;
  p.favorite = next;                                   // 乐观更新，先动 UI
  if (current && current.id === id) setFavoriteUI(p);
  renderSidebar(); if (currentView === 'home') renderHome(); if (currentView === 'manage') renderManage();
  try {
    await fetch('/api/favorite', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, favorite: !!next }) });
  } catch (e) { p.favorite = next ? 0 : 1; if (current && current.id === id) setFavoriteUI(p); renderSidebar(); }
}
function toggleFavFilter() {
  favOnly = !favOnly;
  const b = $('#favFilter'); b.classList.toggle('on', favOnly); b.textContent = favOnly ? '★ 收藏' : '☆ 收藏';
  refresh();
}

// ====== 语义检索（本地嵌入 + 余弦排序）======
function toggleSem() {
  semActive = !semActive;
  const b = $('#semToggle'); b.classList.toggle('on', semActive); b.textContent = semActive ? '🔮 语义 ✓' : '🔮 语义';
  const s = $('#search'); s.placeholder = semActive ? '语义检索：一句话/中文描述，回车…' : '搜索…';
  semRank = null;
  if (semActive) { s.focus(); if (q) runSemSearch(q); else refresh(); }
  else refresh();
}
async function runSemSearch(query) {
  query = (query || '').trim();
  if (!query) { semRank = null; refresh(); return; }
  if (semBusy) return;
  semBusy = true;
  const sum = $('#progressSummary');
  const tick = (t) => { if (sum) sum.textContent = t; };
  tick('🔮 语义检索中…');
  try {
    await streamNDJSON('/api/semsearch', { query, k: 60 }, (ev) => {
      if (ev.type === 'progress') {
        if (ev.line.startsWith('STAGE::model')) tick('🔮 载入模型（首次需下载，请稍候）…');
        else if (ev.line.startsWith('STAGE::index')) tick('🔮 首次建立语义索引…');
        else if (ev.line.startsWith('STAGE::query')) tick('🔮 匹配中…');
      } else if (ev.type === 'result') {
        if (ev.ok) { semRank = new Map((ev.results || []).map(r => [r.id, r.score])); refresh(); }
        else { updateSummary(); alert('语义检索失败：' + (ev.error || '未知') + '\n（首次使用需联网下载嵌入模型；可在 ⚙ 重建语义索引重试）'); }
      }
    });
  } catch (e) { updateSummary(); alert('语义检索失败：' + e); }
  finally { semBusy = false; }
}
async function reindexAll() {
  const btn = $('#reindexBtn'), hint = $('#reindexHint');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '重建中…';
  hint.textContent = '首次会下载嵌入模型，请稍候…';
  try {
    await streamNDJSON('/api/embed', { scope: 'all' }, (ev) => {
      if (ev.type === 'progress') {
        if (ev.line.startsWith('STAGE::model')) hint.textContent = '载入模型（首次需下载）…';
        else { const m = /^PROG::(\d+)::(\d+)/.exec(ev.line); if (m) hint.textContent = `向量化 ${m[1]} / ${m[2]}…`; }
      } else if (ev.type === 'result') {
        if (ev.ok) { hint.textContent = `✅ 已索引 ${ev.indexed} 篇`; semRank = null; }
        else hint.textContent = '失败：' + (ev.error || '未知');
      }
    });
  } catch (e) { hint.textContent = '失败：' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}

// ====== 手动添加 / 编辑论文 ======
const MTYPES = ['检测', '缓解·解码', '缓解·训练', '机制', '评测', '定义', '其他'];
const MTOPICS = ['知识-视觉冲突', '多图', '多物体', '通用物体', '语言先验', '其他'];
const PM_FIELDS = { title: 'pmTitleI', venue: 'pmVenue', year: 'pmYear', url: 'pmUrl', pdf_url: 'pmPdfUrl', pdf_path: 'pmPdfPath', tldr: 'pmTldr', abstract: 'pmAbstract', contribution: 'pmContribution' };
function fillSelect(sel, opts, val) { sel.innerHTML = opts.map(o => `<option ${o === val ? 'selected' : ''}>${o}</option>`).join(''); }
async function openPaperModal(id) {
  $('#pmHint').textContent = '';
  fillSelect($('#pmType'), MTYPES, '其他'); fillSelect($('#pmTopic'), MTOPICS, '其他');
  Object.values(PM_FIELDS).forEach(k => $('#' + k).value = '');
  if (id) {
    $('#pmTitle').textContent = '编辑论文';
    let row = null;
    try { row = await (await fetch('/api/paper/get?id=' + encodeURIComponent(id))).json(); } catch (e) {}
    if (!row) { alert('找不到该论文'); return; }
    $('#pmId').value = row.id;
    for (const [col, el] of Object.entries(PM_FIELDS)) $('#' + el).value = row[col] || '';
    fillSelect($('#pmType'), MTYPES, row.type || '其他'); fillSelect($('#pmTopic'), MTOPICS, row.topic || '其他');
  } else {
    $('#pmTitle').textContent = '手动添加论文';
    $('#pmId').value = '';
  }
  $('#paperModal').classList.remove('hidden');
  setTimeout(() => $('#pmTitleI').focus(), 30);
}
function closePaperModal() { $('#paperModal').classList.add('hidden'); }
async function savePaperModal() {
  const title = $('#pmTitleI').value.trim();
  if (!title) { $('#pmHint').textContent = '⚠ 标题不能为空'; return; }
  const payload = { type: $('#pmType').value, topic: $('#pmTopic').value };
  for (const [col, el] of Object.entries(PM_FIELDS)) payload[col] = $('#' + el).value.trim();
  const id = $('#pmId').value;
  const btn = $('#pmSave'); btn.disabled = true; $('#pmHint').textContent = '保存中…';
  try {
    if (id) payload.id = id;
    const j = await (await fetch(id ? '/api/paper/update' : '/api/paper/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json();
    if (!j.ok) { $('#pmHint').textContent = '失败: ' + (j.error || '未知错误'); return; }
    closePaperModal(); await reloadPapers(); renderManage(); updateSummary();
  } catch (e) { $('#pmHint').textContent = '失败: ' + e; }
  finally { btn.disabled = false; }
}

// ====== 采集向导（R3：流式两阶段 + 动画）======
let candidates = [];
const currentQueries = () => { try { return JSON.parse($('#ingQueryChips').dataset.qs || '[]'); } catch (e) { return []; } };

async function streamNDJSON(url, body, onEvent) {
  const resp = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!resp.body || !resp.body.getReader) { const j = await resp.json().catch(() => ({})); onEvent({ type: 'result', candidates: j.candidates || [] }); return; }
  const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
  for (; ;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (line) { try { onEvent(JSON.parse(line)); } catch (e) {} }
    }
  }
  if (buf.trim()) { try { onEvent(JSON.parse(buf.trim())); } catch (e) {} }
}
function setStage(name, cls) { const el = document.querySelector(`#ingStages .stage[data-st="${name}"]`); if (el) el.className = 'stage ' + cls; }
function renderQueryChips(qs) {
  const box = $('#ingQueryChips'); box.dataset.qs = JSON.stringify(qs);
  box.innerHTML = qs.map((x, i) => `<span class="iq-chip">${x}<b class="iq-x" data-i="${i}">×</b></span>`).join('') || '<span class="placeholder">（无检索词）</span>';
  document.querySelectorAll('#ingQueryChips .iq-x').forEach(b => b.onclick = () => { const a = currentQueries(); a.splice(+b.dataset.i, 1); renderQueryChips(a); });
}
async function editQueries() {
  const q = $('#ingQuery').value.trim(); if (!q) { alert('请先填写检索方向'); return; }
  $('#ingQueriesBox').classList.remove('hidden');
  $('#ingQueryChips').innerHTML = '<span class="placeholder">生成中…</span>';
  try {
    const j = await (await fetch('/api/expand', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: q, expandN: 6 }) })).json();
    renderQueryChips((j.queries && j.queries.length) ? j.queries : [q]);
  } catch (e) { renderQueryChips([q]); }
}
function handleProgress(line) {
  if (line.startsWith('STAGE::')) {
    const s = line.slice(7);
    if (s === 'search') { setStage('expand', 'done'); setStage('search', 'active'); }
    else if (s === 'classify') { setStage('search', 'done'); setStage('classify', 'active'); }
  } else if (line.startsWith('QUERIES::')) {
    try { renderQueryChips(JSON.parse(line.slice(9))); $('#ingQueriesBox').classList.remove('hidden'); } catch (e) {}
  } else if (line.startsWith('FOUND::')) { $('#stFound').textContent = ' ' + line.slice(7); }
  else if (line.startsWith('CLASSIFIED::')) { $('#stCls').textContent = ' ' + line.split('::')[1]; }
}
async function runSearch(queries) {
  const sources = [...document.querySelectorAll('.ib-opts .src-chip.active')].map(c => c.dataset.src);
  const q = $('#ingQuery').value.trim();
  if (!q) { alert('请填写检索方向'); return; }
  if (!sources.length) { alert('请至少选择一个数据源'); return; }
  candidates = [];
  $('#candPanel').classList.add('hidden');
  $('#ingStages').classList.remove('hidden');
  setStage('expand', queries ? 'done' : 'active'); setStage('search', ''); setStage('classify', '');
  $('#stFound').textContent = ''; $('#stCls').textContent = '';
  const btn = $('#ingSearchBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '检索中…';
  try {
    await streamNDJSON('/api/search', {
      query: q, sources, years: $('#ingYears').value.trim(),
      max: parseInt($('#ingMax').value) || 10, minRelevance: parseFloat($('#ingRel').value),
      expand: queries ? false : $('#ingExpand').checked, queries: queries || null
    }, (ev) => {
      if (ev.type === 'progress') handleProgress(ev.line);
      else if (ev.type === 'result') { candidates = ev.candidates || []; renderCandidates(); }
    });
  } catch (e) { alert('检索失败: ' + e); }
  finally { btn.disabled = false; btn.textContent = old; }
}
function renderCandidates() {
  setStage('expand', 'done'); setStage('search', 'done'); setStage('classify', 'done');
  $('#candPanel').classList.remove('hidden');
  const fresh = candidates.filter(c => !c.in_library).length;
  $('#candCount').textContent = `找到 ${candidates.length} 篇 · ${fresh} 篇新`;
  const SRC_SHORT = { dblp: 'DBLP', semanticscholar: 'S2', openalex: 'OpenAlex' };
  $('#candList').innerHTML = candidates.map((c, i) => {
    const rel = c.relevance != null ? Math.round(c.relevance * 100) : 0;
    const vv = c._verify;
    const vb = vv ? (
      vv.skipped ? `<b class="vbadge src" title="${vv.note || ''}">源自${SRC_SHORT[vv.source_of_truth] || vv.source_of_truth}</b>`
        : vv.matched ? `<b class="vbadge ok" title="权威来源：${SRC_SHORT[vv.source_of_truth] || vv.source_of_truth}">✓已核实${vv.changed ? '·已更正' : ''}</b>`
          : `<b class="vbadge miss" title="${vv.note || ''}">仅预印本</b>`) : '';
    return `<label class="cand ${c.in_library ? 'in-lib' : ''}">
      <input type="checkbox" class="cand-ck" data-i="${i}" ${c.in_library ? 'disabled' : 'checked'} />
      <div class="cand-main">
        <div class="cand-title">${c.title}</div>
        <div class="cand-meta"><span class="venue v-${c.venue || ''}">${c.venue || '—'} ${c.year || ''}</span>${vb ? ' ' + vb : ''} · ${c.type || ''}${c.topic ? ' · ' + c.topic : ''}${c.in_library ? ' · <b class="inlib-tag">已在库</b>' : ''}</div>
      </div>
      <div class="cand-rel" title="相关度 ${rel}%"><div class="cand-rel-track"><div class="cand-rel-bar" style="width:${rel}%"></div></div><span>${rel}</span></div>
    </label>`;
  }).join('') || '<div class="placeholder">没有匹配的候选。</div>';
  $('#candSelAll').checked = true;
  $('#candSelAll').onchange = () => document.querySelectorAll('#candList .cand-ck:not([disabled])').forEach(ck => ck.checked = $('#candSelAll').checked);
}
async function ingestSelected() {
  const picks = [...document.querySelectorAll('#candList .cand-ck:checked')].map(ck => candidates[+ck.dataset.i]).filter(Boolean);
  if (!picks.length) { alert('请勾选要入库的论文'); return; }
  const log = $('#ingLog'); log.classList.remove('hidden'); log.textContent = `入库 ${picks.length} 篇中…`;
  const btn = $('#ingestSelBtn'); btn.disabled = true;
  try {
    await streamNDJSON('/api/ingest-selected', { candidates: picks, deep: $('#ingDeep').checked }, (ev) => {
      if (ev.type === 'progress') { log.textContent += '\n' + ev.line; log.scrollTop = log.scrollHeight; }
      else if (ev.type === 'done') { log.textContent += `\n✅ 完成，新增 ${ev.added} 篇`; }
    });
    picks.forEach(p => p.in_library = true);
    await reloadPapers(); renderCandidates(); renderManage();
  } catch (e) { log.textContent = '失败: ' + e; }
  finally { btn.disabled = false; }
}
async function verifyVenues() {
  if (!candidates.length) { alert('请先检索出候选论文'); return; }
  const sources = [...document.querySelectorAll('#candPanel .vsrc-chip.active')].map(c => c.dataset.vsrc);
  if (!sources.length) { alert('请至少选择一个核实源'); return; }
  const log = $('#ingLog'); log.classList.remove('hidden'); log.textContent = `核实会议中（查 ${sources.join(' / ')} 权威库，非 LLM 臆测；本就来自所选源的会跳过）…`;
  const btn = $('#verifyVenueBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '核实中…';
  try {
    await streamNDJSON('/api/verify-venue', { candidates, sources }, (ev) => {
      if (ev.type === 'progress') { log.textContent += '\n' + ev.line; log.scrollTop = log.scrollHeight; }
      else if (ev.type === 'result') {
        const vs = ev.verifications || [];
        vs.forEach((v, i) => {
          if (!candidates[i]) return;
          candidates[i]._verify = v;
          if (v.matched && !v.skipped) { candidates[i].venue = v.venue; if (v.year) candidates[i].year = v.year; }
        });
        renderCandidates();
        const hit = vs.filter(v => v.matched && !v.skipped).length;
        const skp = vs.filter(v => v.skipped).length;
        const chg = vs.filter(v => v.changed).length;
        log.textContent += `\n✅ 核实完成：${hit} 篇查到正式发表（${chg} 篇会议更正）` + (skp ? `；${skp} 篇本就源自所选权威源，已跳过` : '');
        log.scrollTop = log.scrollHeight;
      }
    });
  } catch (e) { log.textContent = '失败: ' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}

// ====== 本地 PDF 批量导入 ======
let scannedFiles = [];
function fmtSize(n) { if (n == null) return ''; if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB'; if (n >= 1024) return Math.round(n / 1024) + ' KB'; return n + ' B'; }
async function scanPdfs() {
  const dir = $('#impDir').value.trim();
  if (!dir) { alert('请先填写文件夹路径'); return; }
  const hint = $('#impScanHint'), btn = $('#impScanBtn');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '扫描中…'; hint.textContent = '';
  try {
    const j = await (await fetch('/api/scan-pdfs?dir=' + encodeURIComponent(dir))).json();
    if (!j.ok) { hint.textContent = '✗ ' + (j.error || '扫描失败'); $('#impPanel').classList.add('hidden'); scannedFiles = []; return; }
    scannedFiles = j.files || [];
    renderImpList();
    hint.textContent = `找到 ${scannedFiles.length} 个 PDF`;
  } catch (e) { hint.textContent = '✗ ' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}
function renderImpList() {
  $('#impPanel').classList.remove('hidden');
  $('#impCount').textContent = `${scannedFiles.length} 个 PDF`;
  $('#impList').innerHTML = scannedFiles.map((f, i) => `
    <label class="cand imp-item">
      <input type="checkbox" class="imp-ck" data-i="${i}" checked />
      <div class="cand-main">
        <div class="cand-title">${esc(f.name)}</div>
        <div class="cand-meta">${fmtSize(f.size)} · ${esc(f.path)}</div>
      </div>
    </label>`).join('') || '<div class="placeholder">这个文件夹里没有 PDF。</div>';
  $('#impSelAll').checked = true;
  $('#impSelAll').onchange = () => document.querySelectorAll('#impList .imp-ck').forEach(ck => ck.checked = $('#impSelAll').checked);
}
async function importPdfs() {
  const picks = [...document.querySelectorAll('#impList .imp-ck:checked')].map(ck => scannedFiles[+ck.dataset.i]).filter(Boolean);
  if (!picks.length) { alert('请勾选要导入的 PDF'); return; }
  const enrich = $('#impEnrich').checked;
  const log = $('#impLog'); log.classList.remove('hidden');
  log.textContent = `导入 ${picks.length} 个 PDF…（读取首页 → 大模型抽标题/摘要 → 分类，每篇约几秒）`;
  const btn = $('#impImportBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '导入中…';
  try {
    await streamNDJSON('/api/import-pdfs', { paths: picks.map(f => f.path), enrich }, (ev) => {
      if (ev.type === 'progress') {
        const ln = ev.line; let m;
        if ((m = /^TOTAL::(\d+)/.exec(ln))) log.textContent += `\n共 ${m[1]} 篇，读取解析中…`;
        else if ((m = /^PARSED::(\d+)::(\d+)::(.*)/.exec(ln))) log.textContent += `\n  解析 ${m[1]}/${m[2]}：${m[3]}`;
        else if ((m = /^ADDED::(.*)/.exec(ln))) log.textContent += `\n  ✅ 入库：${m[1]}`;
        else if ((m = /^DUP::(.*)/.exec(ln))) log.textContent += `\n  ↩ 已在库，跳过：${m[1]}`;
        else if (/^SKIP::/.test(ln)) log.textContent += `\n  ⚠ 跳过（无法解析标题）`;
        log.scrollTop = log.scrollHeight;
      } else if (ev.type === 'result') {
        log.textContent += ev.ok
          ? `\n\n✅ 完成：新增 ${ev.added} · 已在库 ${ev.dup} · 失败 ${ev.failed}`
          : `\n\n✗ 失败：${ev.error || '未知'}`;
        log.scrollTop = log.scrollHeight;
      }
    });
    await reloadPapers(); renderManage();
  } catch (e) { log.textContent += '\n✗ ' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}

async function reloadPapers() {
  PAPERS = normPapers(await (await fetch('/api/papers')).json());
  buildYearFilters();
  renderSidebar();
  renderHome();
}

async function deletePaper(id) {
  const p = PAPERS.find(x => x.id === id);
  if (!confirm(`删除《${p ? p.title : id}》？此操作不可撤销。`)) return;
  await fetch('/api/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
  PAPERS = PAPERS.filter(x => x.id !== id);
  if (current && current.id === id) { current = null; }
  renderManage(); renderHome(); renderSidebar();
}

// ====== 设置 ======
async function loadSettings() {
  try {
    const s = await (await fetch('/api/settings')).json();
    $('#setProvider').value = s.provider || 'deepseek';
    $('#setBaseUrl').value = s.baseUrl || '';
    $('#setModel').value = s.model || '';
    $('#setApiKey').value = '';
    $('#setS2Key').value = '';
    $('#setPdfDir').value = s.pdfDir || '';
    if ($('#setTheme')) $('#setTheme').value = s.researchTheme || '';
    $('#setKeyTip').textContent = s.hasApiKey ? `当前已配置：${s.apiKeyTail}` : '⚠️ 未配置 API Key';
    $('#setS2Tip').textContent = s.hasS2Key ? `当前已配置：${s.s2KeyTail}` : '未配置（不填也能用，仅高峰可能限流）';
    const meta = $('#setSummaryMeta'); if (meta) meta.textContent = `${s.provider} · ${s.model || '—'}` + (s.hasApiKey ? '' : ' · ⚠ 未配置 Key');
  } catch (e) { }
}
async function testLLM() {
  const h = $('#setHint'); h.textContent = '测试中…';
  try {
    const j = await (await fetch('/api/test-llm', { method: 'POST' })).json();
    h.textContent = j.ok ? '连接正常 ✓' : ('失败：' + String(j.output || '').replace(/\s+/g, ' ').slice(-100));
  } catch (e) { h.textContent = '失败：' + e; }
  setTimeout(() => h.textContent = '', 6000);
}
async function saveSettings() {
  const body = {
    provider: $('#setProvider').value,
    baseUrl: $('#setBaseUrl').value.trim(),
    model: $('#setModel').value.trim(),
    pdfDir: $('#setPdfDir').value.trim(),
    researchTheme: $('#setTheme') ? $('#setTheme').value.trim() : ''
  };
  const ak = $('#setApiKey').value.trim(), sk = $('#setS2Key').value.trim();
  const badKey = (k) => k && !/^[\x21-\x7E]+$/.test(k);   // 合法 key 全是无空格的可见 ASCII；含空格/中文 → 拦截
  if (badKey(ak) || badKey(sk)) { const h = $('#setHint'); h.textContent = '⚠ API Key 含空格或非英文字符，看着不像 key，已拦截'; setTimeout(() => h.textContent = '', 4000); return; }
  if (ak) body.apiKey = ak;
  if (sk) body.s2ApiKey = sk;
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const h = $('#setHint'); h.textContent = '已保存 ✓（下次采集生效）'; setTimeout(() => h.textContent = '', 3000);
  loadSettings();
}
function openSettingsModal() { loadSettings(); $('#settingsModal').classList.remove('hidden'); }
function closeSettingsModal() { $('#settingsModal').classList.add('hidden'); }
