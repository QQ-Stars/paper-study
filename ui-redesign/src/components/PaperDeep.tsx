import { useCallback, useEffect, useState } from 'react';

import { acquireApi, libraryApi, v2Api } from '../api/client';
import type { Candidate, Paper } from '../api/types';
import { PlusIcon } from './Icons';
import { StreamConsole, useStream } from './StreamConsole';

interface PaperDeepProps {
  paper: Paper;
  notify: (message: string) => void;
}

type SourceRow = Record<string, unknown>;

export function PaperDeep({ paper, notify }: PaperDeepProps) {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [artifacts, setArtifacts] = useState<SourceRow[]>([]);
  const [indexStatus, setIndexStatus] = useState<Record<string, unknown> | null>(null);
  const [pdfInfo, setPdfInfo] = useState<{ hasPdf: boolean; size: number; canDownload: boolean } | null>(null);
  const [recommendations, setRecommendations] = useState<Candidate[]>([]);
  const sourceStream = useStream();
  const recommendStream = useStream();

  const refresh = useCallback(async () => {
    try {
      const [sourceList, artifactList] = await Promise.all([
        v2Api.listSources(paper.id),
        v2Api.listArtifacts(paper.id),
      ]);
      setSources(sourceList.items);
      setArtifacts(artifactList.items);
      const firstSource =
        sourceList.items.length > 0 ? String(sourceList.items[0].id ?? '') : '';
      setIndexStatus(
        firstSource
          ? await v2Api.indexStatus(paper.id, firstSource).catch(() => null)
          : null,
      );
    } catch {
      setSources([]);
      setArtifacts([]);
      setIndexStatus(null);
    }
    setPdfInfo(await libraryApi.pdfStatus(paper.id).catch(() => null));
  }, [paper.id]);

  useEffect(() => {
    setRecommendations([]);
    void refresh();
  }, [refresh]);

  const enqueueSource = async (sourceMode: 'native' | 'ocr') => {
    try {
      const result = await v2Api.enqueueSource(paper.id, sourceMode);
      notify(
        `${sourceMode === 'native' ? '原生文本' : 'OCR'}源文档已入队（job ${String(result.job.id)}）`,
      );
      await refresh();
    } catch (error) {
      notify(`入队失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const firstSourceId = sources.length > 0 ? String(sources[0].id ?? '') : '';

  const enqueueArtifact = async (kind: 'classification' | 'metadata' | 'summary') => {
    if (!firstSourceId) {
      notify('请先建立源文档，再入队 AI 工件');
      return;
    }
    try {
      const result = await v2Api.enqueueArtifact(paper.id, kind, firstSourceId, 'native');
      notify(`${kind} 工件已入队（job ${String(result.job.id)}）`);
      await refresh();
    } catch (error) {
      notify(`入队失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const enqueueIndex = async () => {
    if (!firstSourceId) {
      notify('请先建立源文档，再建立分块索引');
      return;
    }
    try {
      await v2Api.enqueueIndex(paper.id, firstSourceId, 'native', true);
      notify('分块索引任务已入队（含嵌入）');
      await refresh();
    } catch (error) {
      notify(`入队失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const runRecommend = async () => {
    const anchor = recommendStream.anchorRef.current + 1;
    recommendStream.begin();
    setRecommendations([]);
    try {
      await acquireApi.recommend(paper.id, 6, (event) => {
        recommendStream.accept(anchor, event);
        if (event.type === 'done' || event.type === 'result') {
          setRecommendations(((event.candidates as Candidate[]) ?? []).slice(0, 6));
        }
      });
    } catch (error) {
      recommendStream.fail(anchor, error);
    }
  };

  const importOne = async (candidate: Candidate) => {
    const anchor = sourceStream.anchorRef.current + 1;
    sourceStream.begin();
    try {
      await acquireApi.ingestSelected({ candidates: [candidate] }, (event) =>
        sourceStream.accept(anchor, event),
      );
      notify('推荐论文已导入文献库');
    } catch (error) {
      sourceStream.fail(anchor, error);
    }
  };

  const exportObsidian = async () => {
    try {
      const result = await v2Api.obsidianExport(paper.id);
      notify(`Obsidian 导出任务已创建（job ${String((result.job as { id?: string }).id ?? '')}）`);
    } catch (error) {
      notify(`导出失败：${error instanceof Error ? error.message : error}`);
    }
  };

  return (
    <div className="deep">
      <section className="deep__block">
        <header className="artifacts__head">
          <h4>本地 PDF 与源文档</h4>
          <div className="deep__actions">
            <button type="button" className="btn btn--sm" onClick={() => void enqueueSource('native')}>
              建立原生源文档
            </button>
            <button type="button" className="btn btn--sm" onClick={() => void enqueueSource('ocr')}>
              OCR 源文档
            </button>
          </div>
        </header>
        <p className="deep__fact">
          {pdfInfo
            ? pdfInfo.hasPdf
              ? `本地 PDF ${(pdfInfo.size / 1024 / 1024).toFixed(1)} MB${pdfInfo.canDownload ? ' · 支持补下载' : ''}`
              : '本机暂无 PDF' + (pdfInfo.canDownload ? ' · 可从 arXiv 下载' : '')
            : 'PDF 状态加载中…'}
        </p>
        {sources.length > 0 ? (
          <ul className="deep__list">
            {sources.map((source, index) => (
              <li key={index}>
                <span className="badge badge--venue">{String(source.status ?? '')}</span>
                <span>
                  {String(source.mode ?? '')} · {String(source.id ?? '').slice(0, 18)}…
                </span>
                <small>{String(source.createdAt ?? '')}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="artifacts__empty">尚无 v2 源文档。源文档是分块索引与 AI 工件的前置条件。</p>
        )}
      </section>

      <section className="deep__block">
        <header className="artifacts__head">
          <h4>v2 AI 工件（durable 管线）</h4>
          <div className="deep__actions">
            <button type="button" className="btn btn--sm" onClick={() => void enqueueArtifact('classification')}>
              分类
            </button>
            <button type="button" className="btn btn--sm" onClick={() => void enqueueArtifact('metadata')}>
              元数据
            </button>
            <button type="button" className="btn btn--sm" onClick={() => void enqueueArtifact('summary')}>
              摘要
            </button>
          </div>
        </header>
        {artifacts.length > 0 ? (
          <ul className="deep__list">
            {artifacts.map((artifact, index) => (
              <li key={index}>
                <span className="badge badge--seal">{String(artifact.kind ?? '')}</span>
                <span>{String(artifact.status ?? '')}</span>
                <small>{String(artifact.updatedAt ?? '')}</small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="artifacts__empty">尚无 durable 工件记录。</p>
        )}
      </section>

      <section className="deep__block">
        <header className="artifacts__head">
          <h4>分块索引</h4>
          <button type="button" className="btn btn--sm" onClick={() => void enqueueIndex()}>
            建立/刷新索引
          </button>
        </header>
        {indexStatus ? (
          <pre className="deep__code">{JSON.stringify(indexStatus, null, 2)}</pre>
        ) : (
          <p className="artifacts__empty">暂无索引状态（需先建立源文档）。</p>
        )}
      </section>

      <section className="deep__block">
        <header className="artifacts__head">
          <h4>相似论文推荐</h4>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => void runRecommend()}
            disabled={recommendStream.state.running}
          >
            生成推荐
          </button>
        </header>
        <StreamConsole state={recommendStream.state} />
        <StreamConsole state={sourceStream.state} />
        {recommendations.length > 0 && (
          <ul className="deep__recs">
            {recommendations.map((candidate, index) => (
              <li key={index} className="card deep__rec">
                <div>
                  <strong>{candidate.title_zh || candidate.title}</strong>
                  <small>
                    {candidate.venue} {candidate.year} · 被引 {candidate.citations ?? 0}
                  </small>
                </div>
                <button type="button" className="btn btn--sm" onClick={() => void importOne(candidate)}>
                  <PlusIcon size={13} />
                  导入
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="deep__block">
        <header className="artifacts__head">
          <h4>Obsidian 导出</h4>
          <button type="button" className="btn btn--sm" onClick={() => void exportObsidian()}>
            导出本篇
          </button>
        </header>
        <p className="artifacts__empty">需先在「设置」中启用 Obsidian 并配置 Vault 路径。</p>
      </section>
    </div>
  );
}
