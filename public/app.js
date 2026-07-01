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
let sideQ = '', sideStatus = 'all', sideFav = false, sideYear = 'all';  // 阅读侧边栏筛选：搜索 / 状态 / 收藏 / 年份
let currentView = 'home';
let homeSort = { key: 'year', dir: 1 };
let manageSrc = 'all';
let reviewData = null;
let chProgress = null, chDir = null, chVenue = null;
let chTrend = null, chTree = null, chCited = null, chCite = null;  // 洞察：趋势面积 / 馆藏树图 / 被引 / 引用图

const $ = (s) => document.querySelector(s);
const md = (t) => (window.marked ? window.marked.parse(t || '') :
  '<pre>' + (t || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) + '</pre>');
const esc = (s) => (s == null ? '' : String(s)).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const normTitle = (s) => (s || '').toLowerCase().replace(/[^a-z0-9一-龥]+/g, '');  // 同 db.title_norm
// 渲染 markdown 到元素，并用 KaTeX 把 $...$ / $$...$$ 公式排版出来（讲解/译文/笔记共用）。
// 关键：必须先把公式抽成占位符再交给 marked，否则 marked 会把 LaTeX 里的 _ 当斜体、[..](..) 当链接，
// 把 $$...$$ 整块公式破坏掉（KaTeX 再扫已无完整分隔符 → 原样显示一坨 LaTeX）。用 PUA 字符做占位，marked 不会动它。
function renderMd(el, text) {
  const math = [];
  const stash = (tex, display) => '' + (math.push({ tex, display }) - 1) + '';
  let src = String(text || '')
    .replace(/\$\$([\s\S]+?)\$\$/g, (_m, x) => stash(x, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_m, x) => stash(x, true))
    .replace(/(?<!\\)\$([^\n$]+?)\$/g, (_m, x) => stash(x, false))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, x) => stash(x, false));
  let html = md(src).replace(/(\d+)/g, (_m, i) => {
    const it = math[+i];
    if (!it) return '';
    try { return window.katex ? katex.renderToString(it.tex.trim(), { displayMode: it.display, throwOnError: false }) : esc(it.tex); }
    catch (e) { return esc(it.tex); }
  });
  el.innerHTML = html;
}
const EMPTY_HTML = '<div id="viewerEmpty" class="empty"><div class="empty-ico">📄</div><div class="empty-title">从左侧选择一篇论文</div><div class="empty-sub">建议从 ① HallusionBench 开始</div></div>';

// 会议名归一化：统一大小写/常见别名，避免 NeurIPS 与 NEURIPS、arXiv 与 arXiv.org 被当成两个会议
const VENUE_CANON = { neurips: 'NeurIPS', nips: 'NeurIPS', cvpr: 'CVPR', iccv: 'ICCV', eccv: 'ECCV', wacv: 'WACV', icml: 'ICML', iclr: 'ICLR', aaai: 'AAAI', ijcai: 'IJCAI', acl: 'ACL', emnlp: 'EMNLP', naacl: 'NAACL', coling: 'COLING', tmlr: 'TMLR', tpami: 'TPAMI', corr: 'arXiv' };
// 会议「全名 → 缩写」子串匹配（顺序敏感：更具体的在前，如 NAACL/Findings 先于 ACL）
const VENUE_FULL = [
  ['empirical methods in natural language', 'EMNLP'],
  ['north american chapter', 'NAACL'],
  ['findings of the association for computational linguistics', 'ACL Findings'],
  ['association for computational linguistics', 'ACL'],
  ['computer vision and pattern recognition', 'CVPR'],
  ['european conference on computer vision', 'ECCV'],
  ['winter conference on applications of computer vision', 'WACV'],
  ['international conference on computer vision', 'ICCV'],
  ['learning representations', 'ICLR'],
  ['international conference on machine learning', 'ICML'],
  ['neural information processing systems', 'NeurIPS'],
  ['international joint conference on artificial intelligence', 'IJCAI'],
  ['aaai conference on artificial intelligence', 'AAAI'],
  ['advancement of artificial intelligence', 'AAAI'],
  ['acm multimedia', 'ACM MM'],
  ['international conference on multimedia', 'ACM MM']
];
function normVenue(v) {
  if (!v) return v;
  const s = String(v).trim();
  const k = s.toLowerCase();
  if (VENUE_CANON[k]) return VENUE_CANON[k];                 // 缩写大小写变体
  if (k.startsWith('arxiv')) return 'arXiv';                 // arXiv / arXiv.org / arXiv preprint…
  for (const [sub, abbr] of VENUE_FULL) { if (k.includes(sub)) return abbr; }   // 全名 → 缩写
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
  applyTheme(localStorage.getItem('theme') || 'light');
  if (localStorage.getItem('hide-left') === '1') $('#layout').classList.add('hide-left');
  if (localStorage.getItem('hide-right') === '1') $('#layout').classList.add('hide-right');
  buildYearFilters();
  buildSideYears();
  renderSidebar();
  buildDashShell();
  renderHome();
  bindUI();
  initResizers();
  showView('home');
}
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  const b = $('#themeBtn');
  if (b) {
    b.textContent = t === 'dark' ? 'Light' : 'Dark';
    b.setAttribute('aria-label', t === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  }
  localStorage.setItem('theme', t);
}
function toggleTheme() { applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); renderHome(); if (currentView === 'insights') renderInsights(); }
function togglePane(cls) { const L = $('#layout'); L.classList.toggle(cls); localStorage.setItem(cls, L.classList.contains(cls) ? '1' : '0'); if (pdfDoc && currentView === 'read') setTimeout(() => layoutPages(++renderToken), 240); }
const MIN_VIEWER = 320; // 中间 PDF 区最小宽度，任何时候都保留
function initResizers() {
  const layout = $('#layout');
  const apply = (k, v) => document.documentElement.style.setProperty(k, v + 'px');
  // 载入时校验持久化宽度：单边夹值 + 保证两侧之和给中间 PDF 留 ≥MIN_VIEWER，否则整体复位默认（修复历史异常拖拽值把布局挤坏的问题）
  let lw = parseInt(localStorage.getItem('left-w'), 10) || 0;
  let rw = parseInt(localStorage.getItem('right-w'), 10) || 0;
  if (lw) lw = Math.max(200, lw);
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
        const w = Math.max(side === 'left' ? 200 : 220, Math.min(Math.round(raw), maxW));
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
function buildSideYears() {
  const box = $('#sideYears'); if (!box) return; box.innerHTML = '';
  const years = [...new Set(PAPERS.map(p => p.year))].filter(y => /^\d{4}$/.test(y)).sort();
  if (years.length <= 6) {
    // 年份不多：chips（一行/两行可放下，最新在右）
    ['all', ...years].forEach(y => {
      const b = document.createElement('button');
      b.className = 'chip-btn' + (sideYear === y ? ' active' : '');
      b.textContent = y === 'all' ? '全部' : y;
      b.onclick = () => { sideYear = y; [...box.children].forEach(c => c.classList.toggle('active', c === b)); renderSidebar(); };
      box.appendChild(b);
    });
  } else {
    // 年份过多：收成下拉，永不撑高筛选头（全部 + 年份新→旧）
    const sel = document.createElement('select');
    sel.className = 'year-select side-year-select';
    sel.innerHTML = ['all', ...years.slice().reverse()]
      .map(y => `<option value="${y}" ${sideYear === y ? 'selected' : ''}>${y === 'all' ? '全部年份' : y + ' 年'}</option>`).join('');
    sel.onchange = () => { sideYear = sel.value; renderSidebar(); };
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
  $('#review').classList.toggle('hidden', v !== 'review');
  $('#manage').classList.toggle('hidden', v !== 'manage');
  $('#insights').classList.toggle('hidden', v !== 'insights');
  $('#jobs').classList.toggle('hidden', v !== 'jobs');
  $('#layout').classList.toggle('hidden', v !== 'read');
  const tf = document.getElementById('topFilters'); if (tf) tf.classList.toggle('hidden', v !== 'home');
  if (v === 'home') { renderHome(); refreshExplainBatch(); }
  if (v === 'review') loadReviews();
  if (v === 'manage') renderManage();
  if (v === 'insights') renderInsights();
  if (v === 'jobs') renderJobs(); else stopJobsPoll();
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
    <div class="chart-card d-prog"><div class="chart-title">学习进度</div><div id="chartProgress" class="echart"></div></div>
    <div class="chart-card d-venue"><div class="chart-title">会议分布</div><div id="chartVenue" class="echart"></div></div>
    <div class="chart-card dash-wide"><div class="chart-title">研究方向分布</div><div id="chartDir" class="echart"></div></div>`;
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
  $('#homeBody').innerHTML = list.map((p, i) => rowHTML(p, i + 1)).join('') || `<tr><td colspan="11" class="empty-row">${emptyMsg}</td></tr>`;
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
  const compact = window.innerWidth < 700;
  chProgress.setOption({
    animationDuration: 750, animationDurationUpdate: 600, animationEasing: 'cubicOut',
    title: {
      text: d.pct + '%', subtext: '已理解', left: compact ? '50%' : '33%', top: compact ? '38%' : 'center', textAlign: 'center', itemGap: 4,
      textStyle: { fontSize: 26, fontWeight: 700, color: text }, subtextStyle: { fontSize: 10.5, color: t3 }
    },
    tooltip: { trigger: 'item', formatter: '{b}：{c} 篇 ({d}%)' },
    legend: {
      orient: compact ? 'horizontal' : 'vertical',
      right: compact ? 'center' : '4%',
      top: compact ? '78%' : 'center',
      itemWidth: 9, itemHeight: 9, itemGap: compact ? 10 : 13,
      icon: 'roundRect', textStyle: { color: t2, fontSize: 11.5 },
      formatter: (name) => { const m = { '已理解': d.done, '学习中': d.ing, '未开始': d.idle }; return `${name}  ${m[name] || 0}`; }
    },
    series: [{
      type: 'pie', radius: compact ? ['45%', '68%'] : ['56%', '80%'], center: compact ? ['50%', '38%'] : ['33%', '50%'], avoidLabelOverlap: false,
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
  const palette = ['#256f8f', '#2e9a92', '#4f84a2', '#c4882f', '#6c8798', '#7d8fa0', '#b36b7c'];
  const out = entries.map(([name, value], i) => ({ name, value, color: palette[i % palette.length] }));
  const otherTotal = literalOther + merged.reduce((s, [, v]) => s + v, 0);
  if (otherTotal) {
    const breakdown = merged.map(([name, value]) => ({ name, value }));
    if (literalOther) breakdown.push({ name: '（未细分）', value: literalOther });
    breakdown.sort((a, b) => b.value - a.value);
    out.push({ name: '其他', value: otherTotal, color: '#8fa3ad', breakdown });
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

// ====== 复习：艾宾浩斯队列 ======
function blankReviewData(error = '') {
  return {
    ok: !error,
    error,
    today: '',
    counts: { overdue: 0, dueToday: 0, upcoming: 0, completed: 0 },
    overdue: [],
    dueToday: [],
    upcoming: [],
    completed: []
  };
}
async function loadReviews(renderList = true) {
  try {
    const data = await (await fetch('/api/reviews')).json();
    reviewData = data && data.counts ? data : blankReviewData('复习数据格式异常');
  } catch (e) {
    reviewData = blankReviewData(String(e));
  }
  if (renderList) renderReviews();
  renderCurrentReviewStatus();
  return reviewData;
}
function reviewItems(data = reviewData) {
  const d = data || blankReviewData();
  return [...(d.overdue || []), ...(d.dueToday || []), ...(d.upcoming || []), ...(d.completed || [])];
}
function currentReviewItem(id) {
  if (!id || !reviewData) return null;
  return reviewItems().find(item => item.paper_id === id) || null;
}
function dayIndex(day) {
  const [y, m, d] = String(day || '').slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return null;
  return Math.floor(Date.UTC(y, m - 1, d) / 86400000);
}
function dueText(item, today) {
  if (!item) return '';
  if (item.completed_at) return '已完成';
  if (!today || !item.next_due_at) return item.next_due_at || '';
  const due = dayIndex(item.next_due_at), now = dayIndex(today);
  if (due == null || now == null) return item.next_due_at;
  const diff = due - now;
  if (diff < 0) return `已逾期 ${Math.abs(diff)} 天`;
  if (diff === 0) return '今天';
  return `${diff} 天后`;
}
function reviewIsActive(item) {
  return item && !item.completed_at && (item.review_state === 'overdue' || item.review_state === 'dueToday');
}
function reviewCard(item, kind) {
  const p = PAPERS.find(x => x.id === item.paper_id) || {};
  const title = item.title || p.title || item.paper_id;
  const venueYear = [item.venue || p.venue || '未标注', item.year || p.year || ''].filter(Boolean).join(' ');
  const step = `${item.current_step || 1}/${item.total_steps || 7}`;
  const active = reviewIsActive(item);
  return `<div class="review-card ${kind}" data-id="${esc(item.paper_id)}">
    <div class="review-main">
      <div class="review-title">${esc(title)}</div>
      <div class="review-meta"><span>${esc(venueYear)}</span><span>${esc(item.status || '未开始')}</span><span>第 ${step} 轮</span><span>${esc(dueText(item, reviewData && reviewData.today))}</span></div>
    </div>
    <div class="review-actions">
      <button class="mini ghost review-open" data-id="${esc(item.paper_id)}">开始阅读</button>
      ${active ? `<button class="mini primary review-done" data-id="${esc(item.paper_id)}">完成本轮</button>` : ''}
    </div>
  </div>`;
}
function renderReviews() {
  const dash = $('#reviewDash'), list = $('#reviewList');
  if (!dash || !list) return;
  const d = reviewData || blankReviewData();
  dash.innerHTML = [
    ['今日到期', d.counts.dueToday || 0],
    ['已逾期', d.counts.overdue || 0],
    ['未来计划', d.counts.upcoming || 0],
    ['已完成', d.counts.completed || 0]
  ].map(([label, value]) => `<div class="review-stat"><b>${value}</b><span>${label}</span></div>`).join('');
  const groups = [
    ['overdue', '已逾期', d.overdue || []],
    ['dueToday', '今日到期', d.dueToday || []],
    ['upcoming', '未来计划', d.upcoming || []],
    ['completed', '已完成', d.completed || []]
  ];
  list.innerHTML = (d.error ? `<div class="review-error">${esc(d.error)}</div>` : '') + groups.map(([kind, title, items]) => `
    <div class="review-group ${kind}">
      <div class="review-group-head"><span>${title}</span><b>${items.length}</b></div>
      ${items.length ? items.map(item => reviewCard(item, kind)).join('') : '<div class="review-empty">这一组暂无论文。</div>'}
    </div>
  `).join('');
  list.querySelectorAll('.review-open').forEach(btn => btn.onclick = () => openPaper(PAPERS.find(p => p.id === btn.dataset.id)));
  list.querySelectorAll('.review-done').forEach(btn => btn.onclick = () => completeReview(btn.dataset.id));
}
function renderCurrentReviewStatus() {
  const box = $('#reviewStatus');
  if (!box) return;
  const item = currentReviewItem(current && current.id);
  box.classList.toggle('hidden', !item);
  if (!item) { box.innerHTML = ''; return; }
  const step = `${item.current_step || 1}/${item.total_steps || 7}`;
  const active = reviewIsActive(item);
  box.innerHTML = `<div class="review-status-main">
    <span class="review-status-k">复习</span>
    <span class="review-status-v">第 ${step} 轮 · ${esc(dueText(item, reviewData && reviewData.today))}</span>
  </div>
  ${active ? `<button class="mini primary" data-review-done="${esc(item.paper_id)}">完成本轮</button>` : ''}`;
  const btn = box.querySelector('[data-review-done]');
  if (btn) btn.onclick = () => completeReview(btn.dataset.reviewDone);
}
async function completeReview(id) {
  const r = await (await fetch('/api/reviews/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id })
  })).json();
  if (!r.ok) { alert(r.error || '复习更新失败'); return; }
  reviewData = r.reviews || await (await fetch('/api/reviews')).json();
  renderReviews();
  renderCurrentReviewStatus();
}

// ====== 洞察：研究趋势(堆叠面积) + 馆藏构成(树图) + 被引Top10 + 引用关系图 ======
function buildInsightsShell() {
  if (chCite || !window.echarts) return;
  chTree = echarts.init($('#chartTree'));
  chTrend = echarts.init($('#chartTrend'));
  chCited = echarts.init($('#chartCited'));
  chCite = echarts.init($('#chartCite'));
  chCite.on('click', (params) => {
    if (params.dataType === 'node') { const p = PAPERS.find(x => x.id === params.data.id); if (p) openPaper(p); }
  });
}
async function renderInsights() {
  buildInsightsShell();
  renderTree();
  renderTrend();
  try {
    const g = await (await fetch('/api/citegraph')).json();
    if (g && g.edgeCount > 0) { renderCite(g); renderCited(g); }
    else { showCitePrompt(); renderCited(null); }
  } catch (e) { showCitePrompt(); renderCited(null); }
}
// 馆藏构成：研究方向 → 主题 的矩形树图（块大小=论文数，颜色=方向）
function renderTree() {
  if (!chTree) return;
  const dirItems = topGroups(PAPERS, p => (p.type || '').split('·')[0], 7);
  const topNames = new Set(dirItems.filter(d => d.name !== '其他').map(d => d.name));
  const bucketDir = (p) => { const k = (p.type || '').split('·')[0] || '其他'; return topNames.has(k) ? k : '其他'; };
  const tree = {};
  PAPERS.forEach(p => { const d = bucketDir(p), t = (p.topic || '').trim() || '未分'; (tree[d] = tree[d] || {})[t] = (tree[d][t] || 0) + 1; });
  const data = dirItems.map(d => {
    const kids = Object.entries(tree[d.name] || {}).sort((a, b) => b[1] - a[1]).map(([t, v]) => ({ name: t, value: v }));
    return { name: d.name, value: d.value, itemStyle: { color: d.color }, children: kids.length ? kids : undefined };
  });
  chTree.setOption({
    animationDuration: 600,
    tooltip: { formatter: (p) => `${(p.treePathInfo || []).map(x => x.name).filter(Boolean).join(' / ') || p.name}　${p.value} 篇` },
    series: [{
      type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false }, visibleMin: 1,
      top: 4, left: 4, right: 4, bottom: 4,
      label: { show: true, color: '#fff', fontSize: 11, overflow: 'truncate', textBorderColor: 'rgba(0,0,0,.3)', textBorderWidth: 2 },
      upperLabel: { show: true, height: 20, color: '#fff', fontSize: 11.5, fontWeight: 600, textBorderColor: 'rgba(0,0,0,.32)', textBorderWidth: 2 },
      itemStyle: { borderColor: cssVar('--surface'), borderWidth: 2, gapWidth: 2, borderRadius: 4 },
      levels: [
        { itemStyle: { borderWidth: 0, gapWidth: 4, borderRadius: 8 } },
        { colorSaturation: [0.3, 0.52], itemStyle: { gapWidth: 2, borderColorSaturation: 0.55 } }
      ],
      data
    }]
  });
}
// 研究趋势：各年份 × 研究方向 的堆叠渐变面积（看每个方向逐年消长）
function renderTrend() {
  if (!chTrend) return;
  const t2 = cssVar('--ink-2'), t3 = cssVar('--ink-3');
  const years = [...new Set(PAPERS.map(p => p.year).filter(y => /^\d{4}$/.test(y)))].sort();
  const dirItems = topGroups(PAPERS, p => (p.type || '').split('·')[0], 6);
  const topNames = new Set(dirItems.filter(d => d.name !== '其他').map(d => d.name));
  const bucket = (p) => { const k = (p.type || '').split('·')[0] || '其他'; return topNames.has(k) ? k : '其他'; };
  const hex8 = (c, a) => (c && c[0] === '#' && c.length === 7) ? c + a : c;
  const series = dirItems.map(d => ({
    name: d.name, type: 'line', stack: 'total', smooth: 0.4, symbol: 'none',
    lineStyle: { width: 1.5, color: d.color },
    areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: hex8(d.color, 'cc') }, { offset: 1, color: hex8(d.color, '1f') }]) },
    emphasis: { focus: 'series' },
    data: years.map(y => PAPERS.filter(p => p.year === y && bucket(p) === d.name).length)
  }));
  chTrend.setOption({
    animationDuration: 700, animationEasing: 'cubicOut',
    color: dirItems.map(d => d.color),
    legend: { top: 2, textStyle: { color: t2, fontSize: 11 }, itemWidth: 11, itemHeight: 11, itemGap: 12 },
    grid: { left: 6, right: 18, top: 40, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: cssVar('--border-2') } } },
    xAxis: { type: 'category', boundaryGap: false, data: years, axisTick: { show: false }, axisLine: { lineStyle: { color: cssVar('--border') } }, axisLabel: { color: t2, fontSize: 12 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: cssVar('--surface-3') } }, axisLabel: { color: t3, fontSize: 11 } },
    series
  });
}
// 馆内被引 Top 10（来自引用图，未构建则提示）
function renderCited(g) {
  if (!chCited) return;
  const t2 = cssVar('--ink-2');
  const nodes = ((g && g.nodes) || []).filter(n => n.indeg > 0).sort((a, b) => b.indeg - a.indeg).slice(0, 10);
  chCited.clear();
  if (!nodes.length) {
    chCited.setOption({ graphic: { type: 'text', left: 'center', top: 'center', style: { text: '构建引用图后\n显示馆内被引排行', fill: cssVar('--ink-3'), fontSize: 12, lineHeight: 20, textAlign: 'center' } } });
    return;
  }
  const labels = nodes.map(n => (n.title.length > 20 ? n.title.slice(0, 20) + '…' : n.title)).reverse();
  const rows = nodes.map(n => ({ value: n.indeg, id: n.id })).reverse();
  chCited.setOption({
    animationDuration: 600,
    grid: { left: 6, right: 30, top: 6, bottom: 6, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p) => `被库内 ${p[0].value} 篇引用` },
    xAxis: { type: 'value', max: 'dataMax', axisLabel: { show: false }, splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'category', data: labels, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t2, fontSize: 11 } },
    series: [{ type: 'bar', data: rows, barWidth: '60%', itemStyle: { color: cssVar('--primary'), borderRadius: [0, 6, 6, 0] }, label: { show: true, position: 'right', color: t2, fontSize: 11, fontWeight: 600, formatter: '{c}' } }]
  });
  chCited.off('click');
  chCited.on('click', (p) => { const r = rows[p.dataIndex]; const pp = r && PAPERS.find(x => x.id === r.id); if (pp) openPaper(pp); });
}
function renderCite(g) {
  if (!chCite) return;
  chCite.clear();
  $('#citeHint').textContent = `${g.nodes.length} 篇 · ${g.edgeCount} 条引用 · 环上按方向分组、越大被引越多 · 悬停看关系`;
  const t2 = cssVar('--ink-2');
  // 节点按方向分桶（与看板/趋势一致，避免图例过多、配色重复）
  const dirItems = topGroups(g.nodes, n => (n.type || '').split('·')[0], 6);
  const topNames = new Set(dirItems.filter(d => d.name !== '其他').map(d => d.name));
  const bucket = (n) => { const k = (n.type || '').split('·')[0] || '其他'; return topNames.has(k) ? k : '其他'; };
  const cats = dirItems.map(d => d.name);
  const colorOf = {}; dirItems.forEach(d => { colorOf[d.name] = d.color; });
  const maxIn = Math.max(1, ...g.nodes.map(n => n.indeg));
  const labelMin = Math.max(5, maxIn * 0.28);
  // 按方向分组、组内按被引降序 → 同色在环上相邻，高被引枢纽带标签
  const nodes = g.nodes.slice().sort((a, b) => (cats.indexOf(bucket(a)) - cats.indexOf(bucket(b))) || (b.indeg - a.indeg));
  const data = nodes.map(n => ({
    id: n.id, name: n.title,
    symbolSize: 8 + (n.indeg / maxIn) * 26,
    category: cats.indexOf(bucket(n)), value: n.indeg,
    label: { show: n.indeg >= labelMin }
  }));
  chCite.setOption({
    tooltip: { confine: true, formatter: (p) => p.dataType === 'node' ? `${esc(p.data.name)}<br>被库内 <b>${p.data.value}</b> 篇引用` : '' },
    legend: [{ data: cats, top: 2, textStyle: { color: t2, fontSize: 11 }, itemWidth: 11, itemHeight: 11, itemGap: 12 }],
    series: [{
      type: 'graph', layout: 'circular', circular: { rotateLabel: true }, roam: true,
      categories: cats.map(c => ({ name: c, itemStyle: { color: colorOf[c] } })),
      data, links: g.links.map(l => ({ source: l.source, target: l.target })),
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 4,
      lineStyle: { color: 'source', opacity: 0.18, width: 1, curveness: 0.3 },
      label: { position: 'right', fontSize: 9.5, color: t2, formatter: (p) => p.data.name.length > 14 ? p.data.name.slice(0, 14) + '…' : p.data.name },
      emphasis: { focus: 'adjacency', lineStyle: { width: 2, opacity: 0.65 }, label: { show: true } }
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
  else if (k === 'ccf') { const m = { A: 0, B: 1, C: 2 }; va = (a.ccf in m) ? m[a.ccf] : 3; vb = (b.ccf in m) ? m[b.ccf] : 3; }
  else { va = (a[k] || '') + ''; vb = (b[k] || '') + ''; }
  if (va < vb) return -d; if (va > vb) return d;
  return (a.year + '').localeCompare(b.year + '') || ((a.order || 99) - (b.order || 99));
}
function semScoreBadge(id) {
  if (!(semActive && semRank && semRank.has(id))) return '';
  const pct = Math.max(0, Math.min(100, Math.round(semRank.get(id) * 100)));
  return `<span class="sem-score" title="语义相关度 ${pct}%">${pct}</span>`;
}
function ccfBadge(ccf) { return ccf ? `<span class="ccf ccf-${ccf}" title="CCF ${ccf} 类">${ccf}</span>` : ''; }
function pdfBadge(has) { return has ? `<span class="pdf-flag" title="PDF 已在本地，可直接阅读">📄</span>` : `<span class="pdf-flag off" title="本地暂无 PDF（可在「管理」页「补下载缺失 PDF」）">📄</span>`; }
function rowHTML(p, idx) {
  return `<tr data-id="${p.id}">
    <td><span class="ht-idx">${idx}</span></td>
    <td class="ht-title" title="${esc(p.title)}">${semScoreBadge(p.id)}<span class="fav-star ${p.favorite ? 'on' : ''}" data-id="${p.id}" title="${p.favorite ? '取消收藏' : '收藏'}">${p.favorite ? '★' : '☆'}</span>${p.title}</td>
    <td><span class="venue v-${p.venue}">${p.venue}</span></td>
    <td class="ht-ccf">${ccfBadge(p.ccf)}</td>
    <td class="ht-pdf">${pdfBadge(p.hasPdf)}</td>
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
  const side = $('#sideList'); if (!side) return; side.innerHTML = '';
  const sem = semActive && semRank;
  // 阅读侧边栏自有筛选：语义结果（若开启）为底，再按状态 / 收藏 / 搜索收窄
  let list = sem ? PAPERS.filter(p => semRank.has(p.id)) : PAPERS.slice();
  if (sideYear !== 'all') list = list.filter(p => p.year === sideYear);
  if (sideStatus !== 'all') list = list.filter(p => (p.status || '未开始') === sideStatus);
  if (sideFav) list = list.filter(p => p.favorite);
  if (sideQ) { const k = sideQ.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + (p.topic || '') + ' ' + (p.type || '')).toLowerCase().includes(k)); }
  if (!list.length) {
    const e = document.createElement('div'); e.className = 'side-empty';
    e.textContent = sideFav && sideStatus === 'all' && sideYear === 'all' && !sideQ ? '还没有收藏的论文。' : '没有符合筛选条件的论文。';
    side.appendChild(e); updateSummary(); return;
  }
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
     <div class="pi-meta"><span class="venue v-${p.venue}">${p.venue} ${p.year}</span>${ccfBadge(p.ccf)}${pdfBadge(p.hasPdf)}<span class="dir">${p.type}${p.topic ? ' · ' + p.topic : ''}</span>${semScoreBadge(p.id)}</div>`;
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
  if (reviewData) renderCurrentReviewStatus(); else loadReviews(false);
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
  hideSelUI(); clearMerge();        // 切换论文：收起划词 UI 并清空合并缓冲
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
  const scale = curScale();
  const vp = page.getViewport({ scale });
  holder.style.width = vp.width + 'px'; holder.style.height = vp.height + 'px';
  const dpr = window.devicePixelRatio || 1;
  const cv = document.createElement('canvas');
  cv.width = Math.floor(vp.width * dpr); cv.height = Math.floor(vp.height * dpr);
  cv.style.width = vp.width + 'px'; cv.style.height = vp.height + 'px';
  holder.appendChild(cv);
  await page.render({ canvasContext: cv.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
  // 文本层：透明、可选中的文字覆盖在 canvas 上 → 支持选中/复制/划词翻译（PDF.js 3.x 需 --scale-factor）
  try {
    const tc = await page.getTextContent();
    const tl = document.createElement('div');
    tl.className = 'textLayer';
    tl.style.setProperty('--scale-factor', scale);
    tl.style.width = vp.width + 'px'; tl.style.height = vp.height + 'px';
    holder.appendChild(tl);
    await pdfjsLib.renderTextLayer({ textContentSource: tc, container: tl, viewport: vp }).promise;
  } catch (e) {}
}
function setZoom(f) { zoomFactor = Math.min(3, Math.max(0.5, f)); hideSelUI(); if (pdfDoc) layoutPages(++renderToken); }

// ====== 划词翻译（PDF 选段 → 小弹窗，调 LLM 译中文；双栏防溢出 + 多选合并）======
let selBubbleEl = null, selPopEl = null, mergeBarEl = null;
let selText = '', selRect = null, mergeBuf = [];
function cleanSelection(s) {
  return (s || '')
    .replace(/-\s*\n\s*/g, '')      // 连字符断词：represen-\ntation → representation
    .replace(/\s*\n\s*/g, ' ')       // 其余硬换行 → 空格
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}
const _R = el => el.getBoundingClientRect();
function _median(arr) { if (!arr.length) return 0; const a = [...arr].sort((x, y) => x - y); return a[Math.floor(a.length / 2)]; }
function nodeSpan(node) { const el = node && (node.nodeType === 1 ? node : node.parentElement); return el ? el.closest('.textLayer span') : null; }
// 找栏间缝（双栏 PDF）：按 x 统计每竖条被多少个 span 覆盖；栏内每行都覆盖 → 计数高，
// 栏间缝只被横跨的标题/作者块覆盖几行 → 计数低。取中部计数最低的连续段为缝。找不到 → 单栏(null)。
function findGutter(spans, pr) {
  const N = 160, pw = pr.width || 1, cnt = new Array(N).fill(0);
  for (const s of spans) { const r = _R(s); let a = Math.floor((r.left - pr.left) / pw * N), b = Math.ceil((r.right - pr.left) / pw * N); a = Math.max(0, a); b = Math.min(N - 1, b); for (let k = a; k <= b; k++) cnt[k]++; }
  const side = []; for (let k = 0; k < N; k++) if ((k < N * 0.3 || k > N * 0.7) && cnt[k] > 0) side.push(cnt[k]);
  const ref = _median(side); if (!ref) return null;                 // 没有明显栏 → 单栏
  const thr = ref * 0.3;
  let best = 0, bestMid = -1, run = 0, start = 0;                     // 中部最长的“低覆盖”连续段
  for (let k = Math.floor(N * 0.33); k <= Math.ceil(N * 0.67); k++) { if (cnt[k] <= thr) { if (run === 0) start = k; run++; if (run > best) { best = run; bestMid = (start + k) / 2; } } else run = 0; }
  return (best >= 2 && bestMid > 0) ? pr.left + (bestMid / N) * pw : null;
}
// 几何抽取：双栏里浏览器原生选择按 DOM 顺序走会溢出到另一栏并带上脚注；这里限定在起点所在栏、
// 起止纵向范围内，并滤掉明显更小字号的脚注。返回 { text, rect, runSpans }，失败回退原生 toString。
function extractSelection() {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  const raw = sel.toString();
  const fallback = () => raw.trim() ? { text: cleanSelection(raw), rect: range.getBoundingClientRect(), runSpans: null } : null;
  const aSpan = nodeSpan(sel.anchorNode), fSpan = nodeSpan(sel.focusNode);
  if (!aSpan || !fSpan) return fallback();
  const tl = aSpan.closest('.textLayer'), page = tl && tl.closest('.pdf-page');
  if (!page) return fallback();
  const all = [...tl.querySelectorAll('span')].filter(s => s.textContent && s.textContent.trim());
  const inSel = all.filter(s => range.intersectsNode(s));
  if (!inSel.length) return fallback();
  const pr = _R(page), gutter = findGutter(all, pr);
  const colOf = s => { if (gutter == null) return 0; const r = _R(s); return (r.left + r.right) / 2 < gutter ? 0 : 1; };
  const medFs = _median(inSel.map(s => _R(s).height)) || 1;
  const aCol = colOf(aSpan), aTop = _R(aSpan).top, fTop = _R(fSpan).top;
  // 单次拖拽 = 单栏：限定在起点所在栏 + 起止纵向范围内（终点即便滑进另一栏，也用其纵坐标定下界，
  // 因为左右栏共用 y 轴），再滤掉明显更小字号的脚注/上标。跨栏续读请用「续选」多选合并。
  const yLo = Math.min(aTop, fTop) - medFs * 0.6, yHi = Math.max(aTop, fTop) + medFs * 1.3;
  const kept = inSel.filter(s => colOf(s) === aCol && _R(s).top >= yLo && _R(s).top <= yHi && _R(s).height >= medFs * 0.7);
  if (!kept.length) return fallback();
  kept.sort((x, y) => { const cx = colOf(x), cy = colOf(y); if (cx !== cy) return cx - cy; const rx = _R(x), ry = _R(y); if (Math.abs(rx.top - ry.top) > medFs * 0.5) return rx.top - ry.top; return rx.left - ry.left; });
  let out = '', lastTop = null, lastCol = null;            // 换行处插 \n，交给 cleanSelection 去连字符 + 重排
  for (const s of kept) { const r = _R(s), c = colOf(s); if (lastTop !== null && (c !== lastCol || r.top - lastTop > medFs * 0.5)) out += '\n'; out += s.textContent; lastTop = r.top; lastCol = c; }
  const rects = kept.map(_R);
  const rect = { left: Math.min(...rects.map(r => r.left)), top: Math.min(...rects.map(r => r.top)), right: Math.max(...rects.map(r => r.right)), bottom: Math.max(...rects.map(r => r.bottom)) };
  rect.width = rect.right - rect.left; rect.height = rect.bottom - rect.top;
  return { text: cleanSelection(out), rect, runSpans: kept };
}
function ensureSelEls() {
  if (!selBubbleEl) {
    selBubbleEl = document.createElement('div');
    selBubbleEl.className = 'sel-bubble';
    selBubbleEl.innerHTML = '<button class="sel-b-tr">🌐 翻译</button><button class="sel-b-add" title="加入合并缓冲，可继续选其它段落；也可按住 Alt 选中直接加入">➕ 续选</button>';
    selBubbleEl.addEventListener('mousedown', e => e.preventDefault());   // 点按钮别清掉选区
    selBubbleEl.querySelector('.sel-b-tr').addEventListener('click', () => { hideSelBubble(); doTranslate(selText, selRect); });
    selBubbleEl.querySelector('.sel-b-add').addEventListener('click', () => { addToMerge(selText); hideSelBubble(); clearNativeSelection(); });
    document.body.appendChild(selBubbleEl);
  }
  if (!selPopEl) { selPopEl = document.createElement('div'); selPopEl.className = 'sel-pop'; document.body.appendChild(selPopEl); }
  if (!mergeBarEl) { mergeBarEl = document.createElement('div'); mergeBarEl.className = 'sel-merge'; mergeBarEl.addEventListener('mousedown', e => e.preventDefault()); document.body.appendChild(mergeBarEl); }
}
function hideSelBubble() { if (selBubbleEl) selBubbleEl.style.display = 'none'; }
function hideSelUI() { hideSelBubble(); if (selPopEl) selPopEl.style.display = 'none'; }   // 不动合并缓冲条
function clearNativeSelection() { try { const s = window.getSelection(); if (s) s.removeAllRanges(); } catch (e) {} }
function reselectRun(run) {   // 把原生选区收紧到干净的同栏连续 run（高亮与译文一致，不再溢出）
  try { const r = document.createRange(); r.setStartBefore(run[0]); r.setEndAfter(run[run.length - 1]); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); selRect = r.getBoundingClientRect(); } catch (e) {}
}
function onPdfMouseUp(e) {
  const alt = !!(e && e.altKey);
  setTimeout(() => {                  // 等浏览器把 selection 更新好
    const got = extractSelection();
    if (!got || !got.text || got.text.length < 2) { hideSelBubble(); return; }
    selText = got.text; selRect = got.rect;
    if (alt) { addToMerge(selText); clearNativeSelection(); hideSelBubble(); return; }   // 按住 Alt：直接进合并缓冲
    if (got.runSpans && got.runSpans.length) reselectRun(got.runSpans);
    showSelBubble();
  }, 0);
}
function showSelBubble() {
  ensureSelEls();
  if (selPopEl) selPopEl.style.display = 'none';
  const b = selBubbleEl; b.style.display = 'flex';
  const bw = b.offsetWidth || 132, bh = b.offsetHeight || 30;
  let x = selRect.left + selRect.width / 2 - bw / 2, y = selRect.bottom + 6;
  x = Math.max(8, Math.min(x, innerWidth - bw - 8));
  if (y + bh > innerHeight - 8) y = Math.max(8, selRect.top - bh - 6);
  b.style.left = x + 'px'; b.style.top = y + 'px';
}
// 合并缓冲：累积多段，最后一并翻译（跨栏续读 / 跳过脚注用）
function addToMerge(t) { t = (t || '').trim(); if (t) { mergeBuf.push(t); renderMergeBar(); } }
function clearMerge() { mergeBuf = []; renderMergeBar(); }
function renderMergeBar() {
  ensureSelEls();
  if (!mergeBuf.length) { mergeBarEl.style.display = 'none'; return; }
  mergeBarEl.style.display = 'flex';
  mergeBarEl.innerHTML = `<span class="sel-merge-n">合并翻译 · 已选 ${mergeBuf.length} 段</span><button class="sel-merge-go">翻译</button><button class="sel-merge-clr">清空</button>`;
  mergeBarEl.querySelector('.sel-merge-go').onclick = () => doTranslate(mergeBuf.join('\n\n'), mergeBarEl.getBoundingClientRect());
  mergeBarEl.querySelector('.sel-merge-clr').onclick = clearMerge;
}
function positionPopupAt(rect) {
  const calc = window.SelectionPopover && window.SelectionPopover.calculatePopupLayout;
  const layout = calc
    ? calc(rect, { width: innerWidth, height: innerHeight }, { width: 420, margin: 8, gap: 8, maxHeightRatio: 0.72 })
    : { left: Math.max(8, Math.min(rect.left, innerWidth - 348)), top: rect.bottom + 8, bottom: 'auto', width: 340, maxHeight: Math.floor(innerHeight * 0.56) };
  selPopEl.style.width = layout.width + 'px';
  selPopEl.style.maxHeight = layout.maxHeight + 'px';
  selPopEl.style.left = layout.left + 'px';
  selPopEl.style.top = layout.top === 'auto' ? 'auto' : layout.top + 'px';
  selPopEl.style.bottom = layout.bottom === 'auto' ? 'auto' : layout.bottom + 'px';
}
async function doTranslate(text, rect) {
  ensureSelEls();
  text = (text || '').trim(); if (!text) return;
  positionPopupAt(rect || { left: innerWidth / 2 - 170, top: 80, bottom: 120 });
  selPopEl.style.display = 'flex';
  const srcHtml = `<div class="sel-pop-src">${esc(text.length > 180 ? text.slice(0, 180) + '…' : text)}</div>`;
  selPopEl.innerHTML = srcHtml + '<div class="sel-pop-body"><span class="sel-spin"></span>翻译中…</div>';
  try {
    const r = await (await fetch('/api/translate-text', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })).json();
    if (!selPopEl || selPopEl.style.display === 'none') return;     // 期间被关掉了
    if (r.ok && r.text) {
      selPopEl.innerHTML = srcHtml + '<div class="sel-pop-trans markdown"></div><div class="sel-pop-foot"><button class="sel-copy mini">复制译文</button></div>';
      renderMd(selPopEl.querySelector('.sel-pop-trans'), r.text);
      const cp = selPopEl.querySelector('.sel-copy');
      cp.onclick = () => { try { navigator.clipboard.writeText(r.text); cp.textContent = '已复制 ✓'; setTimeout(() => cp.textContent = '复制译文', 1200); } catch (e) {} };
    } else {
      selPopEl.innerHTML = srcHtml + '<div class="sel-pop-body err">翻译失败：' + esc(r.error || '未知错误') + '</div>';
    }
  } catch (e) {
    if (selPopEl) selPopEl.innerHTML = srcHtml + '<div class="sel-pop-body err">翻译失败：' + esc(String(e)) + '</div>';
  }
}

// ====== 交互绑定 ======
// ====== 采集页：后台任务 + 定时（P5） ======
let jobsTimer = null;
const JOB_STATUS = { pending: ['待命', 'st-pending'], running: ['运行中', 'st-running'], review: ['待确认', 'st-review'], done: ['已完成', 'st-done'], failed: ['失败', 'st-failed'] };
async function renderJobs() { renderSchedules(); await refreshJobs(); }
async function refreshJobs() {
  let jobs = [];
  try { jobs = await (await fetch('/api/jobs')).json(); } catch (e) {}
  const box = $('#jobsList'); if (!box) return jobs;
  $('#jobsCount').textContent = jobs.length ? `共 ${jobs.length}` : '';
  box.innerHTML = jobs.length ? jobs.map(jobCardHTML).join('') : '<div class="placeholder">还没有采集任务。在上面发起一个后台采集吧。</div>';
  document.querySelectorAll('#jobsList .job-del').forEach(b => b.onclick = () => deleteJob(b.dataset.id));
  document.querySelectorAll('#jobsList .job-review').forEach(b => b.onclick = () => toggleJobCands(b.dataset.id));
  if (jobs.some(j => j.status === 'running' || j.status === 'pending')) startJobsPoll();
  return jobs;
}
function jobCardHTML(j) {
  const [label, cls] = JOB_STATUS[j.status] || [j.status, ''];
  const yr = (j.year_from && j.year_to) ? `${j.year_from}-${j.year_to}` : '';
  const busy = (j.status === 'running' || j.status === 'pending');
  return `<div class="job-card" data-id="${j.id}" data-status="${j.status}">
    <div class="job-top">
      <div class="job-main"><span class="job-q">${esc(j.query)}</span>
        <span class="job-meta">${esc(j.venues || '')}${yr ? ' · ' + yr : ''}${j.only_a ? ' · 只采A' : ''}${j.schedule_id ? ' · ⏱定时' : ''}</span></div>
      <span class="job-badge ${cls}">${busy ? '<span class="ingd-spin"></span>' : ''}${label}</span>
    </div>
    <div class="job-stats">找到 ${j.found || 0} · 待确认 <b>${j.pending || 0}</b> · 已入库 ${j.added || 0} · ${fmtTime(j.created_at)}</div>
    <div class="job-actions">
      ${j.status === 'review' && j.pending > 0 ? `<button class="mini primary job-review" data-id="${j.id}">查看 ${j.pending} 篇待确认 →</button>` : ''}
      <button class="mini job-del" data-id="${j.id}">删除</button>
    </div>
    <div class="job-cands hidden" data-id="${j.id}"></div>
  </div>`;
}
async function toggleJobCands(id) {
  const box = document.querySelector(`#jobsList .job-cands[data-id="${id}"]`); if (!box) return;
  if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = '<div class="placeholder">加载候选…</div>';
  let d = {};
  try { d = await (await fetch('/api/jobs/detail?id=' + id)).json(); } catch (e) {}
  const cands = d.candidates || [];
  if (!cands.length) { box.innerHTML = '<div class="placeholder">没有待确认候选。</div>'; return; }
  box.dataset.cands = JSON.stringify(cands);
  box.innerHTML = `<div class="jc-head"><label class="cand-all"><input type="checkbox" class="jc-all" checked> 全选</label>
      <span class="jc-tip">勾选要入库的，其余忽略</span>
      <button class="mini primary jc-confirm" data-id="${id}">确认入库 →</button></div>
    <div class="jc-list">${cands.map((c, i) => jobCandHTML(c, i)).join('')}</div>
    <pre class="jc-log hidden"></pre>`;
  const all = box.querySelector('.jc-all');
  all.onchange = () => box.querySelectorAll('.jc-ck').forEach(ck => ck.checked = all.checked);
  box.querySelector('.jc-confirm').onclick = () => confirmJob(id, box);
}
function jobCandHTML(c, i) {
  const rel = c.relevance != null ? Math.round(c.relevance * 100) : 0;
  const vn = normVenue(c.venue) || '';
  return `<label class="cand">
    <input type="checkbox" class="jc-ck" data-i="${i}" checked />
    <div class="cand-main">
      <div class="cand-title">${esc(c.title)}</div>
      <div class="cand-meta"><span class="venue v-${vn}">${esc(vn || '—')} ${c.year || ''}</span>${ccfBadge(c.ccf)} · ${esc(c.type || '')}${c.topic ? ' · ' + esc(c.topic) : ''}</div>
    </div>
    <div class="cand-rel" title="相关度 ${rel}%"><div class="cand-rel-track"><div class="cand-rel-bar" style="width:${rel}%"></div></div><span>${rel}</span></div>
  </label>`;
}
async function confirmJob(id, box) {
  const cands = JSON.parse(box.dataset.cands || '[]');
  const picks = [...box.querySelectorAll('.jc-ck:checked')].map(ck => cands[+ck.dataset.i]).filter(Boolean);
  if (!picks.length) { alert('请勾选要入库的论文'); return; }
  const log = box.querySelector('.jc-log'); log.classList.remove('hidden'); log.textContent = `入库 ${picks.length} 篇中…`;
  const btn = box.querySelector('.jc-confirm'); btn.disabled = true;
  try {
    await streamNDJSON('/api/jobs/confirm', { jobId: +id, candidates: picks }, (ev) => {
      if (ev.type === 'progress') { log.textContent += '\n' + ev.line; log.scrollTop = log.scrollHeight; }
      else if (ev.type === 'done') { log.textContent += `\n✅ 完成，新增 ${ev.added} 篇`; }
    });
    await reloadPapers(); renderManage();
    await refreshJobs();
  } catch (e) { log.textContent = '失败: ' + e; }
  finally { btn.disabled = false; }
}
async function deleteJob(id) {
  if (!confirm('删除这个采集任务及其待确认候选？')) return;
  await fetch('/api/jobs/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: +id }) });
  refreshJobs();
}
async function startJob() {
  const q = $('#jbQuery').value.trim(); if (!q) { alert('请填写检索方向'); return; }
  const sources = [...document.querySelectorAll('#jobs .ib-opts .src-chip.active')].map(c => c.dataset.src);
  if (!sources.length) { alert('请至少选择一个数据源'); return; }
  const btn = $('#jbStartBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '提交中…';
  try {
    const r = await (await fetch('/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, sources, years: $('#jbYears').value.trim(), max: parseInt($('#jbMax').value) || 15, minRelevance: parseFloat($('#jbRel').value), onlyA: $('#jbOnlyA').checked })
    })).json();
    if (!r.ok) { alert('发起失败：' + (r.error || '')); return; }
    $('#jbQuery').value = '';
    await refreshJobs(); startJobsPoll();
  } catch (e) { alert('发起失败：' + e); }
  finally { btn.disabled = false; btn.textContent = old; }
}
function startJobsPoll() {
  stopJobsPoll();
  jobsTimer = setInterval(async () => {
    if (currentView !== 'jobs') { stopJobsPoll(); return; }
    if (document.querySelector('#jobsList .job-cands:not(.hidden)')) return;   // 正在看候选，别打断
    const jobs = await refreshJobs();
    if (!jobs || !jobs.some(j => j.status === 'running' || j.status === 'pending')) stopJobsPoll();
  }, 3000);
}
function stopJobsPoll() { if (jobsTimer) { clearInterval(jobsTimer); jobsTimer = null; } }

// ---- 定时任务 ----
async function renderSchedules() {
  let list = [];
  try { list = await (await fetch('/api/schedules')).json(); } catch (e) {}
  const box = $('#schList'); if (!box) return;
  box.innerHTML = list.length ? list.map(s => `<div class="sch-item" data-id="${s.id}">
    <label class="sch-toggle" title="${s.enabled ? '已启用，点击暂停' : '已暂停，点击启用'}"><input type="checkbox" class="sch-en" data-id="${s.id}" ${s.enabled ? 'checked' : ''}><span class="sch-slider"></span></label>
    <div class="sch-main"><span class="sch-q">${esc(s.query)}</span><span class="sch-meta">每 ${s.every_days} 天 · ${esc(s.sources || '')}${s.only_a ? ' · 只采A' : ''}${s.next_run ? ' · 下次 ' + fmtTime(s.next_run) : ''}</span></div>
    <button class="mini sch-del" data-id="${s.id}">删除</button></div>`).join('') : '<div class="sch-empty">还没有定时任务。添加后系统会按周期自动采集。</div>';
  box.querySelectorAll('.sch-en').forEach(c => c.onchange = () => schToggle(c.dataset.id, c.checked));
  box.querySelectorAll('.sch-del').forEach(b => b.onclick = () => schDelete(b.dataset.id));
}
async function schAdd() {
  const q = $('#schQuery').value.trim(); if (!q) { alert('请填写定时采集的方向'); return; }
  const days = parseInt($('#schDays').value) || 7;
  const r = await (await fetch('/api/schedules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: q, sources: ['semanticscholar', 'openalex', 'dblp'], years: '2024-2026', everyDays: days, onlyA: $('#schOnlyA').checked })
  })).json();
  if (!r || !r.ok) { alert('添加失败：' + ((r && r.error) || '')); return; }
  $('#schQuery').value = '';
  renderSchedules();
}
async function schToggle(id, enabled) {
  await fetch('/api/schedules/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: +id, enabled }) });
}
async function schDelete(id) {
  if (!confirm('删除这个定时任务？')) return;
  await fetch('/api/schedules/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: +id }) });
  renderSchedules();
}

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
  { const r = $('#ebRun'), s = $('#ebStop'); if (r) r.onclick = runExplainBatch; if (s) s.onclick = () => { if (ebAbort) ebAbort.abort(); }; }
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
  { const ps = $('#pdfScroll'); if (ps) { ps.addEventListener('mouseup', onPdfMouseUp); ps.addEventListener('scroll', hideSelBubble, { passive: true }); } }
  document.addEventListener('mousedown', (e) => {     // 点选区气泡/弹窗/合并条以外 → 收起划词翻译
    if (selBubbleEl && selBubbleEl.contains(e.target)) return;
    if (selPopEl && selPopEl.contains(e.target)) return;
    if (mergeBarEl && mergeBarEl.contains(e.target)) return;
    hideSelUI();
  });
  $('#ingSearchBtn').onclick = () => { if ($('#ingExpand').checked) editQueries(); else runSearch(null); };
  $('#ingExpand').onchange = syncSearchBtnLabel; syncSearchBtnLabel();
  $('#ingEditBtn').onclick = editQueries;
  $('#ingSearchWithBtn').onclick = () => runSearch(currentQueries());
  $('#ingQueryAdd').onkeydown = (e) => { if (e.key === 'Enter' && e.target.value.trim()) { const a = currentQueries() || []; a.push(e.target.value.trim()); e.target.value = ''; renderQueryChips(a); } };
  $('#ingHistClear').onclick = () => { try { localStorage.removeItem(HIST_KEY); } catch (e) {} renderHist(); };
  renderHist();
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
  $('#normVenueBtn').onclick = normVenues;
  $('#dlPdfsBtn').onclick = downloadPdfs;
  $('#settingsBtn').onclick = openSettingsModal;
  $('#setClose').onclick = closeSettingsModal;
  $('#settingsModal').onclick = (e) => { if (e.target.id === 'settingsModal') closeSettingsModal(); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeSettingsModal(); closePaperModal(); hideSelUI(); } });
  document.querySelectorAll('.ib-opts .src-chip').forEach(c => c.onclick = () => c.classList.toggle('active'));
  $('#jbStartBtn').onclick = startJob;
  $('#jbQuery').onkeydown = (e) => { if (e.key === 'Enter') startJob(); };
  $('#schAddBtn').onclick = schAdd;
  $('#jobsRefresh').onclick = () => refreshJobs();
  document.querySelectorAll('#libSrcFilter .fchip').forEach(c => c.onclick = () => {
    document.querySelectorAll('#libSrcFilter .fchip').forEach(x => x.classList.remove('active'));
    c.classList.add('active'); manageSrc = c.dataset.src; renderManage();
  });
  $('#themeBtn').onclick = toggleTheme;
  $('#toggleLeft').onclick = () => togglePane('hide-left');
  $('#toggleRight').onclick = () => togglePane('hide-right');
  $('#stubLeft').onclick = () => togglePane('hide-left');
  $('#stubRight').onclick = () => togglePane('hide-right');
  // 阅读侧边栏筛选
  const sideSearch = $('#sideSearch'), sideClear = $('#sideClear');
  if (sideSearch) sideSearch.oninput = (e) => { sideQ = e.target.value.trim(); sideClear.classList.toggle('hidden', !sideQ); renderSidebar(); };
  if (sideClear) sideClear.onclick = () => { sideQ = ''; sideSearch.value = ''; sideClear.classList.add('hidden'); renderSidebar(); sideSearch.focus(); };
  document.querySelectorAll('#sideStatusSeg button').forEach(b => b.onclick = () => {
    sideStatus = b.dataset.st;
    document.querySelectorAll('#sideStatusSeg button').forEach(x => x.classList.toggle('active', x === b));
    renderSidebar();
  });
  const sideFavBtn = $('#sideFav');
  if (sideFavBtn) sideFavBtn.onclick = () => { sideFav = !sideFav; sideFavBtn.classList.toggle('on', sideFav); sideFavBtn.textContent = sideFav ? '★' : '☆'; renderSidebar(); };
  let rzT; window.addEventListener('resize', () => { clearTimeout(rzT); rzT = setTimeout(() => { if (pdfDoc && currentView === 'read') layoutPages(++renderToken); [chProgress, chDir, chVenue, chTrend, chTree, chCited, chCite].forEach(c => c && c.resize()); }, 200); });
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
  if (status === '已理解' || reviewData) await loadReviews(currentView === 'review');
  else renderCurrentReviewStatus();
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
    const meta = [`<span class="venue v-${p.venue}">${p.venue} ${p.year}</span>${ccfBadge(p.ccf)}${pdfBadge(p.hasPdf)}`, p.type];
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
async function normVenues() {
  const btn = $('#normVenueBtn'), hint = $('#normVenueHint');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '规整中…';
  hint.textContent = '大模型规整会议名中…';
  try {
    await streamNDJSON('/api/norm-venues', {}, (ev) => {
      if (ev.type === 'progress') {
        if (ev.line.startsWith('STAGE::apply')) hint.textContent = '写入中…';
      } else if (ev.type === 'result') {
        if (ev.ok) {
          const n = Object.keys(ev.mapping || {}).length;
          hint.textContent = n ? `✅ 规整 ${n} 个会议名 · ${ev.changed} 篇` : '✅ 已是规范，无需改动';
          reloadPapers();
        } else hint.textContent = '失败：' + (ev.error || '未知');
      }
    });
 } catch (e) { hint.textContent = '失败：' + e; }
 finally { btn.disabled = false; btn.textContent = old; }
}
async function downloadPdfs() {
  const btn = $('#dlPdfsBtn'), hint = $('#dlPdfsHint');
  btn.disabled = true; const old = btn.textContent; btn.textContent = '下载中…';
  hint.textContent = '正在扫描库内缺 PDF 的论文…';
  let ok = 0, skip = 0, fail = 0, total = 0;
  try {
    await streamNDJSON('/api/download-pdfs', {}, (ev) => {
      if (ev.type === 'progress') {
        let m;
        if ((m = /^TOTAL::(\d+)/.exec(ev.line))) { total = +m[1]; hint.textContent = `共 ${total} 篇缺 PDF，逐一下载中…`; }
        else if ((m = /^PDFSTART::(.*)/.exec(ev.line))) hint.textContent = `下载中：${m[1]}`;
        else if ((m = /^PDFOK::(.*)/.exec(ev.line))) { ok++; hint.textContent = `✅ ${m[1]}（${ok}/${total}）`; }
        else if (/^PDFNOURL::/.test(ev.line)) { skip++; hint.textContent = `跳过（无 PDF 地址） ${ok+skip}/${total}`; }
        else if ((m = /^PDFERR::(.*)/.exec(ev.line))) { fail++; hint.textContent = `❌ ${m[1]}`; }
      } else if (ev.type === 'result') {
        if (ev.ok) hint.textContent = `✅ 完成：下载 ${ev.downloaded} · 跳过 ${ev.skipped} · 失败 ${ev.failed}`;
        else hint.textContent = '失败：' + (ev.error || '');
      }
    });
  } catch (e) { hint.textContent = '失败：' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
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
// ====== 采集检索历史（localStorage） ======
const HIST_KEY = 'paperstudy.searchHistory', HIST_MAX = 12;
function loadHist() { try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch (e) { return []; } }
function saveHist(a) { try { localStorage.setItem(HIST_KEY, JSON.stringify(a.slice(0, HIST_MAX))); } catch (e) {} }
function pushHist(q, queries) {
  q = (q || '').trim(); if (!q) return;
  const a = loadHist().filter(e => e.q !== q);
  a.unshift({ q, queries: (queries && queries.length) ? queries : undefined, ts: Date.now() });
  saveHist(a); renderHist();
}
function renderHist() {
  const wrap = $('#ingHistory'), box = $('#ingHistChips'); if (!wrap || !box) return;
  const a = loadHist();
  if (!a.length) { wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  box.innerHTML = a.map((e, i) => `<span class="ih-chip" data-i="${i}" title="${esc(e.q)}${e.queries ? ' · ' + e.queries.length + ' 个检索词（点击恢复）' : ''}"><span class="ih-t">${esc(e.q)}</span><b class="ih-x" data-i="${i}" title="从历史删除">×</b></span>`).join('');
  box.querySelectorAll('.ih-chip').forEach(c => c.onclick = (ev) => {
    if (ev.target.classList.contains('ih-x')) return;
    const e = loadHist()[+c.dataset.i]; if (!e) return;
    $('#ingQuery').value = e.q;
    if (e.queries && e.queries.length) { renderQueryChips(e.queries); $('#ingQueriesBox').classList.remove('hidden'); }
  });
  box.querySelectorAll('.ih-x').forEach(x => x.onclick = (ev) => {
    ev.stopPropagation();
    const a2 = loadHist(); a2.splice(+x.dataset.i, 1); saveHist(a2); renderHist();
  });
}
function syncSearchBtnLabel() {
  const b = $('#ingSearchBtn'), exp = $('#ingExpand') && $('#ingExpand').checked;
  if (b) { b.textContent = exp ? '扩展检索词 →' : '检索 →'; b.title = exp ? '先用大模型扩展检索词并预览，确认/编辑后再点「用这些词检索」正式检索' : '直接用方向原词检索'; }
  const edit = $('#ingEditBtn'); if (edit) edit.style.display = exp ? 'none' : '';
}
async function editQueries() {
  const q = $('#ingQuery').value.trim(); if (!q) { alert('请先填写检索方向'); return; }
  const btn = $('#ingSearchBtn'), old = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '扩展中…'; }
  $('#ingStages').classList.add('hidden'); $('#candPanel').classList.add('hidden');
  $('#ingQueriesBox').classList.remove('hidden');
  $('#ingQueryChips').innerHTML = '<span class="placeholder">生成中…</span>';
  $('#ingQueriesBox').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  try {
    const j = await (await fetch('/api/expand', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: q, expandN: 6 }) })).json();
    const qs = (j.queries && j.queries.length) ? j.queries : [q];
    renderQueryChips(qs); pushHist(q, qs);
  } catch (e) { renderQueryChips([q]); pushHist(q, [q]); }
  finally { if (btn) { btn.disabled = false; btn.textContent = old; } }
}
let srcCounts = {}, keptN = null, errN = 0;
const SRC_LABEL = { arxiv: 'arXiv', semanticscholar: 'S2', openalex: 'OpenAlex', dblp: 'DBLP' };
function setDetail(main, sub) {
  if (main != null && $('#ingdMain')) $('#ingdMain').textContent = main;
  if (sub != null && $('#ingdSub')) $('#ingdSub').innerHTML = sub;
}
function srcSummary() { return Object.entries(srcCounts).map(([k, v]) => `${SRC_LABEL[k] || k} ${v}`).join(' · '); }
function updCount() { const el = $('#ingdCount'); if (el) el.textContent = keptN != null ? `保留 ${keptN}${errN ? ` · ${errN} 跳过` : ''}` : ''; }
function handleProgress(line) {
  if (line.startsWith('STAGE::')) {
    const s = line.slice(7);
    if (s === 'search') { setStage('expand', 'done'); setStage('search', 'active'); setDetail('正在检索数据源…', ''); }
    else if (s === 'classify') { setStage('search', 'done'); setStage('classify', 'active'); keptN = 0; errN = 0; updCount(); setDetail('分类打分中…', srcSummary()); }
  } else if (line.startsWith('QUERIES::')) {
    try { renderQueryChips(JSON.parse(line.slice(9))); $('#ingQueriesBox').classList.remove('hidden'); } catch (e) {}
  } else if (line.startsWith('SRC::')) {
    const p = line.split('::'); srcCounts[p[1]] = p[2]; setDetail('正在检索数据源…', srcSummary());
  } else if (line.startsWith('SRCERR::')) {
    const p = line.split('::'); setDetail(null, (srcSummary() ? srcSummary() + ' · ' : '') + `<b class="ingd-warn">${SRC_LABEL[p[1]] || p[1]} 失败</b>`);
  } else if (line.startsWith('FOUND::')) {
    const n = line.slice(7); $('#stFound').textContent = ' ' + n; setDetail(`共 ${n} 篇候选 · 开始分类打分`, srcSummary());
  } else if (line.startsWith('DOING::')) {
    const rest = line.slice(7), m = rest.indexOf('::'); const idx = rest.slice(0, m), title = rest.slice(m + 2);
    $('#stCls').textContent = ' ' + idx; setDetail(`分类打分 · 第 ${idx} 篇`, `《${esc(title)}》`);
  } else if (line.startsWith('KEPT::')) {
    keptN = line.slice(6); updCount();
  } else if (line.startsWith('CLSERR::')) {
    errN++; updCount();
  }
}
async function runSearch(queries) {
  const sources = [...document.querySelectorAll('#manage .ib-opts .src-chip.active')].map(c => c.dataset.src);
  const q = $('#ingQuery').value.trim();
  if (!q) { alert('请填写检索方向'); return; }
  if (!sources.length) { alert('请至少选择一个数据源'); return; }
  pushHist(q, queries && queries.length ? queries : null);
  candidates = [];
  $('#candPanel').classList.add('hidden');
  $('#ingStages').classList.remove('hidden');
  setStage('expand', queries ? 'done' : 'active'); setStage('search', ''); setStage('classify', '');
  $('#stFound').textContent = ''; $('#stCls').textContent = '';
  srcCounts = {}; keptN = null; errN = 0;
  $('#ingDetail').classList.remove('hidden'); setDetail(queries ? '正在检索数据源…' : '扩展检索词中…', ''); updCount();
  const btn = $('#ingSearchBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '检索中…';
  try {
    await streamNDJSON('/api/search', {
      query: q, sources, years: $('#ingYears').value.trim(),
      max: parseInt($('#ingMax').value) || 10, minRelevance: parseFloat($('#ingRel').value),
      expand: queries ? false : $('#ingExpand').checked, onlyA: $('#ingOnlyA').checked, queries: queries || null
    }, (ev) => {
      if (ev.type === 'progress') handleProgress(ev.line);
      else if (ev.type === 'result') { candidates = ev.candidates || []; renderCandidates(); }
    });
  } catch (e) { alert('检索失败: ' + e); }
  finally { btn.disabled = false; btn.textContent = old; }
}
function renderCandidates() {
  setStage('expand', 'done'); setStage('search', 'done'); setStage('classify', 'done');
  $('#ingDetail').classList.add('hidden');
  $('#candPanel').classList.remove('hidden');
  const fresh = candidates.filter(c => !c.in_library).length;
  $('#candCount').textContent = `找到 ${candidates.length} 篇 · ${fresh} 篇新`;
  const SRC_SHORT = { dblp: 'DBLP', semanticscholar: 'S2', openalex: 'OpenAlex' };
  $('#candList').innerHTML = candidates.map((c, i) => {
    const rel = c.relevance != null ? Math.round(c.relevance * 100) : 0;
    const vn = normVenue(c.venue) || '';
    const vv = c._verify;
    const vb = vv ? (
      vv.skipped ? `<b class="vbadge src" title="${vv.note || ''}">源自${SRC_SHORT[vv.source_of_truth] || vv.source_of_truth}</b>`
        : vv.matched ? `<b class="vbadge ok" title="权威来源：${SRC_SHORT[vv.source_of_truth] || vv.source_of_truth}">✓已核实${vv.changed ? '·已更正' : ''}</b>`
          : `<b class="vbadge miss" title="${vv.note || ''}">仅预印本</b>`) : '';
    return `<label class="cand ${c.in_library ? 'in-lib' : ''}">
      <input type="checkbox" class="cand-ck" data-i="${i}" ${c.in_library ? 'disabled' : 'checked'} />
      <div class="cand-main">
        <div class="cand-title">${c.title}</div>
        <div class="cand-meta"><span class="venue v-${vn}">${vn || '—'} ${c.year || ''}</span>${ccfBadge(c.ccf)}${vb ? ' ' + vb : ''} · ${c.type || ''}${c.topic ? ' · ' + c.topic : ''}${c.in_library ? ' · <b class="inlib-tag">已在库</b>' : ''}</div>
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
          if (v.ccf !== undefined) candidates[i].ccf = v.ccf;
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
  buildSideYears();
  renderSidebar();
  renderHome();
}

// ====== 一键生成讲解（批量，逐篇通读本地 PDF 全文，与单篇「读PDF全文」逻辑一致）======
let ebAbort = null;
async function refreshExplainBatch() {
  const runBtn = $('#ebRun'); if (!runBtn || ebAbort) return;   // 正在跑时不要覆盖按钮态
  try {
    const r = await (await fetch('/api/explain-batch')).json();
    const n = r.withPdf || 0, tip = $('#ebTip');
    runBtn.disabled = n === 0;
    runBtn.textContent = n > 0 ? `生成缺失讲解（${n} 篇）` : '讲解已齐全 ✓';
    if (tip) tip.textContent = (r.pending || 0) === 0
      ? '所有论文都已有讲解。'
      : `当前 ${r.pending} 篇缺讲解，其中 ${n} 篇有本地 PDF 可生成` + (r.noPdf ? `、${r.noPdf} 篇无 PDF 将跳过` : '') +
        '。逐篇通读全文 PDF 生成（与单篇「读PDF全文」一致），可随时停止，已生成的自动保存。';
  } catch (e) {}
}
async function runExplainBatch() {
  const runBtn = $('#ebRun'), stopBtn = $('#ebStop'), prog = $('#ebProgress');
  const fill = $('#ebBarFill'), stat = $('#ebStat'), now = $('#ebNow');
  const limit = parseInt(($('#ebLimit') || {}).value) || 0;
  runBtn.disabled = true; const oldTxt = runBtn.textContent; runBtn.textContent = '生成中…';
  stopBtn.classList.remove('hidden'); prog.classList.remove('hidden');
  fill.style.width = '0%'; stat.textContent = '准备中…'; now.textContent = '';
  let total = 0, done = 0, fail = 0, skip = 0;
  const setBar = (i) => { fill.style.width = (total ? Math.round(i / total * 100) : 0) + '%'; };
  const onEvent = (ev) => {
    if (ev.type === 'progress') {
      const L = ev.line; let m;
      if ((m = /^BATCH::total::(\d+)::skip::(\d+)/.exec(L))) {
        total = +m[1]; skip = +m[2];
        stat.textContent = `共 ${total} 篇待生成` + (skip ? `，${skip} 篇无 PDF 已跳过` : '');
      } else if ((m = /^ITEM::(\d+)::(\d+)::start::[^:]*::(.*)$/.exec(L))) {
        now.textContent = `（${m[1]}/${total || m[2]}）正在：${m[3]}`;
      } else if (/^STAGE::pdf/.test(L)) {
        now.textContent = now.textContent.replace(/ —— .*$/, '') + ' —— 读取 PDF 全文…';
      } else if (/^STAGE::generate/.test(L)) {
        now.textContent = now.textContent.replace(/ —— .*$/, '') + ' —— 大模型撰写中…';
      } else if ((m = /^ITEM::(\d+)::\d+::done::/.exec(L))) {
        done++; setBar(+m[1]); stat.textContent = `已完成 ${done} / ${total}` + (fail ? `，失败 ${fail}` : '');
      } else if ((m = /^ITEM::(\d+)::\d+::fail::/.exec(L))) {
        fail++; setBar(+m[1]); stat.textContent = `已完成 ${done} / ${total}，失败 ${fail}`;
      } else if (/^STAGE::reindex/.test(L)) {
        stat.textContent = `已完成 ${done} / ${total}，正在重建语义索引…`; now.textContent = '';
      }
    } else if (ev.type === 'result') {
      const s = ev.summary || {}; fill.style.width = '100%';
      const nf = (s.failed || []).length, ns = (s.skipped_no_pdf || []).length;
      stat.textContent = ev.ok
        ? `✅ 完成：生成 ${s.done != null ? s.done : done} 篇` + (nf ? `，失败 ${nf}` : '') + (ns ? `，跳过 ${ns}（无 PDF）` : '')
        : `结束：${ev.error || '部分失败'}`;
      now.textContent = '';
    }
  };
  ebAbort = new AbortController();
  try {
    const resp = await fetch('/api/explain-batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit }), signal: ebAbort.signal
    });
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (; ;) {
      const { done: rd, value } = await reader.read(); if (rd) break;
      buf += dec.decode(value, { stream: true }); let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (line) { try { onEvent(JSON.parse(line)); } catch (e) {} }
      }
    }
    if (buf.trim()) { try { onEvent(JSON.parse(buf.trim())); } catch (e) {} }
  } catch (e) {
    if (ebAbort && ebAbort.signal.aborted) stat.textContent = '已停止（已生成的讲解已保存，可再次点击续跑）';
    else stat.textContent = '出错：' + String(e);
  } finally {
    ebAbort = null; stopBtn.classList.add('hidden'); runBtn.textContent = oldTxt;
    await reloadPapers();          // 刷新表格 + 看板讲解覆盖率
    await refreshExplainBatch();   // 刷新「可生成篇数」
  }
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
   $('#setExplainerDir').value = s.explainerDir || '';
   $('#setTranslationDir').value = s.translationDir || '';
   if ($('#setTheme')) $('#setTheme').value = s.researchTheme || '';
   if ($('#setEmbedProvider')) $('#setEmbedProvider').value = s.embedProvider || 'local';
   if ($('#setEmbedBase')) $('#setEmbedBase').value = s.embedApiBase || '';
   if ($('#setEmbedModel')) $('#setEmbedModel').value = s.embedApiModel || '';
   if ($('#setEmbedKey')) $('#setEmbedKey').value = '';
   if ($('#setEmbedTip')) $('#setEmbedTip').textContent = s.hasEmbedKey ? `当前已配置：${s.embedKeyTail}` : '未配置（本地模式无需 Key）';
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
   explainerDir: $('#setExplainerDir').value.trim(),
   translationDir: $('#setTranslationDir').value.trim(),
   researchTheme: $('#setTheme') ? $('#setTheme').value.trim() : '',
   embedProvider: $('#setEmbedProvider') ? $('#setEmbedProvider').value : 'local',
   embedApiBase: $('#setEmbedBase') ? $('#setEmbedBase').value.trim() : '',
   embedApiModel: $('#setEmbedModel') ? $('#setEmbedModel').value.trim() : ''
  };
  const ak = $('#setApiKey').value.trim(), sk = $('#setS2Key').value.trim();
  const ek = $('#setEmbedKey') ? $('#setEmbedKey').value.trim() : '';
  const badKey = (k) => k && !/^[\x21-\x7E]+$/.test(k);   // 合法 key 全是无空格的可见 ASCII；含空格/中文 → 拦截
  if (badKey(ak) || badKey(sk) || badKey(ek)) { const h = $('#setHint'); h.textContent = '⚠ API Key 含空格或非英文字符，看着不像 key，已拦截'; setTimeout(() => h.textContent = '', 4000); return; }
  if (ak) body.apiKey = ak;
  if (sk) body.s2ApiKey = sk;
  if (ek) body.embedApiKey = ek;
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const h = $('#setHint'); h.textContent = '已保存 ✓（下次采集生效）'; setTimeout(() => h.textContent = '', 3000);
  loadSettings();
}
function openSettingsModal() { loadSettings(); $('#settingsModal').classList.remove('hidden'); }
function closeSettingsModal() { $('#settingsModal').classList.add('hidden'); }
