import type { Paper } from '../api/types';

interface DeleteConfirmDialogProps {
  papers: Paper[];
  running: boolean;
  progress: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/* 删除二次确认对话框：显示数量与标题摘要；执行中禁止取消关闭 */

export function DeleteConfirmDialog({
  papers,
  running,
  progress,
  onCancel,
  onConfirm,
}: DeleteConfirmDialogProps) {
  const shown = papers.slice(0, 8);
  const rest = papers.length - shown.length;

  return (
    <div
      className="danger-dialog-overlay"
      role="presentation"
      onClick={(event) => {
        if (!running && event.target === event.currentTarget) onCancel();
      }}
    >
      <div className="danger-dialog" role="alertdialog" aria-modal="true" aria-labelledby="danger-dialog-title">
        <header className="danger-dialog__head">
          <span className="danger-dialog__mark" aria-hidden="true">
            !
          </span>
          <div>
            <h3 id="danger-dialog-title">确认删除</h3>
            <small>删除不可撤销，论文及其讲解、翻译、复习计划将一并移除</small>
          </div>
        </header>

        <p className="danger-dialog__count">
          即将删除 <strong>{papers.length}</strong> 篇论文
        </p>

        <ul className="danger-dialog__list">
          {shown.map((paper) => (
            <li key={paper.id} title={paper.title}>
              {paper.title_zh || paper.title}
              <small>
                {paper.venue} {paper.year}
              </small>
            </li>
          ))}
          {rest > 0 && <li className="danger-dialog__more">… 以及其余 {rest} 篇</li>}
        </ul>

        <footer className="danger-dialog__foot">
          {running && <span className="danger-dialog__progress">{progress || '删除中…'}</span>}
          <div className="deep__actions">
            <button type="button" className="btn" onClick={onCancel} disabled={running}>
              取消
            </button>
            <button
              type="button"
              className="btn btn--danger"
              onClick={onConfirm}
              disabled={running || papers.length === 0}
            >
              {running ? '删除中…' : `删除 ${papers.length} 篇`}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
