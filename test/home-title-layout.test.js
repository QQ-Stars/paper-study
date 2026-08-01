const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(root, 'public', 'app.js'), 'utf8');
const styleSource = fs.readFileSync(path.join(root, 'public', 'style.css'), 'utf8');

test('home title markup keeps badges and the bilingual title in one content wrapper', () => {
  const rowSource = appSource.match(/function rowHTML\(p, idx\)\s*\{[\s\S]*?\n\}/)?.[0];

  assert.ok(rowSource, 'rowHTML should exist');
  assert.match(
    rowSource,
    /<td class="ht-title" title="\$\{esc\(titleSearch\(p\)\)\}">/
  );
  assert.match(
    rowSource,
    /<span class="fav-star \$\{p\.favorite \? 'on' : ''\}" data-id="\$\{p\.id\}" title="\$\{p\.favorite \? '取消收藏' : '收藏'\}">\$\{p\.favorite \? '★' : '☆'\}<\/span>/
  );
  assert.match(
    rowSource,
    /\$\{semScoreBadge\(p\.id\)\}<span class="fav-star[\s\S]*?<\/span>\$\{titleMarkup\(p\)\}/
  );
  assert.match(
    rowSource,
    /<span class="ht-title-content">\$\{semScoreBadge\(p\.id\)\}<span class="fav-star[\s\S]*?\$\{titleMarkup\(p\)\}<\/span>/
  );
});

test('home title layout reserves badge space and ellipsizes both title lines', () => {
  assert.match(styleSource, /\.paper-title-stack\{[^}]*display:flex[^}]*min-width:0[^}]*flex-direction:column[^}]*gap:2px[^}]*\}/);
  assert.match(styleSource, /\.ht-title-content\{[^}]*display:flex[^}]*align-items:center[^}]*width:100%[^}]*min-width:0[^}]*\}/);
  assert.match(styleSource, /\.ht-title-content>\.sem-score,\.ht-title-content>\.fav-star\{[^}]*flex:0 0 auto[^}]*\}/);
  assert.match(styleSource, /\.ht-title \.paper-title-stack\{[^}]*flex:1[^}]*min-width:0[^}]*\}/);
  assert.doesNotMatch(styleSource, /\.ht-title \.paper-title-stack\{[^}]*max-width:100%/);
  assert.match(styleSource, /\.m-item-title \.paper-title-stack\{[^}]*display:inline-flex[^}]*vertical-align:middle[^}]*max-width:100%[^}]*\}/);
  assert.match(styleSource, /\.ht-title \.paper-title-primary,[^}]*\.ht-title \.paper-title-secondary,[^}]*\.pi-title \.paper-title-primary,[^}]*\.m-item-title \.paper-title-primary\{[^}]*overflow:hidden[^}]*text-overflow:ellipsis[^}]*white-space:nowrap[^}]*\}/);
});
