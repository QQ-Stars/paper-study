(function (scope) {
  let renderer = null;
  try {
    scope.importScripts('vendor/marked.min.js', 'vendor/katex/katex.min.js', 'markdown-rendering.js');
    renderer = scope.MarkdownRendering.createMarkdownRenderer({
      getMarked: () => scope.marked,
      getKatex: () => scope.katex,
    });
  } catch (error) {
    renderer = null;
  }
  scope.onmessage = function (event) {
    const message = event && event.data;
    const id = message && message.id;
    if (!message || !Number.isInteger(id) || typeof message.text !== 'string' || !renderer) {
      scope.postMessage({ id, error: true });
      return;
    }
    try {
      const html = renderer.render(message.text);
      if (typeof html !== 'string') throw new TypeError('Markdown renderer returned non-string HTML');
      scope.postMessage({ id, html });
    } catch (error) {
      scope.postMessage({ id, error: true });
    }
  };
}(self));
