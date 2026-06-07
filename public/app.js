// ====== 状态 ======
let PAPERS = [];
let current = null;
let curNoteText = '';
let yearFilter = 'all';
let q = '';
let currentView = 'home';
let homeSort = { key: 'year', dir: 1 };

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
  buildYearFilters();
  renderSidebar();
  renderHome();
  bindUI();
  showView('home');
}

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
  $('#layout').classList.toggle('hidden', v !== 'read');
  if (v === 'home') renderHome();
  if (v === 'read' && !current) { $('#pdfScroll').innerHTML = EMPTY_HTML; }
}

// ====== 总览 Dashboard ======
function renderHome() {
  const total = PAPERS.length;
  const done = PAPERS.filter(p => p.status === '已理解').length;
  const ing = PAPERS.filter(p => p.status === '学习中').length;
  const pub = PAPERS.filter(p => p.venue !== 'arXiv').length;
  const yc = (yr) => PAPERS.filter(p => p.year === yr).length;
  const card = (num, label, plain, mini) =>
    `<div class="stat-card"><div class="stat-num ${plain ? 'plain' : ''}">${num}</div><div class="stat-label">${label}</div>${mini ? '<div class="stat-mini">' + mini + '</div>' : ''}</div>`;
  $('#statCards').innerHTML = [
    card(total, '论文总数', true),
    card(done, '已理解 ✓', false),
    card(ing, '学习中', false),
    card(pub, '顶会发表', true, (total - pub) + ' 篇 arXiv'),
    card(`${yc('2024')} / ${yc('2025')} / ${yc('2026')}`, '2024 / 25 / 26', true)
  ].join('');

  let list = PAPERS.filter(p => (yearFilter === 'all' || p.year === yearFilter));
  if (q) { const k = q.toLowerCase(); list = list.filter(p => (p.title + ' ' + p.venue + ' ' + p.type + ' ' + (p.topic || '')).toLowerCase().includes(k)); }
  list.sort(cmpHome);
  $('#homeBody').innerHTML = list.map(rowHTML).join('');
  document.querySelectorAll('#homeBody tr').forEach(tr => tr.onclick = () => openPaper(PAPERS.find(x => x.id === tr.dataset.id)));
  document.querySelectorAll('#homeTable th[data-sort]').forEach(th => {
    const base = th.dataset.label || th.textContent.replace(/[▲▼]/g, '').trim();
    th.dataset.label = base;
    th.innerHTML = base + (homeSort.key === th.dataset.sort ? ` <span class="arrow">${homeSort.dir > 0 ? '▲' : '▼'}</span>` : '');
  });
  updateSummary();
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
  $('#pdfOpen').href = '/papers/' + encodeURIComponent(p.file);
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
