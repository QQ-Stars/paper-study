import type { RefObject } from 'react';

import type {
  PublicationValidationResponse,
  ReproductionArtifact,
  ReproductionConclusion,
  ReproductionPublication,
  ReproductionProjectKind,
  ReproductionStatus,
} from '../api/types';
import { ArchiveIcon, CheckIcon, CloseIcon, DocumentIcon, UploadIcon } from './Icons';

export interface PublicationDraft {
  decision: 'draft' | 'approved' | 'revoked';
  stableSlug: string;
  publicTitle: string;
  publicSummary: string;
  aggregateConclusion: ReproductionConclusion | '';
  paperUrl: string;
  codeUrl: string;
  datasetUrlsText: string;
  publicArtifactIds: string[];
}

export const CONCLUSION_LABELS: Record<ReproductionConclusion, string> = {
  reproduced: '完全复现',
  partial: '部分复现',
  inconsistent: '结果存在偏差',
  not_reproduced: '未能复现',
};

const STATUS_LABELS: Record<ReproductionPublication['status'], string> = {
  draft: '草稿',
  published: '已发布',
  stale: '需重新发布',
  failed: '校验失败',
  revoked: '已撤回',
};

export function publicationDraftFrom(publication: ReproductionPublication): PublicationDraft {
  return {
    decision: publication.decision,
    stableSlug: publication.stableSlug ?? '',
    publicTitle: publication.publicTitle ?? '',
    publicSummary: publication.publicSummary ?? '',
    aggregateConclusion: publication.aggregateConclusion ?? '',
    paperUrl: publication.paperUrl ?? '',
    codeUrl: publication.codeUrl ?? '',
    datasetUrlsText: publication.datasetUrls.join('\n'),
    publicArtifactIds: publication.publicArtifactIds,
  };
}

interface PublicationDialogProps {
  open: boolean;
  dialogRef: RefObject<HTMLFormElement>;
  publication: ReproductionPublication | null;
  draft: PublicationDraft;
  artifacts: ReproductionArtifact[];
  projectStatus: ReproductionStatus;
  projectKind: ReproductionProjectKind;
  busy: boolean;
  validation: PublicationValidationResponse | null;
  artifactUrl: (projectId: string, artifactId: string) => string;
  onClose: () => void;
  onChange: (change: Partial<PublicationDraft>) => void;
  onSave: () => void;
  onValidate: () => void;
  onPreview: () => void;
  onPublish: () => void;
  onRevoke: () => void;
}

export function PublicationDialog({
  open,
  dialogRef,
  publication,
  draft,
  artifacts,
  projectStatus,
  projectKind,
  busy,
  validation,
  artifactUrl,
  onClose,
  onChange,
  onSave,
  onValidate,
  onPreview,
  onPublish,
  onRevoke,
}: PublicationDialogProps) {
  if (!open || !publication) return null;
  const isArticle = projectKind === 'article';
  const selectedArtifacts = new Set(draft.publicArtifactIds);
  const coverArtifactId = draft.publicArtifactIds.find((artifactId) => artifacts.some((artifact) => artifact.id === artifactId && artifact.mimeType.startsWith('image/')));
  const toggleArtifact = (artifactId: string) => {
    const next = new Set(selectedArtifacts);
    if (next.has(artifactId)) next.delete(artifactId);
    else next.add(artifactId);
    onChange({ publicArtifactIds: [...next] });
  };

  return (
    <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <form ref={dialogRef} className="reproduction__dialog reproduction__dialog--publication" role="dialog" aria-modal="true" aria-labelledby="publication-title" onSubmit={(event) => { event.preventDefault(); onSave(); }}>
        <div className="reproduction__dialog-head">
          <div>
            <span className="eyebrow">PUBLIC SHOWCASE</span>
            <h2 id="publication-title">{isArticle ? '文章公开发布' : '复现成果公开发布'}</h2>
          </div>
          <div className="reproduction__publication-head-actions">
            <span className={`reproduction__publication-status reproduction__publication-status--${publication.status}`}>{STATUS_LABELS[publication.status]}</span>
            <button type="button" className="btn btn--ghost btn--sm" aria-label="关闭公开发布面板" disabled={busy} onClick={onClose}><CloseIcon size={15} /></button>
          </div>
        </div>

        <div className="reproduction__publication-grid">
          <section className="reproduction__publication-main">
            <label>公开标题<input className="input" autoFocus required value={draft.publicTitle} onChange={(event) => onChange({ publicTitle: event.target.value })} /></label>
            <label>{isArticle ? '文章摘要' : '结论摘要'}<textarea className="input" required rows={4} value={draft.publicSummary} onChange={(event) => onChange({ publicSummary: event.target.value })} placeholder={isArticle ? '概括文章的核心观点和读者可以带走的内容。' : '说明复现是否达到原论文结果、关键证据和主要差异。'} /></label>
            <div className="reproduction__form-grid">
              <label>稳定 slug<input className="input" value={draft.stableSlug} disabled={Boolean(publication.lastExportedAt)} onChange={(event) => onChange({ stableSlug: event.target.value })} placeholder="vit-baseline" /></label>
              {!isArticle && <label>复现结论<select className="input" value={draft.aggregateConclusion} onChange={(event) => onChange({ aggregateConclusion: event.target.value as ReproductionConclusion | '' })}><option value="">选择结论</option>{Object.entries(CONCLUSION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
              <label>论文链接<input className="input" type="url" value={draft.paperUrl} onChange={(event) => onChange({ paperUrl: event.target.value })} placeholder="https://..." /></label>
              <label>代码链接<input className="input" type="url" value={draft.codeUrl} onChange={(event) => onChange({ codeUrl: event.target.value })} placeholder="https://github.com/..." /></label>
            </div>
            <label>数据集链接<textarea className="input" rows={3} value={draft.datasetUrlsText} onChange={(event) => onChange({ datasetUrlsText: event.target.value })} placeholder="每行一个 https:// 链接" /></label>
            <label className="reproduction__publication-approval"><input type="checkbox" checked={draft.decision === 'approved'} onChange={(event) => onChange({ decision: event.target.checked ? 'approved' : 'draft' })} /><span><CheckIcon size={15} />我已核验内容，并允许公开展示</span></label>
            <p className="reproduction__dialog-hint">{isArticle ? '文章状态为' : '项目状态为'}“{projectStatus === 'completed' ? '已完成' : '未完成'}”。发布会检查正文、链接、资源哈希和私有信息；发布过的 slug 不能修改。</p>
          </section>

          <aside className="reproduction__publication-assets" aria-label="公开附件选择">
            <div className="reproduction__section-head"><div><h2>公开附件</h2><p>仅勾选可公开访问的资源；首个勾选图片作为列表封面。</p></div><DocumentIcon size={17} /></div>
            {artifacts.length === 0 ? <p className="reproduction__muted">尚无可选附件。</p> : <ul>{artifacts.map((artifact) => <li key={artifact.id}><label><input type="checkbox" checked={selectedArtifacts.has(artifact.id)} onChange={() => toggleArtifact(artifact.id)} /><span className="reproduction__publication-asset-copy">{artifact.mimeType.startsWith('image/') ? <img src={artifactUrl(artifact.projectId, artifact.id)} alt="" /> : <DocumentIcon size={15} />}<span><strong>{artifact.filename}{coverArtifactId === artifact.id ? ' · 封面' : ''}</strong><small>{artifact.mimeType} · {Math.max(1, Math.ceil(artifact.sizeBytes / 1024))} KB</small></span></span></label></li>)}</ul>}
          </aside>
        </div>

        {validation && <section className={`reproduction__publication-validation${validation.valid ? ' is-valid' : ' is-invalid'}`} aria-live="polite"><strong>{validation.valid ? '公开内容校验通过' : '公开内容校验未通过'}</strong>{validation.errors.length > 0 && <ul>{validation.errors.map((item) => <li key={item}>{item}</li>)}</ul>}{validation.warnings.length > 0 && <p>{validation.warnings.join(' · ')}</p>}</section>}

        <div className="reproduction__dialog-actions reproduction__publication-actions">
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={onValidate}><CheckIcon size={14} /> 检查公开内容</button>
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={onPreview}><DocumentIcon size={14} /> Fluid 预览</button>
          <span className="reproduction__publication-actions-spacer" />
          {publication.contentHash && <button type="button" className="btn btn--ghost reproduction__danger-action" disabled={busy} onClick={onRevoke}><ArchiveIcon size={14} /> 撤回</button>}
          <button type="submit" className="btn btn--ghost" disabled={busy}>保存公开信息</button>
          <button type="button" className="btn btn--primary" disabled={busy || draft.decision !== 'approved'} onClick={onPublish}><UploadIcon size={14} /> {publication.status === 'published' ? '重新发布' : '发布'}</button>
        </div>
      </form>
    </div>
  );
}
