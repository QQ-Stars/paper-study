(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.IngestRendering = api;
}(typeof window !== 'undefined' ? window : undefined, function () {
  function createIngestRenderer({ document }) {
    function text(value) {
      return value == null ? '' : String(value);
    }

    function safeVenueClass(venueName) {
      const token = text(venueName).replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
      return token ? `v-${token}` : '';
    }

    function sourceLabel(source, sourceLabels) {
      const key = text(source);
      return sourceLabels && Object.prototype.hasOwnProperty.call(sourceLabels, key)
        ? text(sourceLabels[key])
        : key;
    }

    function createCandidateCard({ candidate, index, venueName, sourceLabels }) {
      const value = candidate || {};
      const inLibrary = Boolean(value.in_library);
      const card = document.createElement('label');
      card.className = inLibrary ? 'cand in-lib' : 'cand';

      const checkbox = document.createElement('input');
      checkbox.className = 'cand-ck';
      checkbox.type = 'checkbox';
      checkbox.dataset.i = String(index);
      checkbox.checked = !inLibrary;
      checkbox.disabled = inLibrary;

      const main = document.createElement('div');
      main.className = 'cand-main';

      const title = document.createElement('div');
      title.className = 'cand-title';
      title.textContent = text(value.title);

      const meta = document.createElement('div');
      meta.className = 'cand-meta';
      const venue = document.createElement('span');
      const venueClass = safeVenueClass(venueName);
      venue.className = venueClass ? `venue ${venueClass}` : 'venue';
      venue.textContent = `${text(venueName) || '—'} ${text(value.year)}`;
      meta.append(venue);

      const rank = text(value.ccf);
      if (/^[ABC]$/.test(rank)) {
        const ccf = document.createElement('span');
        ccf.className = `ccf ccf-${rank}`;
        ccf.title = `CCF ${rank} 类`;
        ccf.textContent = rank;
        meta.append(ccf);
      }

      const type = text(value.type);
      const topic = text(value.topic);
      if (type) meta.append(document.createTextNode(` · ${type}`));
      if (topic) meta.append(document.createTextNode(` · ${topic}`));

      const verification = value._verify;
      if (verification) {
        const sourceText = sourceLabel(verification.source_of_truth, sourceLabels);
        const badge = document.createElement('b');
        badge.className = 'vbadge';
        if (verification.skipped) {
          badge.className += ' src';
          badge.title = text(verification.note);
          badge.textContent = `源自${sourceText}`;
        } else if (verification.matched) {
          badge.className += ' ok';
          badge.title = `权威来源：${sourceText}`;
          badge.textContent = verification.changed ? '✓ 已核实 · 已更正' : '✓ 已核实';
        } else {
          badge.className += ' miss';
          badge.title = text(verification.note);
          badge.textContent = '仅预印本';
        }
        meta.append(document.createTextNode(' '), badge);
      }

      if (inLibrary) {
        const inLibraryTag = document.createElement('b');
        inLibraryTag.className = 'inlib-tag';
        inLibraryTag.textContent = '已在库';
        meta.append(document.createTextNode(' · '), inLibraryTag);
      }

      const numericRelevance = Number(value.relevance);
      const percent = Math.round(Math.max(0, Math.min(100, Number.isFinite(numericRelevance) ? numericRelevance * 100 : 0)));
      const relevance = document.createElement('div');
      relevance.className = 'cand-rel';
      relevance.title = `相关度 ${percent}%`;
      const track = document.createElement('div');
      track.className = 'cand-rel-track';
      const bar = document.createElement('div');
      bar.className = 'cand-rel-bar';
      bar.style.width = `${percent}%`;
      const percentText = document.createElement('span');
      percentText.textContent = String(percent);
      track.append(bar);
      relevance.append(track, percentText);

      main.append(title, meta);
      card.append(checkbox, main, relevance);
      return card;
    }

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

    return { createCandidateCard, renderQueryChips, setDetail };
  }

  return { createIngestRenderer };
}));
