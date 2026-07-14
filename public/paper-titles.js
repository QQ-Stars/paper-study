(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.PaperTitles = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  function titleLines(paper) {
    const row = paper || {};
    return {
      primary: String(row.title || '').trim(),
      secondary: String(row.title_zh || '').trim()
    };
  }

  function searchableTitle(paper) {
    const lines = titleLines(paper);
    return `${lines.primary} ${lines.secondary}`.trim();
  }

  function resolveCurrentPaper(current, papers) {
    if (!current || !current.id || !Array.isArray(papers)) return null;
    return papers.find(paper => paper.id === current.id) || null;
  }

  function titleMarkup(paper, classes = {}) {
    const lines = titleLines(paper);
    const rootClass = classes.root || 'paper-title-stack';
    const primaryClass = classes.primary || 'paper-title-primary';
    const secondaryClass = classes.secondary || 'paper-title-secondary';
    return `<span class="${escapeHtml(rootClass)}"><span class="${escapeHtml(primaryClass)}">${escapeHtml(lines.primary)}</span>${
      lines.secondary ? `<span class="${escapeHtml(secondaryClass)}">${escapeHtml(lines.secondary)}</span>` : ''
    }</span>`;
  }

  return { escapeHtml, resolveCurrentPaper, searchableTitle, titleLines, titleMarkup };
});
