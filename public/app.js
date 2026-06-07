// ---- 状态 ----
let PAPERS = [];
let current = null;
let curNoteText = '';
let yearFilter = 'all';
let q = '';

const $ = (s) => document.querySelector(s);
const md = (t) => (window.marked ? window.marked.parse(t || '') :
  '<pre>' + (t || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) + '</pre>');

init();
async function init() {
  PAPERS = await (await fetch('/api/papers')).json();
  buildYearFilters();
  renderSidebar();
  bindUI();
  const last = localStorage.getItem('lastPaper');
  if (last) { const p = PAPERS.find(x => x.id === last); if (p) openPaper(p); }
}

function buildYearFilters() {
  const years = [...new Set(PAPERS.map(p => p.year))].sort();
  const box = $('#yearFilters'); box.innerHTML = '';
  ['all', ...years].forEach(y => {
    const b = document.createElement('button');
    b.className = 'chip-btn' + (y === 'all' ? ' active' : '');
    b.textContent = y === 'all' ? '全部' : y;
    b.onclick = () => { yearFilter = y; [...box.children].forEach(c => c.classList.remove('active')); b.classList.add('active'); renderSidebar(); };
    box.appendChild(b);
  });
}

function updateSummary() {
  const done = PAPERS.filter(p => p.status === '已理解').length;
  const ing = PAPERS.filter(p => p.status === '学习中').length;
  $('#progressSummary').textContent = `已理解 ${done} · 学习中 ${ing} · 共 ${PAPERS.length}`;
  const pct = PAPERS.length ? Math.round(done / PAPERS.length * 100) : 0;
  const bar = $('#progressBar'); if (bar) bar.style.width = pct + '%';
}

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
    `<div class="pi-top">
       ${order}
       <div class="pi-title">${p.title}</div>
       <span class="status-dot ${p.status}" title="${p.status}"></span>
     </div>
     <div class="pi-meta">
       <span class="venue v-${p.venue}">${p.venue} ${p.year}</span>
       <span class="dir">${p.type}${p.topic ? ' · ' + p.topic : ''}</span>
     </div>`;
  return d;
}

async function openPaper(p) {
  current = p;
  localStorage.setItem('lastPaper', p.id);
  renderSidebar();
  $('#viewerEmpty').style.display = 'none';
  const f = $('#pdf'); f.style.display = 'block';
  f.src = '/papers/' + encodeURIComponent(p.file) + '#zoom=page-width';
  $('#paperTitle').textContent = `${p.title} — ${p.venue} ${p.year}`;
  setStatusUI(p.status || '未开始');
  const ex = await (await fetch('/api/explainer?id=' + encodeURIComponent(p.id))).text();
  $('#explainerView').innerHTML = md(ex);
  $('#explainerView').scrollTop = 0;
  curNoteText = await (await fetch('/api/note?id=' + encodeURIComponent(p.id))).text();
  $('#noteEdit').value = curNoteText;
  $('#notePreview').innerHTML = curNoteText.trim() ? md(curNoteText)
    : '<div class="placeholder">还没有笔记。点「编辑」开始记，或在对话里让我「记录」。</div>';
  showNoteMode('preview');
}

function bindUI() {
  $('#search').oninput = (e) => { q = e.target.value.trim(); renderSidebar(); };
  document.querySelectorAll('.tab').forEach(t => t.onclick = () => switchTab(t.dataset.tab));
  $('#btnEdit').onclick = () => { setSegActive('#tab-note .seg-sm', $('#btnEdit')); showNoteMode('edit'); $('#noteEdit').focus(); };
  $('#btnPreview').onclick = () => { $('#notePreview').innerHTML = md($('#noteEdit').value) || ''; setSegActive('#tab-note .seg-sm', $('#btnPreview')); showNoteMode('preview'); };
  $('#btnSave').onclick = saveNote;
  $('#noteEdit').onblur = () => { if ($('#noteEdit').value !== curNoteText) saveNote(); };
  document.querySelectorAll('#statusSeg button').forEach(b => b.onclick = () => saveStatus(b.dataset.st));
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
function setSegActive(scope, btn) {
  document.querySelectorAll(scope + ' button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}
function setStatusUI(status) {
  $('#statusSel').value = status;
  document.querySelectorAll('#statusSeg button').forEach(b => b.classList.toggle('active', b.dataset.st === status));
}

async function saveNote() {
  if (!current) return;
  const content = $('#noteEdit').value;
  await fetch('/api/note', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: current.id, content }) });
  curNoteText = content;
  $('#notePreview').innerHTML = content.trim() ? md(content) : '<div class="placeholder">（空）</div>';
  const h = $('#saveHint'); h.textContent = '已保存 ✓ ' + new Date().toLocaleTimeString();
  setTimeout(() => h.textContent = '', 2500);
}

async function saveStatus(status) {
  if (!current) { setStatusUI(status); return; }
  setStatusUI(status);
  await fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: current.id, status }) });
  current.status = status;
  const p = PAPERS.find(x => x.id === current.id); if (p) p.status = status;
  renderSidebar();
}
