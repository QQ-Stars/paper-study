(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.IngestRendering = api;
}(typeof window !== 'undefined' ? window : undefined, function () {
  function createIngestRenderer({ document }) {
    function renderQueryChips(box, queries, onRemove) {
      const values = Array.isArray(queries) ? queries.map(String) : [];
      box.dataset.qs = JSON.stringify(values);

      if (values.length === 0) {
        const placeholder = document.createElement('span');
        placeholder.className = 'placeholder';
        placeholder.textContent = '（无检索词）';
        box.replaceChildren(placeholder);
        return;
      }

      const chips = values.map((query, index) => {
        const chip = document.createElement('span');
        chip.className = 'iq-chip';

        const text = document.createElement('span');
        text.textContent = query;

        const removeButton = document.createElement('button');
        removeButton.className = 'iq-x';
        removeButton.type = 'button';
        removeButton.dataset.i = String(index);
        removeButton.textContent = '×';
        removeButton.addEventListener('click', () => onRemove(index));

        chip.append(text, removeButton);
        return chip;
      });
      box.replaceChildren(...chips);
    }

    function setDetail(main, sub, mainText, subText, warning) {
      if (main && mainText != null) main.textContent = mainText;
      if (sub && subText != null) {
        sub.textContent = subText;
        sub.className = warning ? 'ingd-sub ingd-warn' : 'ingd-sub';
      }
    }

    return { renderQueryChips, setDetail };
  }

  return { createIngestRenderer };
}));
