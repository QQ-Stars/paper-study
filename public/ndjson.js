(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.Ndjson = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  async function streamNDJSON(url, body, onEvent, options = {}) {
    const fetchImpl = options.fetchImpl || globalThis.fetch;
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: options.signal
    });
    if (!response.ok) {
      const detail = (await response.text().catch(() => '')).trim();
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
    }
    if (!response.body || !response.body.getReader) {
      const event = await response.json();
      onEvent(event && event.type ? event : { type: 'result', candidates: event.candidates || [] });
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const consume = line => {
      const text = line.trim();
      if (text) onEvent(JSON.parse(text));
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        consume(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) consume(buffer);
  }

  return { streamNDJSON };
});
