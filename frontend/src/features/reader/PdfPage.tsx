import { useEffect, useRef, type CSSProperties } from 'react';
import 'pdfjs-dist/web/pdf_viewer.css';

import type {
  PdfPageSnapshot,
  PdfPageSurface,
} from '../../lib/pdf/PdfReaderSession';

export interface PdfPageSession {
  mountPage(surface: PdfPageSurface): () => void;
}

export interface PdfPageProps {
  session: PdfPageSession;
  paperId: string;
  generation: number;
  pageNumber: number;
  snapshot?: PdfPageSnapshot;
}

function pageErrorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message.trim()
    : '页面渲染失败';
}

export function PdfPage({
  session,
  paperId,
  generation,
  pageNumber,
  snapshot,
}: PdfPageProps) {
  const pageRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const status = snapshot?.status ?? 'idle';
  const hasDimensions = Boolean(
    snapshot && snapshot.width > 0 && snapshot.height > 0,
  );
  const pageStyle: CSSProperties = hasDimensions
    ? {
        width: snapshot?.width,
        height: snapshot?.height,
        minHeight: 0,
      }
    : {};

  useEffect(() => {
    const target = pageRef.current;
    const canvas = canvasRef.current;
    const textLayer = textLayerRef.current;
    if (!target || !canvas || !textLayer) return undefined;
    return session.mountPage({ pageNumber, target, canvas, textLayer });
  }, [pageNumber, session]);

  return (
    <article
      aria-busy={status === 'loading'}
      aria-label={`第 ${pageNumber} 页`}
      className="pdf-page"
      data-generation={generation}
      data-paper-id={paperId}
      data-pdf-page-number={pageNumber}
      data-status={status}
      ref={pageRef}
      style={pageStyle}
    >
      <canvas
        aria-hidden="true"
        className="pdf-page__canvas"
        ref={canvasRef}
      />
      <div
        aria-hidden="true"
        className="textLayer pdf-page__text-layer"
        ref={textLayerRef}
      />
      {status === 'loading' ? (
        <p
          className="pdf-page__state"
          role="status"
        >
          正在渲染第 {pageNumber} 页…
        </p>
      ) : null}
      {status === 'error' ? (
        <p
          className="pdf-page__state pdf-page__state--error"
          role="alert"
        >
          第 {pageNumber} 页渲染失败：{pageErrorMessage(snapshot?.error)}
        </p>
      ) : null}
    </article>
  );
}
