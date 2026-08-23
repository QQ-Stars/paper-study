import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';

import { artifactApi } from '../api/client';
import { CloseIcon, SparkIcon } from './Icons';

interface SelectionRect {
  text: string;
  x: number;
  y: number;
}

type TransPhase =
  | { kind: 'idle' }
  | { kind: 'loading'; text: string }
  | { kind: 'ok'; text: string; result: string }
  | { kind: 'error'; text: string; error: string };

interface SelectionTranslateProps {
  children: ReactNode;
  onSuccess?: (source: string, translated: string) => void;
}

/* 划词翻译：监听容器内文本选择，浮层触发后端 POST /api/translate-text */

export function SelectionTranslate({ children, onSuccess }: SelectionTranslateProps) {
  const [selection, setSelection] = useState<SelectionRect | null>(null);
  const [phase, setPhase] = useState<TransPhase>({ kind: 'idle' });
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const captureSelection = useCallback(() => {
    const sel = window.getSelection();
    const text = (sel?.toString() ?? '').trim();
    if (!sel || sel.isCollapsed || !text || text.length < 2) {
      return;
    }
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    const active = document.activeElement;
    previousFocusRef.current = active instanceof HTMLElement ? active : null;
    setSelection({
      text: text.slice(0, 6000),
      x: Math.min(rect.left + rect.width / 2, window.innerWidth - 220),
      y: rect.bottom + 8,
    });
  }, []);

  const close = useCallback(() => {
    setSelection(null);
    setPhase({ kind: 'idle' });
    window.getSelection()?.removeAllRanges();
    window.requestAnimationFrame(() => {
      const previous = previousFocusRef.current;
      if (previous?.isConnected && previous !== document.body) previous.focus();
      else rootRef.current?.focus();
    });
  }, []);

  useEffect(() => {
    if (selection) triggerRef.current?.focus();
  }, [selection]);

  const panelVisible = phase.kind !== 'idle';

  useEffect(() => {
    if (panelVisible) dialogRef.current?.focus();
  }, [panelVisible]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [close]);

  const translate = async (text: string) => {
    setSelection(null);
    window.getSelection()?.removeAllRanges();
    setPhase({ kind: 'loading', text });
    try {
      const result = await artifactApi.translateText(text);
      /* 后端 /api/translate-text 返回字段为 text（兼容 translation 别名） */
      const translated = (result.text ?? result.translation ?? '').trim();
      if (result.ok && translated) {
        setPhase({ kind: 'ok', text, result: translated });
        try {
          onSuccess?.(text, translated);
        } catch {
          /* 历史持久化失败不应把已成功的翻译误报为失败。 */
        }
      } else {
        setPhase({ kind: 'error', text, error: result.error || '后端未返回译文' });
      }
    } catch (error) {
      setPhase({
        kind: 'error',
        text,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const trapDialogFocus = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) {
      event.preventDefault();
      event.currentTarget.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div ref={rootRef} tabIndex={-1} onMouseUp={captureSelection} onKeyUp={captureSelection}>
      {children}

      {selection && (
        <button
          ref={triggerRef}
          type="button"
          className="seltrans-trigger"
          style={{ left: selection.x, top: selection.y }}
          onClick={() => void translate(selection.text)}
        >
          <SparkIcon size={13} />
          划词翻译
        </button>
      )}

      {panelVisible && (
        <div className="seltrans-overlay" role="presentation" onClick={close}>
          <div
            ref={dialogRef}
            className="seltrans-panel"
            role="dialog"
            aria-modal="true"
            aria-label="划词翻译结果"
            tabIndex={-1}
            onKeyDown={trapDialogFocus}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="seltrans-panel__head">
              <span className="eyebrow">划词翻译 · POST /api/translate-text</span>
              <button type="button" className="btn btn--ghost btn--sm" aria-label="关闭" onClick={close}>
                <CloseIcon size={14} />
              </button>
            </header>

            {phase.kind === 'loading' && (
              <div className="seltrans-state">
                <span className="acquire__spinner" aria-hidden="true" />
                正在调用大模型翻译…
              </div>
            )}

            {phase.kind === 'error' && (
              <div className="seltrans-state seltrans-state--error">
                <p>翻译失败：{phase.error}</p>
                <div className="deep__actions">
                  <button type="button" className="btn btn--sm" onClick={() => void translate(phase.text)}>
                    重试
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={close}>
                    关闭
                  </button>
                </div>
              </div>
            )}

            {phase.kind === 'ok' && (
              <>
                <blockquote className="seltrans-origin">{phase.text}</blockquote>
                <p className="seltrans-result">{phase.result}</p>
                <div className="deep__actions seltrans-panel__foot">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={close}>
                    关闭
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
