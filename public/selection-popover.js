(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SelectionPopover = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function number(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function clamp(value, min, max) {
    if (max < min) return min;
    return Math.max(min, Math.min(value, max));
  }

  function calculatePopupLayout(rect, viewport, options) {
    rect = rect || {};
    viewport = viewport || {};
    options = options || {};

    const margin = Math.max(0, number(options.margin, 8));
    const gap = Math.max(0, number(options.gap, 8));
    const viewportWidth = Math.max(margin * 2 + 1, number(viewport.width, 1024));
    const viewportHeight = Math.max(margin * 2 + 1, number(viewport.height, 768));
    const maxHeightRatio = clamp(number(options.maxHeightRatio, 0.72), 0.2, 0.95);
    const hardMaxHeight = Math.floor(Math.min(
      number(options.maxHeight, viewportHeight * maxHeightRatio),
      viewportHeight - margin * 2
    ));

    const preferredWidth = number(options.width, 420);
    const width = Math.floor(clamp(preferredWidth, Math.min(260, viewportWidth - margin * 2), viewportWidth - margin * 2));
    const left = Math.floor(clamp(number(rect.left, (viewportWidth - width) / 2), margin, viewportWidth - width - margin));

    const rectTop = clamp(number(rect.top, margin), margin, viewportHeight - margin);
    const rectBottom = clamp(number(rect.bottom, rectTop), margin, viewportHeight - margin);
    const spaceAbove = Math.max(1, Math.floor(rectTop - gap - margin));
    const spaceBelow = Math.max(1, Math.floor(viewportHeight - rectBottom - gap - margin));
    const preferAbove = rectBottom > viewportHeight * 0.55;
    const placeAbove = preferAbove ? spaceAbove >= 120 || spaceAbove >= spaceBelow : spaceBelow < 220 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(1, Math.min(hardMaxHeight, placeAbove ? spaceAbove : spaceBelow));

    if (placeAbove) {
      return {
        placement: 'above',
        left,
        width,
        top: 'auto',
        bottom: Math.floor(viewportHeight - rectTop + gap),
        maxHeight
      };
    }

    return {
      placement: 'below',
      left,
      width,
      top: Math.floor(clamp(rectBottom + gap, margin, viewportHeight - margin - maxHeight)),
      bottom: 'auto',
      maxHeight
    };
  }

  return { calculatePopupLayout };
});
