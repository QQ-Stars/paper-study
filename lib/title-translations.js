const TITLE_TRANSLATION_SYSTEM = [
  '你是专业的学术论文题名翻译助手。',
  '把英文论文题名翻译为忠实、精炼、自然的简体中文学术题名。',
  '保留必要的模型名、数据集名、缩写与专有名词。',
  '只返回一行中文题名，不要引号、Markdown、标签或解释。'
].join('\n');

function cleanTitleTranslation(value) {
  let text = String(value || '').trim()
    .replace(/^```(?:text|markdown)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (lines.length !== 1) return '';
  text = lines[0]
    .replace(/^(?:中文(?:标题|题名|翻译)|译名)\s*[：:]\s*/i, '')
    .replace(/^[“”"'‘’]+|[“”"'‘’]+$/g, '')
    .trim();
  if (!/[\u3400-\u9fff]/.test(text) || text.length > 300) return '';
  return text;
}

function createTitleTranslationService({ repository, chat }) {
  if (!repository || typeof chat !== 'function') throw new TypeError('repository and chat are required');
  return {
    pendingCount: () => repository.countMissingTitleTranslations(),
    async runBatch({ limit = 0, isCancelled = () => false, onEvent = () => {} } = {}) {
      const rows = repository.listMissingTitleTranslations(limit);
      const summary = { total: rows.length, done: 0, failed: [], cancelled: false };
      onEvent({ type: 'progress', stage: 'batch', total: rows.length });
      for (let index = 0; index < rows.length; index += 1) {
        if (isCancelled()) { summary.cancelled = true; break; }
        const row = rows[index];
        onEvent({ type: 'progress', stage: 'item', state: 'start', index: index + 1, total: rows.length, id: row.id, title: row.title });
        try {
          const raw = await chat([
            { role: 'system', content: TITLE_TRANSLATION_SYSTEM },
            { role: 'user', content: row.title }
          ], { temperature: 0.1, timeoutMs: 45000 });
          const titleZh = cleanTitleTranslation(raw);
          if (!titleZh) throw new Error('大模型未返回有效中文题名');
          const changed = repository.setTitleTranslationIfMissing(row.id, titleZh);
          if (changed) summary.done += 1;
          onEvent({ type: 'progress', stage: 'item', state: changed ? 'done' : 'skipped', index: index + 1, total: rows.length, id: row.id, title_zh: titleZh });
        } catch (error) {
          const failure = { id: row.id, title: row.title, error: String(error && error.message || error) };
          summary.failed.push(failure);
          onEvent({ type: 'progress', stage: 'item', state: 'failed', index: index + 1, total: rows.length, ...failure });
        }
      }
      return summary;
    }
  };
}

module.exports = { TITLE_TRANSLATION_SYSTEM, cleanTitleTranslation, createTitleTranslationService };
