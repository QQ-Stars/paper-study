// ====== 状态 ======
let PAPERS = [];
let current = null;
let curNoteText = '';
let yearFilter = 'all';
let q = '';
let currentView = 'home';
let homeSort = { key: 'year', dir: 1 };
let manageSrc = 'all';
let chProgress = null, chDir = null, chVenue = null;

const $ = (s) => document.querySelector(s);
const md = (t) => (window.marked ? window.marked.parse(t || '') :
  '<pre>' + (t || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) + '</pre>');
const EMPTY_HTML = '<div id="viewerEmpty" class="empty"><div class="empty-ico">📄</div><div class="empty-title">从左侧选择一篇论文</div><div class="empty-sub">建议从 ① HallusionBench 开始</div></div>';

// ====== PDF.js ======
if (window.pdfjsLib) pdfjsLib.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.min.js';
let pdfDoc = null, baseW = 600, zoomFactor = 1, renderToken = 0, io = null;

// ====== 初始化 ======
init();
async function init() {
  PAPERS = await (await fetch('/api/papers')).json();
  applyTheme(localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  if (localStorage.getItem('hide-left') === '1') $('#layout').classList.add('hide-left');
  if (localStorage.getItem('hide-right') === '1') $('#layout').classList.add('hide-right');
  buildYearFilters();
  renderSidebar();
  buildDashShell();
  renderHome();
  bindUI();
  showView('home');
}
function applyTheme(t) { document.documentElement.setAttribute('data-theme', t); const b = $('#themeBtn'); if (b) b.textContent = t === 'dark' ? '☀️' : '🌙'; localStorage.setItem('theme', t); }
function toggleTheme() { applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'); renderHome(); }
function togglePane(cls) { const L = $('#layout'); L.classList.toggle(cls); localStorage.setItem(cls, L.classList.contains(cls) ? '1' : '0'); if (pdfDoc && currentView === 'read') setTimeout(() => layoutPages(++renderToken), 240); }

function buildYearFilters() {
  const years = [...new Set(PAPERS.map(p => p.year))].sort();
  const box = $('#yearFilters'); box.innerHTML = '';
  ['all', ...years].forEach(y => {
    const b = document.createElement('button');
    b.className = 'chip-btn' + (y === 'all' ? ' active' : '');
    b.textContent = y === 'all' ? '全部' : y;
    b.onclick = () => { yearFilter = y; [...box.children].forEach(c => c.classList.remove('active')); b.classList.add('active'); refresh(); };
    box.appendChild(b);
  });
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
  $('#layout').classList.toggle('hidden', v !== 'read');
  if (v === 'home') renderHome();
  if (v === 'manage') { renderManage(); loadSettings(); }
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
    <div class="chart-card">
      <div class="chart-title">概况</div>
      <div class="kpi-big" id="kpiTotal">0</div>
      <div class="kpi-sub" id="kpiSub">篇论文</div>
      <div class="kpi-rows">
        <div><span>顶会发表</span><b id="kpiPub">0</b></div>
        <div><span>arXiv 预印</span><b id="kpiArxiv">0</b></div>
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
  let list = PAPERS.filter(p => (yearFilter === 'all' || p.year === yearFilter));
  if (q) { const k = q.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + p.type + ' ' + (p.topic || '')).toLowerCase().includes(k)); }

  // 表格
  list.sort(cmpHome);
  $('#homeBody').innerHTML = list.map(rowHTML).join('');
  document.querySelectorAll('#homeBody tr').forEach(tr => tr.onclick = () => openPaper(PAPERS.find(x => x.id === tr.dataset.id)));
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
    $('#kpiSub').textContent = '篇论文 ' + (yearFilter === 'all' ? '· 全部' : '· ' + yearFilter);
    $('#kpiPub').textContent = pub;
    $('#kpiArxiv').textContent = total - pub;
    $('#kpiYears').textContent = `${yc('2024')} / ${yc('2025')} / ${yc('2026')}`;

    const dirOrder = [['检测', '#bd5b3a'], ['缓解', '#3f7d5b'], ['机制', '#5b7387'], ['评测', '#b07a2e'], ['定义/其他', '#9a8b72']];
    const dirBucket = (t) => t.includes('检测') ? '检测' : t.includes('缓解') ? '缓解' : t.includes('机制') ? '机制' : t.includes('Bench') ? '评测' : '定义/其他';
    const dc = {}; list.forEach(p => dc[dirBucket(p.type)] = (dc[dirBucket(p.type)] || 0) + 1);
    const dirItems = dirOrder.map(([k, c]) => ({ name: k, value: dc[k] || 0, color: c }));

    const vOrder = [['CV', '#bd5b3a'], ['ML', '#5b7387'], ['NLP', '#3f7d5b'], ['AAAI', '#b07a2e'], ['arXiv', '#8a5a5a']];
    const vBucket = (v) => ['CVPR', 'ICCV', 'ECCV'].includes(v) ? 'CV' : ['ICLR', 'ICML', 'NeurIPS'].includes(v) ? 'ML' : ['ACL', 'EMNLP'].includes(v) ? 'NLP' : v === 'AAAI' ? 'AAAI' : 'arXiv';
    const vc = {}; list.forEach(p => vc[vBucket(p.venue)] = (vc[vBucket(p.venue)] || 0) + 1);
    const vItems = vOrder.map(([k, c]) => ({ name: k, value: vc[k] || 0, color: c }));

    updateCharts({ done, ing, idle, pct, dirItems, vItems });
  }
  updateSummary();
}

function updateCharts(d) {
  if (!window.echarts || !chProgress) return;
  const text = cssVar('--text'), t2 = cssVar('--text-2'), t3 = cssVar('--text-3');
  const surf = cssVar('--surface'), ok = cssVar('--ok'), warn = cssVar('--warn'), idle = cssVar('--idle');
  chProgress.setOption({
    animationDuration: 750, animationDurationUpdate: 600, animationEasing: 'cubicOut',
    title: {
      text: d.pct + '%', subtext: '已理解', left: 'center', top: '38%', itemGap: 3,
      textStyle: { fontSize: 23, fontWeight: 700, color: text }, subtextStyle: { fontSize: 10, color: t3 }
    },
    tooltip: { trigger: 'item', formatter: '{b}：{c} 篇 ({d}%)' },
    series: [{
      type: 'pie', radius: ['62%', '86%'], center: ['50%', '50%'], avoidLabelOverlap: false,
      itemStyle: { borderColor: surf, borderWidth: 2, borderRadius: 5 },
      label: { show: false }, labelLine: { show: false }, emphasis: { scale: true, scaleSize: 6 },
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

function barOption(items, t2, t3) {
  const labels = items.map(i => i.name).reverse();
  const data = items.map(i => ({ value: i.value, itemStyle: { color: i.color, borderRadius: [0, 5, 5, 0] } })).reverse();
  return {
    animationDuration: 750, animationDurationUpdate: 600, animationEasing: 'cubicOut',
    grid: { left: 4, right: 28, top: 6, bottom: 2, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p) => `${p[0].name}：${p[0].value} 篇` },
    xAxis: { type: 'value', max: 'dataMax', axisLabel: { show: false }, splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'category', data: labels, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t2, fontSize: 12 } },
    series: [{ type: 'bar', data, barWidth: '54%', label: { show: true, position: 'right', color: t3, fontSize: 11, formatter: '{c}' } }]
  };
}
function cmpHome(a, b) {
  const k = homeSort.key, d = homeSort.dir;
  let va, vb;
  if (k === 'status') { const m = { '未开始': 0, '学习中': 1, '已理解': 2 }; va = m[a.status]; vb = m[b.status]; }
  else { va = (a[k] || '') + ''; vb = (b[k] || '') + ''; }
  if (va < vb) return -d; if (va > vb) return d;
  return (a.year + '').localeCompare(b.year + '') || ((a.order || 99) - (b.order || 99));
}
function rowHTML(p) {
  const order = p.order ? `<span class="ht-order">${p.order}</span>` : `<span class="ht-order none">·</span>`;
  return `<tr data-id="${p.id}">
    <td>${order}</td>
    <td class="ht-title">${p.title}</td>
    <td><span class="venue v-${p.venue}">${p.venue}</span></td>
    <td>${p.year}</td>
    <td>${p.type}</td>
    <td>${p.topic || ''}</td>
    <td class="ht-time">${fmtTime(p.created_at)}</td>
    <td><span class="ht-status ${p.status}">${p.status}</span></td>
    <td class="ht-note">${p.hasNote ? '✍️' : '<span style="color:#cbd0d8">·</span>'}</td>
  </tr>`;
}

// ====== 阅读视图：左侧列表 ======
function renderSidebar() {
  const side = $('#sidebar'); side.innerHTML = '';
  let list = PAPERS.filter(p => (yearFilter === 'all' || p.year === yearFilter));
  if (q) { const k = q.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + (p.topic || '') + ' ' + (p.type || '')).toLowerCase().includes(k)); }
  const years = [...new Set(list.map(p => p.year))].sort();
  years.forEach(y => {
    const g = document.createElement('div'); g.className = 'year-group';
    const h = document.createElement('div'); h.className = 'year-head';
    h.textContent = y + ' · ' + list.filter(p => p.year === y).length + ' 篇';
    g.appendChild(h);
    list.filter(p => p.year === y)
      .sort((a, b) => (a.order || 99) - (b.order || 99) || a.venue.localeCompare(b.venue))
      .forEach(p => g.appendChild(paperItem(p)));
    side.appendChild(g);
  });
  updateSummary();
}
function paperItem(p) {
  const d = document.createElement('div');
  d.className = 'paper-item' + (current && current.id === p.id ? ' active' : '');
  d.onclick = () => openPaper(p);
  const order = p.order ? `<span class="order-badge">${p.order}</span>` : '';
  d.innerHTML =
    `<div class="pi-top">${order}<div class="pi-title">${p.title}</div><span class="status-dot ${p.status}" title="${p.status}"></span></div>
     <div class="pi-meta"><span class="venue v-${p.venue}">${p.venue} ${p.year}</span><span class="dir">${p.type}${p.topic ? ' · ' + p.topic : ''}</span></div>`;
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
  // 讲解
  const ex = await (await fetch('/api/explainer?id=' + encodeURIComponent(p.id))).text();
  $('#explainerView').innerHTML = md(ex); $('#explainerView').scrollTop = 0;
  // 笔记
  curNoteText = await (await fetch('/api/note?id=' + encodeURIComponent(p.id))).text();
  $('#noteEdit').value = curNoteText;
  $('#notePreview').innerHTML = curNoteText.trim() ? md(curNoteText)
    : '<div class="placeholder">还没有笔记。点「编辑」开始记，或在对话里让我「记录」。</div>';
  showNoteMode('preview');
  // PDF
  renderPdf(p.id);
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
  $('#search').oninput = (e) => { q = e.target.value.trim(); refresh(); };
  document.querySelectorAll('.viewnav button').forEach(b => b.onclick = () => showView(b.dataset.view));
  document.querySelectorAll('#homeTable th[data-sort]').forEach(th => th.onclick = () => {
    const k = th.dataset.sort; if (homeSort.key === k) homeSort.dir *= -1; else homeSort = { key: k, dir: 1 }; renderHome();
  });
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));
  $('#btnEdit').onclick = () => { setSegActive('#tab-note .seg-sm', $('#btnEdit')); showNoteMode('edit'); $('#noteEdit').focus(); };
  $('#btnPreview').onclick = () => { $('#notePreview').innerHTML = md($('#noteEdit').value) || ''; setSegActive('#tab-note .seg-sm', $('#btnPreview')); showNoteMode('preview'); };
  $('#btnSave').onclick = saveNote;
  $('#noteEdit').onblur = () => { if ($('#noteEdit').value !== curNoteText) saveNote(); };
  document.querySelectorAll('#statusSeg button').forEach(b => b.onclick = () => saveStatus(b.dataset.st));
  $('#zoomIn').onclick = () => setZoom(zoomFactor + 0.15);
  $('#zoomOut').onclick = () => setZoom(zoomFactor - 0.15);
  $('#ingBtn').onclick = doIngest;
  $('#mSearch').oninput = renderManage;
  $('#mSort').onchange = renderManage;
  $('#setSaveBtn').onclick = saveSettings;
  $('#setTestBtn').onclick = testLLM;
  document.querySelectorAll('.ib-opts .src-chip').forEach(c => c.onclick = () => c.classList.toggle('active'));
  document.querySelectorAll('#libSrcFilter .fchip').forEach(c => c.onclick = () => {
    document.querySelectorAll('#libSrcFilter .fchip').forEach(x => x.classList.remove('active'));
    c.classList.add('active'); manageSrc = c.dataset.src; renderManage();
  });
  $('#themeBtn').onclick = toggleTheme;
  $('#toggleLeft').onclick = () => togglePane('hide-left');
  $('#toggleRight').onclick = () => togglePane('hide-right');
  let rzT; window.addEventListener('resize', () => { clearTimeout(rzT); rzT = setTimeout(() => { if (pdfDoc && currentView === 'read') layoutPages(++renderToken); [chProgress, chDir, chVenue].forEach(c => c && c.resize()); }, 200); });
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
  $('#notePreview').innerHTML = content.trim() ? md(content) : '<div class="placeholder">（空）</div>';
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
    const meta = [`<span class="venue">${p.venue} ${p.year}</span>`, p.type];
    if (p.topic) meta.push(p.topic);
    if (p.relevance != null) meta.push('rel ' + p.relevance);
    if (p.citations != null) meta.push(p.citations + ' cite');
    meta.push(fmtTime(p.created_at));
    return `<div class="m-item">
      <span class="mi-status status-dot ${p.status}" data-id="${p.id}" title="点击切换学习状态（当前：${p.status}）"></span>
      <div class="m-item-main" data-id="${p.id}">
        <div class="m-item-title">${p.title}</div>
        <div class="m-item-meta">${meta.join(' · ')}${p.source !== 'seed' ? ' <span class="m-tag">采集</span>' : ''}</div>
      </div>
      <button class="m-del" data-id="${p.id}" title="删除">🗑</button>
    </div>`;
  }).join('') || '<div class="placeholder">没有匹配的论文。</div>';
  document.querySelectorAll('#mList .m-item-main').forEach(el => el.onclick = () => openPaper(PAPERS.find(x => x.id === el.dataset.id)));
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

async function doIngest() {
  const sources = [...document.querySelectorAll('.ib-opts .src-chip.active')].map(c => c.dataset.src);
  const query = $('#ingQuery').value.trim();
  const log = $('#ingLog'); log.classList.remove('hidden');
  if (!query) { log.textContent = '请填写检索方向'; return; }
  if (!sources.length) { log.textContent = '请至少选择一个数据源'; return; }
  const btn = $('#ingBtn'); btn.disabled = true; const old = btn.textContent; btn.textContent = '采集中…';
  log.textContent = '正在采集，请稍候（数量越多越久；深度分类更慢）…';
  try {
    const r = await fetch('/api/ingest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query, sources, years: $('#ingYears').value.trim(),
        max: parseInt($('#ingMax').value) || 10,
        minRelevance: parseFloat($('#ingRel').value),
        deep: $('#ingDeep').checked, expand: $('#ingExpand').checked
      })
    });
    const j = await r.json();
    log.textContent = j.output || j.error || '(无输出)';
    log.scrollTop = log.scrollHeight;
    await reloadPapers(); renderManage();
  } catch (e) { log.textContent = '失败: ' + e; }
  finally { btn.disabled = false; btn.textContent = old; }
}

async function reloadPapers() {
  PAPERS = await (await fetch('/api/papers')).json();
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
    pdfDir: $('#setPdfDir').value.trim()
  };
  if ($('#setApiKey').value.trim()) body.apiKey = $('#setApiKey').value.trim();
  if ($('#setS2Key').value.trim()) body.s2ApiKey = $('#setS2Key').value.trim();
  await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const h = $('#setHint'); h.textContent = '已保存 ✓（下次采集生效）'; setTimeout(() => h.textContent = '', 3000);
  loadSettings();
}
