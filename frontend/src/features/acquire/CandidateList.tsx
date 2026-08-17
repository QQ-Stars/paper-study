import { Table } from '@cloudflare/kumo';

import type { Candidate, Verification } from '../../lib/api/types';
import { candidateKey } from './acquireReducer';

interface CandidateListProps {
  candidates: Candidate[];
  selectedKeys: string[];
  verifications: Verification[];
  disabled?: boolean;
  onToggle: (key: string, selected: boolean) => void;
  onToggleAll: (selected: boolean) => void;
}

function relevancePercent(value: number | null): number {
  if (value === null || !Number.isFinite(value)) return 0;
  return Math.round(Math.min(1, Math.max(0, value)) * 100);
}

function verificationText(verification: Verification | undefined): string | null {
  if (!verification) return null;
  if (verification.error) return '核验失败';
  if (verification.skipped) return `源自 ${verification.sourceOfTruth}`;
  if (!verification.matched) return '仅预印本';
  return verification.changed ? '已核实 · 已更正' : '已核实';
}

export function CandidateList({
  candidates,
  selectedKeys,
  verifications,
  disabled = false,
  onToggle,
  onToggleAll,
}: CandidateListProps) {
  const selected = new Set(selectedKeys);
  const selectable = candidates.filter((candidate) => !candidate.inLibrary);
  const allSelected = selectable.length > 0
    && selectable.every((candidate) => selected.has(candidateKey(candidate)));

  if (candidates.length === 0) {
    return (
      <div className="acquire-empty">
        <strong>候选流尚未开始</strong>
        <p>提交研究方向后，只有服务器返回的真实候选会出现在这里。</p>
      </div>
    );
  }

  return (
    <section className="candidate-list" aria-label="检索候选">
      <div className="candidate-list__toolbar">
        <span>{selectedKeys.length} / {selectable.length} 已选择</span>
      </div>
      <Table className="candidate-list__items">
        <Table.Header>
          <Table.Row>
            <Table.CheckHead
              label="选择全部可入库候选"
              checked={allSelected}
              disabled={disabled || selectable.length === 0}
              onValueChange={(nextSelected) => onToggleAll(nextSelected)}
            />
            <Table.Head className="candidate-list__index-head">#</Table.Head>
            <Table.Head>候选</Table.Head>
            <Table.Head className="candidate-list__score-head">REL</Table.Head>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {candidates.map((candidate, index) => {
            const key = candidateKey(candidate);
            const verification = verifications[index];
            const verified = verificationText(verification);
            const title = candidate.title || candidate.sourceId;
            return (
              <Table.Row
                key={key}
                className={candidate.inLibrary ? 'candidate candidate--in-library' : 'candidate'}
              >
                <Table.CheckCell
                  label={`选择 ${title}`}
                  checked={!candidate.inLibrary && selected.has(key)}
                  disabled={disabled || candidate.inLibrary}
                  onValueChange={(nextSelected) => onToggle(key, nextSelected)}
                />
                <Table.Cell className="candidate__index">
                  {String(index + 1).padStart(2, '0')}
                </Table.Cell>
                <Table.Cell className="candidate__body">
                  <strong>{title}</strong>
                  <p>
                    {[candidate.venue || '未标注来源', candidate.year, candidate.type, candidate.topic]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                  <div className="candidate__badges">
                    <span>{candidate.source}</span>
                    {candidate.ccf ? <span>CCF {candidate.ccf}</span> : null}
                    {verified ? <span title={verification?.note}>{verified}</span> : null}
                    {candidate.inLibrary ? <span className="candidate__existing">已在库</span> : null}
                  </div>
                </Table.Cell>
                <Table.Cell
                  className="candidate__score"
                  aria-label={`相关度 ${relevancePercent(candidate.relevance)}%`}
                >
                  <strong>{relevancePercent(candidate.relevance)}</strong>
                  <span>REL</span>
                </Table.Cell>
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table>
    </section>
  );
}
