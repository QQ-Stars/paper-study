import type { StreamEvent } from '../api/types';

type SummaryRecord = Record<string, unknown>;

const BATCH_FIELDS = ['total', 'done', 'failed', 'skipped', 'skipped_no_pdf'] as const;

function isRecord(value: unknown): value is SummaryRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
/**
 * Convert the two representations used by the legacy batch agents into a
 * count.  Failure/skip fields are normally arrays while a few adapters emit
 * an already-counted number; accepting both keeps the terminal display
 * stable without making assumptions about item shape.
 */
function count(value: unknown): number | undefined {
  if (Array.isArray(value)) return value.length;
  if (typeof value !== 'number') return undefined;
  if (!Number.isSafeInteger(value) || value < 0) return undefined;
  return value;
}

function fieldValue(
  event: SummaryRecord,
  nested: SummaryRecord | undefined,
  field: (typeof BATCH_FIELDS)[number],
): number | undefined {
  /* A nested summary is authoritative when present.  Fall back to the
   * top-level field only when the nested object does not provide it. */
  if (nested && Object.prototype.hasOwnProperty.call(nested, field)) {
    return count(nested[field]);
  }
  return count(event[field]);
}

/**
 * Format a terminal stream event's batch counters for the compact console.
 * Returns an empty string for non-batch result events so callers can retain
 * their specialised summaries (search/import/graph/embedding, etc.).
 */
export function formatTerminalSummary(event: StreamEvent): string {
  if (event.ok === false) {
    return `失败：${String(event.error ?? '未知错误')}`;
  }

  /* Citation graph rebuilds use `failed` for external records that could not
   * be matched in Semantic Scholar.  That is partial coverage, not a failed
   * rebuild; keep the graph counters prominent instead of treating the event
   * as a generic batch summary. */
  const graphEdges = count(event.edges);
  const graphNodes = count(event.nodes);
  if (graphEdges !== undefined && graphNodes !== undefined) {
    const unmatched = count(event.failed) ?? 0;
    return `${graphEdges} 条引用边 / ${graphNodes} 个节点${
      unmatched > 0 ? ` · ${unmatched} 篇未匹配` : ''
    }`;
  }

  const eventRecord = event as SummaryRecord;
  const nested = isRecord(eventRecord.summary) ? eventRecord.summary : undefined;
  const total = fieldValue(eventRecord, nested, 'total');
  const done = fieldValue(eventRecord, nested, 'done');
  const failed = fieldValue(eventRecord, nested, 'failed');
  const skipped = fieldValue(eventRecord, nested, 'skipped');
  const skippedNoPdf = fieldValue(eventRecord, nested, 'skipped_no_pdf');

  /* `total` alone is common on embedding/search results; only treat an event
   * as a batch summary when it has a summary object or another batch counter.
   */
  const hasBatchSummary =
    nested !== undefined ||
    done !== undefined ||
    failed !== undefined ||
    skipped !== undefined ||
    skippedNoPdf !== undefined;
  if (!hasBatchSummary) return '';

  const skippedCount = skipped !== undefined ? skipped : skippedNoPdf ?? 0;
  return `共 ${total ?? 0} 项 · 完成 ${done ?? 0} 项 · 失败 ${failed ?? 0} 项 · 跳过 ${skippedCount} 项`;
}
