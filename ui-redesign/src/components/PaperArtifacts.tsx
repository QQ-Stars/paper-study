import { useEffect, useState } from 'react';

import { artifactApi } from '../api/client';
import type { Paper } from '../api/types';
import { MarkdownView } from './MarkdownView';

interface PaperArtifactsProps {
  paper: Paper;
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
}

export function PaperArtifacts({ paper, notify, reloadPapers }: PaperArtifactsProps) {
  const [explainer, setExplainer] = useState('');
  const [translation, setTranslation] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState('');
  const [logs, setLogs] = useState<string[]>([]);
  const [piece, setPiece] = useState('');
  const [pieceResult, setPieceResult] = useState('');

  useEffect(() => {
    setLogs([]);
    void artifactApi.getExplainer(paper.id).then(setExplainer);
    void artifactApi.getTranslation(paper.id).then(setTranslation);
    void artifactApi.getNote(paper.id).then(setNote);
  }, [paper.id]);

  const runStream = async (kind: 'explain' | 'translate') => {
    setBusy(kind);
    setLogs([]);
    const onEvent = (event: { type: string; message?: string; ok?: boolean; error?: string }) => {
      const text =
        event.message ?? (event.type === 'done' ? (event.ok ? '完成' : `失败：${event.error}`) : event.type);
      setLogs((prev) => [...prev.slice(-40), text]);
    };
    try {
      if (kind === 'explain') await artifactApi.explain(paper.id, onEvent);
      else await artifactApi.translate(paper.id, onEvent);
      if (kind === 'explain') setExplainer(await artifactApi.getExplainer(paper.id));
      else setTranslation(await artifactApi.getTranslation(paper.id));
      notify(kind === 'explain' ? '讲解已生成' : '全文翻译已完成');
    } catch (error) {
      notify(`任务失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setBusy('');
    }
  };

  const saveNote = async () => {
    await artifactApi.saveNote(paper.id, note);
    await reloadPapers();
    notify('笔记已保存');
  };

  const translatePiece = async () => {
    if (!piece.trim()) return;
    setPieceResult('翻译中…');
    const result = await artifactApi.translateText(piece.trim());
    setPieceResult(result.ok ? (result.translation ?? '') : `失败：${result.error}`);
  };

  return (
    <div className="artifacts">
      <section className="artifacts__block">
        <header className="artifacts__head">
          <h4>AI 讲解</h4>
          <div>
            <button type="button" className="btn btn--sm" onClick={() => void runStream('explain')} disabled={busy !== ''}>
              {explainer ? '重新生成' : '生成讲解'}
            </button>
          </div>
        </header>
        {busy === 'explain' && logs.length > 0 && (
          <p className="artifacts__log">{logs[logs.length - 1]}</p>
        )}
        {explainer ? (
          <div className="doc-viewer">
            <MarkdownView source={explainer} />
          </div>
        ) : (
          <p className="artifacts__empty">尚未生成讲解。</p>
        )}
      </section>

      <section className="artifacts__block">
        <header className="artifacts__head">
          <h4>全文中文翻译</h4>
          <div>
            <button type="button" className="btn btn--sm" onClick={() => void runStream('translate')} disabled={busy !== ''}>
              {translation ? '重新翻译' : '生成翻译'}
            </button>
          </div>
        </header>
        {busy === 'translate' && logs.length > 0 && (
          <p className="artifacts__log">{logs[logs.length - 1]}</p>
        )}
        {translation ? (
          <div className="doc-viewer">
            <MarkdownView source={translation} />
          </div>
        ) : (
          <p className="artifacts__empty">尚未生成翻译。</p>
        )}
      </section>

      <section className="artifacts__block">
        <header className="artifacts__head">
          <h4>研究笔记</h4>
          <button type="button" className="btn btn--sm" onClick={() => void saveNote()}>
            保存笔记
          </button>
        </header>
        <textarea
          className="input artifacts__note"
          aria-label="研究笔记"
          placeholder="记录你的阅读心得、疑问与关联…"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </section>

      <section className="artifacts__block">
        <header className="artifacts__head">
          <h4>划词翻译</h4>
          <button type="button" className="btn btn--sm" onClick={() => void translatePiece()} disabled={!piece.trim()}>
            翻译选段
          </button>
        </header>
        <textarea
          className="input artifacts__note"
          aria-label="待翻译文本"
          placeholder="粘贴任意英文段落（≤6000 字符）…"
          value={piece}
          onChange={(event) => setPiece(event.target.value)}
        />
        {pieceResult && (
          <div className="doc-viewer artifacts__piece">
            <MarkdownView source={pieceResult} />
          </div>
        )}
      </section>
    </div>
  );
}
