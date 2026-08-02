const assert = require('node:assert/strict');
const test = require('node:test');
const { marked, Marked } = require('../public/vendor/marked.min.js');
const katex = require('katex');
const { createMarkdownRenderer } = require('../public/markdown-rendering.js');

function makeRenderer(options = {}) {
  return createMarkdownRenderer({
    getMarked: () => marked,
    getKatex: () => katex,
    ...options,
  });
}

test('renders ordinary Markdown and safe links while preserving math', () => {
  const renderer = makeRenderer();
  const html = renderer.render('# Heading\n\n**bold**\n\n- item\n\n[site](http://example.com) [secure](https://example.com) [mail](mailto:hi@example.com)\n\n$E=mc^2$');

  assert.match(html, /<h1>Heading<\/h1>/);
  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<li>item<\/li>/);
  assert.match(html, /href="http:\/\/example\.com"/);
  assert.match(html, /href="https:\/\/example\.com"/);
  assert.match(html, /href="mailto:hi@example\.com"/);
  assert.match(html, /katex/);
});

test('renders into the supplied target', () => {
  const target = { innerHTML: '' };
  const result = makeRenderer().renderInto(target, '**content**');

  assert.match(target.innerHTML, /<strong>content<\/strong>/);
  assert.equal(result, target);
});

test('treats raw HTML as literal text', () => {
  const html = makeRenderer().render('<img src=x onerror=alert(1)> <svg onload=alert(1)></svg> <script>alert(1)</script> <button onclick=alert(1)>x</button>');

  assert.doesNotMatch(html, /<(img|svg|script|button)\b/i);
  assert.doesNotMatch(html, /<(img|svg|script|button)\b[^>]*\s(onerror|onload|onclick)\s*=/i);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;svg onload=alert\(1\)&gt;&lt;\/svg&gt;/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /&lt;button onclick=alert\(1\)&gt;x&lt;\/button&gt;/);
});

test('escapes malformed browser-parseable HTML-like text', () => {
  const payloads = [
    '<img/src=x onerror=alert(1)>',
    '<svg/onload=alert(1)>',
    '<iframe/src=javascript:alert(1)>',
  ];
  const html = makeRenderer().render(payloads.join('\n'));

  for (const payload of payloads) {
    assert.ok(html.includes('&lt;' + payload.slice(1, -1) + '&gt;'));
    assert.ok(!html.includes(payload));
  }
});

test('keeps formulas inside malformed HTML-like constructs literal', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const malformedAttribute = renderer.render('<img/src=$attribute$>$ordinary$');
  assert.deepEqual(calls, ['ordinary']);
  assert.match(malformedAttribute, /&lt;img\/src=\$attribute\$&gt;/);
  assert.match(malformedAttribute, /class="katex">ordinary<\/span>/);

  calls.length = 0;
  const unclosedQuote = renderer.render('<span data-x="$attribute>$ordinary$');
  assert.deepEqual(calls, []);
  assert.match(unclosedQuote, /&lt;span data-x=&quot;\$attribute&gt;\$ordinary\$/);
  assert.doesNotMatch(unclosedQuote, /class="katex"/);
});

test('rejects dangerous link protocols including disguised JavaScript', () => {
  const html = makeRenderer().render('[js](JaVaScRiPt:alert(1)) [data](DaTa:text/html,x) [blob](BlOb:https://example.com/a) [file](FiLe:///tmp/a) [relative](//evil.example)');

  assert.doesNotMatch(html, /\bhref\s*=/i);
  assert.match(html, /js/);
});

test('preserves standard URL and email autolinks', () => {
  const html = makeRenderer().render('<https://example.com> <mailto:person@example.com> <person@example.com>');

  assert.match(html, /href="https:\/\/example\.com"/);
  assert.match(html, /href="mailto:person@example\.com"/);
});

test('keeps formula-looking text literal inside bare URL autolinks', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const safeUrls = [
    'https://example.com/$x$',
    'http://example.com/$x$',
    'www.example.com/$x$',
  ];

  for (const source of safeUrls) {
    calls.length = 0;
    const html = renderer.render(source);
    assert.deepEqual(calls, [], source);
    assert.match(html, /<a\b[^>]*href="[^"]+">/);
    const escapedSource = source.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    assert.match(html, new RegExp(`href="[^"]*${escapedSource}"`));
  }

  calls.length = 0;
  const ftp = renderer.render('ftp://example.com/$x$');
  assert.deepEqual(calls, []);
  assert.doesNotMatch(ftp, /<a\b/i);
  assert.match(ftp, /ftp:\/\/example\.com\/\$x\$/);
});

test('preserves Marked GFM defaults for tables', () => {
  const html = makeRenderer().render('| A | B |\n| --- | --- |\n| 1 | 2 |');

  assert.match(html, /<table>/);
  assert.match(html, /<th>A<\/th>/);
  assert.match(html, /<td>1<\/td>/);
});

test('preserves host inline extensions alongside local math extensions', () => {
  const host = new Marked();
  host.use({
    extensions: [{
      name: 'badge',
      level: 'inline',
      start(source) { return source.indexOf('%%'); },
      tokenizer(source) {
        const match = /^%%([^%]+)%%/.exec(source);
        return match && { type: 'badge', raw: match[0], text: match[1] };
      },
      renderer(token) { return `<mark>${token.text}</mark>`; },
    }],
  });
  const renderer = makeRenderer({
    getMarked: () => host,
    getKatex: () => ({ renderToString(source) { return `<span class="katex">${source}</span>`; } }),
  });

  const html = renderer.render('%%custom%% and $x$');

  assert.match(html, /<mark>custom<\/mark>/);
  assert.match(html, /<span class="katex">x<\/span>/);
});

test('preserves host renderer behavior except the required safe overrides', () => {
  const host = new Marked();
  host.use({
    renderer: {
      paragraph(token) {
        return `<section class="host-paragraph">${this.parser.parseInline(token.tokens || [])}</section>\n`;
      },
      html(token) { return `<unsafe-html>${token.text}</unsafe-html>`; },
      link(token) { return `<unsafe-link href="${token.href}">${this.parser.parseInline(token.tokens || [])}</unsafe-link>`; },
      image(token) { return `<unsafe-image src="${token.href}">${token.text}</unsafe-image>`; },
    },
  });
  const renderer = makeRenderer({ getMarked: () => host });

  const html = renderer.render('ordinary\n\n<img src=x onerror=alert(1)>\n\n[safe](https://example.com) [blocked](javascript:alert(1)) ![alt](https://tracker.example/pixel)');

  assert.match(html, /<section class="host-paragraph">ordinary<\/section>/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /<a href="https:\/\/example\.com">safe<\/a>/);
  assert.doesNotMatch(html, /href="javascript:/i);
  assert.match(html, /alt/);
  assert.doesNotMatch(html, /<(?:img|unsafe-html|unsafe-link|unsafe-image)\b/i);
});

test('does not let host extension renderers bypass the required safe overrides', () => {
  const host = new Marked();
  host.use({
    extensions: [
      { name: 'html', renderer() { return '<unsafe-extension-html>'; } },
      { name: 'link', renderer() { return '<unsafe-extension-link>'; } },
      { name: 'image', renderer() { return '<unsafe-extension-image>'; } },
    ],
  });
  const renderer = makeRenderer({ getMarked: () => host });

  const html = renderer.render('<img src=x onerror=alert(1)>\n\n[safe](https://example.com) ![alt](https://tracker.example/pixel)');

  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /<a href="https:\/\/example\.com">safe<\/a>/);
  assert.match(html, /alt/);
  assert.doesNotMatch(html, /unsafe-extension-(?:html|link|image)/);
});

test('renders images as alt text without an image tag or remote URL', () => {
  const html = makeRenderer().render('![descriptive alt](https://evil.example/track.png)');

  assert.match(html, /descriptive alt/);
  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /evil\.example/);
});

test('preserves escaped inline HTML in image alt text without rendering an image', () => {
  const html = makeRenderer().render('![a <b>x</b> c](https://x)');

  assert.match(html, /a &lt;b&gt;x&lt;\/b&gt; c/);
  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /https:\/\/x/);
});

test('isolates unclosed raw HTML in image alt text from following math', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('![<b>$alt$](https://tracker.example/x) then $outside$');

  assert.deepEqual(calls, ['outside']);
  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /tracker\.example/);
  assert.match(html, /\$alt\$/);
});

test('isolates closing raw HTML in image alt text from an outer context', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b>![</b>$alt$](https://tracker/x) $stillRaw$</b> $outside$');

  assert.deepEqual(calls, ['outside']);
  assert.doesNotMatch(html, /<img\b/i);
  assert.doesNotMatch(html, /tracker\/x/);
  assert.match(html, /\$alt\$/);
  assert.match(html, /\$stillRaw\$/);
});

test('passes restrictive options to KaTeX', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source, options) {
        calls.push({ source, options });
        return '<span class="katex">safe</span>';
      },
    }),
  });

  renderer.render('$x$\n\n$$y$$');
  assert.deepEqual(calls.map(({ options }) => options), [
    { displayMode: false, throwOnError: false, trust: false, maxExpand: 1000 },
    { displayMode: true, throwOnError: false, trust: false, maxExpand: 1000 },
  ]);
});

test('hostile, malformed, and recursive formulas neither link nor throw', () => {
  const renderer = makeRenderer();
  const payloads = [
    '$\\href{javascript:alert(1)}{click}$ $\\htmlStyle{color:red}{x}$',
    '$\\frac{$',
    '$$\\def\\a{\\a}\\a$$',
  ];

  for (const payload of payloads) {
    let html;
    assert.doesNotThrow(() => {
      html = renderer.render(payload);
    });
    assert.doesNotMatch(html, /<a\b/i);
    assert.doesNotMatch(html, /\bhref\s*=/i);
  }
});

test('falls back to escaped text when KaTeX is unavailable or throws', () => {
  const source = '$<img src=x onerror=alert(1)> <svg onload=alert(1)></svg>$';
  for (const getKatex of [() => null, () => ({ renderToString() { throw new Error('bad formula'); } })]) {
    const html = makeRenderer({ getKatex }).render(source);
    assert.doesNotMatch(html, /<(img|svg)\b/i);
    assert.match(html, /&lt;img/i);
    assert.match(html, /&lt;svg/i);
  }
});

test('does not mistake an escaped dollar for the start of inline math', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const html = renderer.render('Price \\$5 and $x$');

  assert.deepEqual(calls, ['x']);
  assert.match(html, /Price \$5 and/);
  assert.match(html, /katex/);
});

test('preserves Markdown formatting inside links while stripping unsafe URLs and image sources', () => {
  const html = makeRenderer().render('[**bold**](https://example.com) [**blocked**](javascript:alert(1)) [![alt](https://tracker.example/pixel)](https://example.com) [![blocked alt](https://tracker.example/blocked)](javascript:alert(1))');

  assert.match(html, /<a href="https:\/\/example\.com"><strong>bold<\/strong><\/a>/);
  assert.match(html, /<strong>blocked<\/strong>/);
  assert.doesNotMatch(html, /javascript:/i);
  assert.match(html, /<a href="https:\/\/example\.com">alt<\/a>/);
  assert.match(html, /blocked alt/);
  assert.doesNotMatch(html, /<a\b[^>]*>blocked alt<\/a>/i);
  assert.doesNotMatch(html, /tracker\.example/);
});

test('does not render formula-looking text inside Markdown metadata, raw HTML attributes, or code', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const html = renderer.render('[link](https://example.com/$href$ "$title$") <span data-formula="$attribute$">text</span> `$code$` and $w$');

  assert.deepEqual(calls, ['w']);
  assert.match(html, /href="https:\/\/example\.com\/\$href\$"/);
  assert.match(html, /title="\$title\$"/);
  assert.match(html, /&lt;span data-formula=&quot;\$attribute\$&quot;&gt;text&lt;\/span&gt;/);
  assert.match(html, /<code>\$code\$<\/code>/);
  assert.match(html, /katex/);
});

test('does not let unsafe-context openers pair with a later ordinary formula delimiter', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const cases = [
    {
      source: '<span data-x="$attribute"></span>$ordinary$',
      unsafeFragment: 'attribute',
      assertContext(html) {
        assert.match(html, /&lt;span data-x=&quot;\$attribute&quot;&gt;&lt;\/span&gt;/);
      },
    },
    {
      source: '`$code`$ordinary$',
      unsafeFragment: 'code',
      assertContext(html) {
        assert.match(html, /<code>\$code<\/code>/);
      },
    },
    {
      source: '[link](https://example.com/$path)$ordinary$',
      unsafeFragment: 'path',
      assertContext(html) {
        assert.match(html, /<a href="https:\/\/example\.com\/\$path">link<\/a>/);
      },
    },
  ];

  for (const { source, unsafeFragment, assertContext } of cases) {
    calls.length = 0;
    const html = renderer.render(source);

    assert.deepEqual(calls, ['ordinary']);
    assertContext(html);
    assert.match(html, /class="katex">ordinary<\/span>/);
    assert.doesNotMatch(html, new RegExp(`class="katex">[^<]*${unsafeFragment}`));
  }
});

test('keeps formula-looking text literal throughout raw HTML', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b data-x="$html$">$raw$</b>');

  assert.deepEqual(calls, []);
  assert.match(html, /&lt;b data-x=&quot;\$html\$&quot;&gt;\$raw\$&lt;\/b&gt;/);
  assert.doesNotMatch(html, /class="katex"/);
});

test('does not render math left inside an unclosed raw HTML context', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  for (const source of ['<b>$raw$', '<b>$raw$\n\n$outside$']) {
    calls.length = 0;
    const html = renderer.render(source);

    assert.deepEqual(calls, [], source);
    assert.match(html, /\$raw\$/);
    if (source.includes('$outside$')) assert.match(html, /\$outside\$/);
    assert.doesNotMatch(html, /class="katex"/);
  }
});

test('resumes math rendering after a raw HTML context closes', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b>$raw$</b> $ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b&gt;\$raw\$&lt;\/b&gt;/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('renders formulas after a closed ordinary block HTML tail', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<div>x</div> $outside$');

  assert.deepEqual(calls, ['outside']);
  assert.match(html, /&lt;div&gt;x&lt;\/div&gt;/);
  assert.match(html, /class="katex">outside<\/span>/);
});

test('renders closed numeric and trailing-digit dollar expressions', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  for (const [source, expected] of [['$5$', '5'], ['$5abc$', '5abc'], ['$x$5', 'x']]) {
    calls.length = 0;
    renderer.render(source);
    assert.deepEqual(calls, [expected], source);
  }
});

test('treats an unquoted slash inside an HTML attribute as literal attribute text', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b data=x/>$raw$</b> $outside$');

  assert.deepEqual(calls, ['outside']);
  assert.match(html, /\$raw\$/);
  assert.match(html, /class="katex">outside<\/span>/);
});

test('recognizes self-closing slashes only outside unquoted attribute values', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const cases = [
    { source: '<b a=x / >$raw$</b> $outside$', expected: ['outside'], literalRaw: true },
    { source: '<b disabled/>$raw$</b> $outside$', expected: ['raw', 'outside'], literalRaw: false },
    { source: '<b a=x/>$raw$</b> $outside$', expected: ['outside'], literalRaw: true },
  ];

  for (const { source, expected, literalRaw } of cases) {
    calls.length = 0;
    const html = renderer.render(source);

    assert.deepEqual(calls, expected, source);
    if (literalRaw) assert.match(html, /\$raw\$/);
    else assert.match(html, /class="katex">raw<\/span>/);
  }
});

test('treats tag-looking text inside raw-text elements as literal', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<script>const label = "<b>"; $raw$</script>\n\n$ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;script&gt;const label = &quot;&lt;b&gt;&quot;; \$raw\$&lt;\/script&gt;/);
  assert.doesNotMatch(html, /class="katex">raw<\/span>/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('does not close an outer raw HTML context from a raw-text element body', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const source = '<b><script>const close = "</b>";</script>$raw$</b> $ordinary$';

  assert.equal(source, '<b><script>const close = "</b>";</script>$raw$</b> $ordinary$');
  const html = renderer.render(source);

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b&gt;&lt;script&gt;const close = "&lt;\/b&gt;";&lt;\/script&gt;\$raw\$&lt;\/b&gt;/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('does not let an unrelated closing tag exit a raw HTML context', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b></br>$raw$</b> $ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b&gt;&lt;\/br&gt;\$raw\$&lt;\/b&gt;/);
  assert.doesNotMatch(html, /class="katex">raw<\/span>/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('does not enter raw HTML context for angle brackets inside quoted attributes', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b data-x="<">$raw$</b> $ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b data-x=&quot;&lt;&quot;&gt;\$raw\$&lt;\/b&gt;/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('does not close a raw HTML tag at a self-closing marker inside a quoted attribute', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b data-x="/>">$raw$</b> $ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b data-x=&quot;\/&gt;&quot;&gt;\$raw\$&lt;\/b&gt;/);
  assert.doesNotMatch(html, /class="katex">raw<\/span>/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('does not close a raw HTML element at a closing-tag string inside a quoted attribute', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<b title="x </b> y">$raw$</b> $ordinary$');

  assert.deepEqual(calls, ['ordinary']);
  assert.match(html, /&lt;b title=&quot;x &lt;\/b&gt; y&quot;&gt;\$raw\$&lt;\/b&gt;/);
  assert.doesNotMatch(html, /class="katex">raw<\/span>/);
  assert.match(html, /class="katex">ordinary<\/span>/);
});

test('protects formulas before Marked interprets their Markdown syntax', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('$x_i + [a,b]$');

  assert.deepEqual(calls, ['x_i + [a,b]']);
  assert.match(html, /katex/);
});

test('does not let math spans cross HTML-like markup boundaries', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const contexts = [
    '<b>inside</b>',
    '<span data-x="unterminated',
    '<!-- unclosed comment',
  ];

  for (const { opening, closing } of delimiters) {
    for (const context of contexts) {
      calls.length = 0;
      const source = `${opening}before ${context} after${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], source);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, /&lt;/);
    }
  }
});

test('does not let math delimiters terminate inside Markdown metadata or code spans', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const contexts = [
    {
      name: 'link destination',
      source: ({ opening, closing }) => `${opening}before [link](https://x/${opening}href${closing}) after${closing}`,
    },
    {
      name: 'image destination',
      source: ({ opening, closing }) => `${opening}before ![alt](https://x/${opening}img${closing}) after${closing}`,
    },
    {
      name: 'code span',
      source: ({ opening, closing }) => `${opening}before \`code ${opening}inside${closing}\` after${closing}`,
    },
  ];

  for (const delimiter of delimiters) {
    for (const context of contexts) {
      calls.length = 0;
      const source = context.source(delimiter);
      const html = renderer.render(source);

      assert.deepEqual(calls, [], `${context.name}: ${source}`);
      assert.doesNotMatch(html, /class="katex"/);
      if (context.name === 'link destination' && delimiter.opening === '$') {
        assert.match(html, /<a href="https:\/\/x\/\$href\$">link<\/a>/);
      }
      if (context.name === 'image destination' && delimiter.opening === '$') {
        assert.match(html, /alt/);
        assert.doesNotMatch(html, /https:\/\/x/);
      }
      if (context.name === 'code span') assert.match(html, /<code>/);
    }
  }
});

test('does not let math spans consume complete URL links or images', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const contexts = [
    {
      name: 'link URL',
      source: ({ opening, closing }) => `${opening}before [link](https://x/path) after${closing}`,
    },
    {
      name: 'link title',
      source: ({ opening, closing }) => `${opening}before [link](https://x/path "title") after${closing}`,
    },
    {
      name: 'image URL',
      source: ({ opening, closing }) => `${opening}before ![alt](https://x/path) after${closing}`,
    },
  ];

  for (const delimiter of delimiters) {
    for (const context of contexts) {
      calls.length = 0;
      const source = context.source(delimiter);
      const html = renderer.render(source);

      assert.deepEqual(calls, [], `${context.name}: ${source}`);
      assert.doesNotMatch(html, /class="katex"/);
      if (context.name === 'link URL' && delimiter.opening === '$') {
        assert.match(html, /<a href="https:\/\/x\/path">link<\/a>/);
      }
      if (context.name === 'image URL' && delimiter.opening === '$') {
        assert.match(html, /alt/);
        assert.doesNotMatch(html, /https:\/\/x/);
      }
    }
  }
});

test('closes every raw-text context at a matching end tag inside comment-looking text', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  for (const tag of ['script', 'style', 'xmp', 'iframe', 'noembed', 'noframes', 'textarea', 'title']) {
    calls.length = 0;
    const html = renderer.render(`<${tag}><!-- </${tag}> -->\n$outside$`);

    assert.deepEqual(calls, ['outside'], tag);
    assert.match(html, /class="katex">outside<\/span>/);
    if (tag === 'script') assert.match(html, /&lt;\/script&gt; --&gt;\n<p>/);
  }
});

test('renders backslash-delimited math and preserves escaped dollars inside inline math', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render(String.raw`\(z\) \[w\] $a\$b$`);

  assert.deepEqual(calls, ['z', 'w', 'a\\$b']);
  assert.equal((html.match(/class="katex"/g) || []).length, 3);
});

test('renders square-bracket display math inside direct Markdown link labels', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source, options) {
        calls.push({ source, displayMode: options.displayMode });
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render(String.raw`[a \[x\]](https://x)`);

  assert.deepEqual(calls, [{ source: 'x', displayMode: true }]);
  assert.match(html, /<a href="https:\/\/x">a <span class="katex">x<\/span><\/a>/);
});

test('renders square-bracket display math inside reference-style Markdown link labels', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source, options) {
        calls.push({ source, displayMode: options.displayMode });
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render(String.raw`[a \[x\]][id]

[id]: https://x`);

  assert.deepEqual(calls, [{ source: 'x', displayMode: true }]);
  assert.match(html, /<a href="https:\/\/x">a <span class="katex">x<\/span><\/a>/);
});

test('scales unclosed square-bracket display markers in link labels linearly', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const elapsedFor = size => {
    const started = performance.now();
    const html = renderer.render('[' + '\\['.repeat(size) + '](https://x)');
    assert.match(html, /<a href="https:\/\/x">/);
    return performance.now() - started;
  };

  const small = elapsedFor(5_000);
  const large = elapsedFor(15_000);

  assert.ok(
    large < small * 5 + 100,
    `expected near-linear unclosed \\[ scans (5k=${small.toFixed(1)}ms, 15k=${large.toFixed(1)}ms)`,
  );
});

test('scales escaped link-label brackets linearly', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const elapsedFor = size => {
    const started = performance.now();
    const html = renderer.render('[' + '\\'.repeat(size) + '](https://x)');
    assert.match(html, /<a href="https:\/\/x">/);
    return performance.now() - started;
  };

  const small = elapsedFor(5_000);
  const large = elapsedFor(20_000);

  assert.ok(
    large < small * 5 + 100,
    `expected near-linear escaped-link-label scans (5k=${small.toFixed(1)}ms, 20k=${large.toFixed(1)}ms)`,
  );
});

test('scales escaped inline-math contents linearly', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const elapsedFor = size => {
    const started = performance.now();
    const html = renderer.render('$' + '\\'.repeat(size) + 'x$');
    assert.match(html, /x/);
    return performance.now() - started;
  };

  const small = elapsedFor(5_000);
  const large = elapsedFor(15_000);

  assert.ok(
    large < small * 5 + 100,
    `expected near-linear escaped inline-math scans (5k=${small.toFixed(1)}ms, 15k=${large.toFixed(1)}ms)`,
  );
});

test('scales ordinary inline fallback without math markers linearly', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const cases = [
    { name: 'unclosed emphasis', source: size => '*a'.repeat(size) },
    { name: 'escaped text', source: size => '\\a'.repeat(size) },
    { name: 'unclosed link', source: size => '[a'.repeat(size) },
  ];

  for (const { name, source } of cases) {
    const elapsedFor = size => {
      const started = performance.now();
      const html = renderer.render(source(size));
      assert.match(html, /a/, name);
      return performance.now() - started;
    };
    const small = elapsedFor(5_000);
    const large = elapsedFor(15_000);

    assert.ok(
      large < small * 5 + 100,
      `expected near-linear ${name} fallback scans (5k=${small.toFixed(1)}ms, 15k=${large.toFixed(1)}ms)`,
    );
  }
});

test('returns escaped literal text before loading dependencies for pathological emphasis paragraphs', () => {
  for (const delimiter of ['*', '_']) {
    let markedReads = 0;
    let katexReads = 0;
    const renderer = makeRenderer({
      getMarked() {
        markedReads++;
        return marked;
      },
      getKatex() {
        katexReads++;
        return katex;
      },
    });
    const source = `${`${delimiter}a`.repeat(2_048)}<img src=x onerror=alert(1)>`;
    const html = renderer.render(source);

    assert.equal(markedReads, 0, delimiter);
    assert.equal(katexReads, 0, delimiter);
    assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(html, /<img\b/i);
  }
});

test('counts escaped emphasis markers toward the pathological-density guard', () => {
  let markedReads = 0;
  let katexReads = 0;
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
    getKatex() {
      katexReads++;
      return katex;
    },
  });
  const html = renderer.render(`${'\\*'.repeat(2_048)}<img src=x onerror=alert(1)>`);

  assert.equal(markedReads, 0);
  assert.equal(katexReads, 0);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img\b/i);
});

test('keeps below-threshold emphasis paragraphs on the Marked path', () => {
  let markedReads = 0;
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
  });
  const html = renderer.render('*a'.repeat(2_047));

  assert.equal(markedReads, 1);
  assert.match(html, /a/);
});

test('does not guard normal GFM or inline math', () => {
  let markedReads = 0;
  const mathCalls = [];
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
    getKatex: () => ({
      renderToString(source) {
        mathCalls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const html = renderer.render('| A | B |\n| --- | --- |\n| 1 | 2 |\n\n- [x] task\n\nhttps://example.com\n\n$x$');

  assert.equal(markedReads, 1);
  assert.deepEqual(mathCalls, ['x']);
  assert.match(html, /<table>/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /href="https:\/\/example\.com"/);
  assert.match(html, /class="katex">x<\/span>/);
});

test('scans code-fence-looking content in the pathological preflight to avoid fence-parser bypasses', () => {
  let markedReads = 0;
  let katexReads = 0;
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
    getKatex() {
      katexReads++;
      return katex;
    },
  });
  const source = `\`\`\`\n${'*a'.repeat(2_048)}<img src=x onerror=alert(1)>\n\`\`\``;
  const html = renderer.render(source);

  assert.equal(markedReads, 0);
  assert.equal(katexReads, 0);
  assert.equal(html, source.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'));
  assert.doesNotMatch(html, /<img\b/i);
});

test('guards a pathological logical run spanning multiple nonblank lines before loading Marked', () => {
  let markedReads = 0;
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
  });
  const source = `${'*a'.repeat(1_024)}\n${'_b'.repeat(1_024)}`;

  const html = renderer.render(source);

  assert.equal(markedReads, 0);
  assert.equal(html, source);
});

test('resets the pathological-density run at blank and whitespace-only lines', () => {
  let markedReads = 0;
  const renderer = makeRenderer({
    getMarked() {
      markedReads++;
      return marked;
    },
  });
  const source = `${'*a'.repeat(1_024)}\n \t\n${'_b'.repeat(1_024)}`;

  const html = renderer.render(source);

  assert.equal(markedReads, 1);
  assert.match(html, /a/);
});

test('treats CRLF and bare CR as logical-run line boundaries', () => {
  for (const lineEnding of ['\r\n', '\r']) {
    let markedReads = 0;
    const renderer = makeRenderer({
      getMarked() {
        markedReads++;
        return marked;
      },
    });
    const source = `${'*a'.repeat(1_024)}${lineEnding}${'_b'.repeat(1_024)}`;

    const html = renderer.render(source);

    assert.equal(markedReads, 0, JSON.stringify(lineEnding));
    assert.equal(html, source, JSON.stringify(lineEnding));
  }
});

test('uses inclusive length and marker thresholds for pathological logical runs', () => {
  let exactMarkedReads = 0;
  const exactRenderer = makeRenderer({
    getMarked() {
      exactMarkedReads++;
      return marked;
    },
  });
  const exactSource = '*a'.repeat(2_048);

  assert.equal(exactRenderer.render(exactSource), exactSource);
  assert.equal(exactMarkedReads, 0);

  let belowMarkedReads = 0;
  const belowRenderer = makeRenderer({
    getMarked() {
      belowMarkedReads++;
      return marked;
    },
  });
  const belowSource = '*a'.repeat(2_047) + 'ab';

  assert.match(belowRenderer.render(belowSource), /a/);
  assert.equal(belowMarkedReads, 1);
});

test('renders numeric inline math without treating every digit as currency', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const cases = [
    ['$2+2$', '2+2'],
    ['$0.05$', '0.05'],
    [String.raw`$3\times 4$`, String.raw`3\times 4`],
    ['$2x$', '2x'],
    ['x=$2$', '2'],
    ['$x$2', 'x'],
  ];

  for (const [source, expected] of cases) {
    calls.length = 0;
    renderer.render(source);
    assert.deepEqual(calls, [expected], source);
  }
});

test('does not let math spans cross complete relative links, titles, or images', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const contexts = [
    { name: 'relative destination', markdown: '[label](foo)', text: 'label' },
    { name: 'fragment destination', markdown: '[label](#anchor)', text: 'label' },
    { name: 'query destination', markdown: '[label](?query)', text: 'label' },
    { name: 'link title', markdown: '[label](foo "title")', text: 'label' },
    { name: 'image destination', markdown: '![alt](foo)', text: 'alt' },
  ];

  for (const { opening, closing } of delimiters) {
    for (const { name, markdown, text } of contexts) {
      calls.length = 0;
      const source = `${opening}before ${markdown} after${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], `${name}: ${source}`);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, new RegExp(text), source);
    }
  }
});

test('does not let math spans treat complete direct links as TeX bracket calls', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const { opening, closing } of delimiters) {
    calls.length = 0;
    const source = `${opening}x_[a](b)${closing}`;
    const html = renderer.render(source);

    assert.deepEqual(calls, [], source);
    assert.doesNotMatch(html, /class="katex"/);
    assert.match(html, /a/, source);
  }
});

test('does not mistake dollar-denominated prices for inline math', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('The price is $5 and then $10.');

  assert.deepEqual(calls, []);
  assert.match(html, /The price is \$5 and then \$10\./);
});

test('does not start inline math at a currency prefix before a later delimiter', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const withAnd = renderer.render('$5 and $x$');
  assert.deepEqual(calls, ['x']);
  assert.match(withAnd, /\$5 and/);
  assert.match(withAnd, /class="katex">x<\/span>/);

  calls.length = 0;
  const withUsd = renderer.render('$5 USD and $x$');
  assert.deepEqual(calls, ['x']);
  assert.match(withUsd, /\$5 USD and/);
  assert.match(withUsd, /class="katex">x<\/span>/);

  calls.length = 0;
  const range = renderer.render('$5-$10');
  assert.deepEqual(calls, []);
  assert.match(range, /\$5-\$10/);
});

test('renders non-string values safely and tolerates null input', () => {
  const renderer = makeRenderer();

  assert.match(renderer.render(0), /0/);
  assert.doesNotThrow(() => renderer.render(null));
});

test('uses an empty safe fallback when input cannot be stringified', () => {
  const renderer = makeRenderer();
  const nullPrototype = Object.create(null);
  const throwingToString = { toString() { throw new Error('no conversion'); } };

  assert.doesNotThrow(() => renderer.render(nullPrototype));
  assert.doesNotThrow(() => renderer.render(throwingToString));
  assert.equal(renderer.render(nullPrototype), '');
  assert.equal(renderer.render(throwingToString), '');
});

test('reads the KaTeX dependency once for each render', () => {
  let reads = 0;
  const renderer = makeRenderer({
    getKatex() {
      reads++;
      return { renderToString(source) { return `<span class="katex">${source}</span>`; } };
    },
  });

  renderer.render('$x$ and $y$');
  assert.equal(reads, 1);

  renderer.render('plain text');
  assert.equal(reads, 2);
});

test('reads KaTeX between local tokenization and parsing', () => {
  const events = [];
  const wrappedMarked = {
    ...marked,
    lexer(source, options) {
      events.push('lexer');
      return marked.lexer(source, options);
    },
    parser(tokens, options) {
      events.push('parser');
      return marked.parser(tokens, options);
    },
  };
  const renderer = createMarkdownRenderer({
    getMarked: () => wrappedMarked,
    getKatex: () => {
      events.push('katex');
      return { renderToString(source) { return `<span class="katex">${source}</span>`; } };
    },
  });

  renderer.render('$x$');
  assert.deepEqual(events, ['lexer', 'katex', 'parser']);
});

test('keeps formulas literal in unclosed HTML-like tails and comments', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const sources = [
    '<img/src=$attribute-$ordinary$',
    '<img/src=$x$',
    '<img src=$x$',
    '<span data-x=$x$',
    '<b data-x=$x$ and $y$',
    '<span title="$a$\n$x$',
    '<span data=$a$\n$x$',
    'x <!-- $raw$ $out$',
    '<span data=x $formula$',
  ];

  for (const source of sources) {
    calls.length = 0;
    const html = renderer.render(source);
    assert.deepEqual(calls, [], source);
    assert.doesNotMatch(html, /class="katex"/);
    assert.match(html, /\$/);
    assert.match(html, /&lt;/);
  }
});

test('escapes hostile HTML when Marked is unavailable', () => {
  const html = makeRenderer({ getMarked: () => null }).render('<img src=x onerror=alert(1)>');

  assert.doesNotMatch(html, /<img\b/i);
  assert.match(html, /&lt;img\b/i);
});

test('accepts uppercase HTTPS links as safe anchors', () => {
  const html = makeRenderer().render('[secure](HTTPS://example.com)');

  assert.match(html, /<a\b[^>]*>secure<\/a>/i);
});

test('preserves reference links after splitting raw-text HTML tails', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('[bar]: https://example.com\n\n<script>x</script> [foo][bar] $x$');

  assert.deepEqual(calls, ['x']);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(html, /<a href="https:\/\/example\.com">foo<\/a>/);
  assert.match(html, /class="katex">x<\/span>/);
});

test('preserves reference links whose definition is discovered in a split raw-text HTML tail', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });

  const html = renderer.render('<script>x</script> [foo][bar]\n\n[bar]: https://example.com\n\n$x$');

  assert.deepEqual(calls, ['x']);
  assert.match(html, /&lt;script&gt;x&lt;\/script&gt;/);
  assert.match(html, /<a href="https:\/\/example\.com">foo<\/a>/);
  assert.match(html, /class="katex">x<\/span>/);
});

test('does not let math spans cross autolinks or bare GFM URLs', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  const links = [
    { source: '<mailto:a@b.c>', href: /href="mailto:a@b\.c"/, text: />mailto:a@b\.c<\/a>/ },
    { source: '<a@b.c>', href: /href="mailto:a@b\.c"/, text: />a@b\.c<\/a>/ },
    { source: 'https://example.com/path', href: /href="https:\/\/example\.com\/path"/, text: />https:\/\/example\.com\/path<\/a>/ },
    { source: 'http://example.com/path', href: /href="http:\/\/example\.com\/path"/, text: />http:\/\/example\.com\/path<\/a>/ },
    { source: 'www.example.com/path', href: /href="http:\/\/www\.example\.com\/path"/, text: />www\.example\.com\/path<\/a>/ },
  ];

  for (const { opening, closing } of delimiters) {
    for (const link of links) {
      calls.length = 0;
      const source = `${opening}before ${link.source} after${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], source);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, link.href);
      assert.match(html, link.text);
    }
  }
});

test('does not let math spans cross bare GFM URLs after punctuation', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const prefixes = [',', ';', ':', '!', '[', '{', ')'];
  const href = /href="https:\/\/example\.com\/p"/;

  for (const prefix of prefixes) {
    assert.match(renderer.render(`before${prefix}https://example.com/p after`), href, prefix);
    for (const { opening, closing } of delimiters) {
      calls.length = 0;
      const source = `${opening}before${prefix}https://example.com/p after${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], source);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, href, source);
    }
  }
});

test('does not let math spans cross CDATA sections', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const { opening, closing } of delimiters) {
    calls.length = 0;
    const source = `${opening}before <![CDATA[ x ]]> after${closing}`;
    const html = renderer.render(source);

    assert.deepEqual(calls, [], source);
    assert.doesNotMatch(html, /class="katex"/);
    assert.match(html, /&lt;!\[CDATA\[ x \]\]&gt;/);
  }
});

test('does not let math spans cross browser-parseable malformed HTML-like text', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const { opening, closing } of delimiters) {
    for (const context of ['<x.y>', '<foo=bar>']) {
      calls.length = 0;
      const source = `${opening}before ${context} after${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], source);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, /&lt;/);
    }
  }

  calls.length = 0;
  const comparisonHtml = renderer.render('$x<y$');
  assert.deepEqual(calls, ['x<y']);
  assert.match(comparisonHtml, /class="katex">x<y<\/span>/);
});

test('updates raw HTML state only for syntactically valid tags', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const { opening, closing } of delimiters) {
    for (const malformed of ['<x.y>', '<foo=bar>']) {
      calls.length = 0;
      const source = `${opening}before ${malformed} after${closing} ${opening}good${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, ['good'], source);
      assert.ok(html.includes(malformed.replace('<', '&lt;').replace('>', '&gt;')), source);
      assert.equal((html.match(/class="katex">good<\/span>/g) || []).length, 1, source);
    }
  }

  calls.length = 0;
  const closed = renderer.render('<b>$raw$</b> $outside$');
  assert.deepEqual(calls, ['outside']);
  assert.match(closed, /&lt;b&gt;\$raw\$&lt;\/b&gt;/);
  assert.equal((closed.match(/class="katex">outside<\/span>/g) || []).length, 1);

  calls.length = 0;
  const unclosed = renderer.render('<b>$raw$ $outside$');
  assert.deepEqual(calls, []);
  assert.match(unclosed, /\$raw\$ \$outside\$/);
  assert.doesNotMatch(unclosed, /class="katex"/);
});

test('renders TeX intervals without treating unmatched brackets as Markdown links', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const { opening, closing } of delimiters) {
    calls.length = 0;
    const source = `${opening}x \\in [0,\\infty)${closing}`;
    const html = renderer.render(source);

    assert.deepEqual(calls, ['x \\in [0,\\infty)'], source);
    assert.match(html, /class="katex">x \\in \[0,\\infty\)<\/span>/);
  }
});

test('renders a large unmatched-bracket formula without quadratic scanning', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const source = '$' + '['.repeat(30_000) + 'x$';
  const started = performance.now();

  const html = renderer.render(source);
  const elapsed = performance.now() - started;

  assert.match(html, /\[\[\[\[/);
  assert.ok(elapsed < 800, `expected 30k unmatched brackets to render below 800ms, got ${elapsed.toFixed(1)}ms`);
});

test('scales unmatched reference-label scans linearly', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const elapsedFor = size => {
    const started = performance.now();
    renderer.render('$' + '['.repeat(size) + 'x$');
    return performance.now() - started;
  };

  const small = elapsedFor(10_000);
  const large = elapsedFor(40_000);

  assert.ok(
    large < small * 5 + 100,
    `expected near-linear unmatched reference-label scans (10k=${small.toFixed(1)}ms, 40k=${large.toFixed(1)}ms)`,
  );
});

test('bounds unmatched-bracket scans across many formulas', () => {
  const renderer = makeRenderer({ getKatex: () => null });
  const source = '$[x$'.repeat(10_000);
  const started = performance.now();

  const html = renderer.render(source);
  const elapsed = performance.now() - started;

  assert.match(html, /\[x/);
  assert.ok(elapsed < 800, `expected 10k unmatched-bracket formulas to render below 800ms, got ${elapsed.toFixed(1)}ms`);
});

test('does not let math spans cross reference-style links or images', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];
  const references = [
    { source: '![alt][id]', definition: '[id]: https://x', rendered: /alt/ },
    { source: '[link][id]', definition: '[id]: https://x', rendered: /<a href="https:\/\/x">link<\/a>/ },
    { source: '[link][]', definition: '[link]: https://x', rendered: /<a href="https:\/\/x">link<\/a>/ },
    { source: '[id]', definition: '[id]: https://x', rendered: /<a href="https:\/\/x">id<\/a>/ },
  ];

  for (const { opening, closing } of delimiters) {
    for (const reference of references) {
      calls.length = 0;
      const source = `${opening}before ${reference.source} after${closing}\n\n${reference.definition}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, [], source);
      assert.doesNotMatch(html, /class="katex"/);
      assert.match(html, reference.rendered);
    }
  }
});

test('keeps rejected display-math closers from consuming later formulas', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const cases = [
    { source: '<b>$$bad</b> after$$ $$good$$', preserved: /&lt;b&gt;\$\$bad&lt;\/b&gt; after\$\$/ },
    { source: '<b>$$bad</b> after$$ x $$good$$', preserved: /&lt;b&gt;\$\$bad&lt;\/b&gt; after\$\$/ },
    { source: '<b>$$bad</b> after$$foo$$good$$', preserved: /&lt;b&gt;\$\$bad&lt;\/b&gt; after\$\$/ },
    { source: '$$before `code $$ inside` then $$good$$', preserved: /<code>code \$\$ inside<\/code>/ },
    { source: '$$before [link](https://x/$$href$$) then $$good$$', preserved: /<a href="https:\/\/x\/\$\$href\$\$">link<\/a>/ },
    { source: '$$before ![alt](https://x/$$img$$) then $$good$$', preserved: /before alt then / },
  ];

  for (const { source, preserved } of cases) {
    calls.length = 0;
    const html = renderer.render(source);

    assert.deepEqual(calls, ['good'], source);
    assert.match(html, preserved);
    assert.equal((html.match(/class="katex">good<\/span>/g) || []).length, 1, source);
  }
});

test('splits closed block HTML comments before later formulas', () => {
  const calls = [];
  const renderer = makeRenderer({
    getKatex: () => ({
      renderToString(source) {
        calls.push(source);
        return `<span class="katex">${source}</span>`;
      },
    }),
  });
  const delimiters = [
    { opening: '$', closing: '$' },
    { opening: '$$', closing: '$$' },
    { opening: '\\(', closing: '\\)' },
    { opening: '\\[', closing: '\\]' },
  ];

  for (const prefix of ['<!-- c --> ', '<script>x</script><!-- c --> ']) {
    for (const { opening, closing } of delimiters) {
      calls.length = 0;
      const source = `${prefix}${opening}ordinary${closing}`;
      const html = renderer.render(source);

      assert.deepEqual(calls, ['ordinary'], source);
      assert.match(html, /&lt;!-- c --&gt;/);
      assert.match(html, /class="katex">ordinary<\/span>/);
    }
  }
});
