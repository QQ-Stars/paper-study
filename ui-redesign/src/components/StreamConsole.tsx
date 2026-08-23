import { useEffect, useRef, useState } from 'react';

import type { StreamEvent } from '../api/types';

/* 统一的流式任务控制台：追加 NDJSON 事件日志，展示终态结果 */

export interface StreamState {
  running: boolean;
  lines: string[];
  summary: string;
  progress: string;
}

export function useStream() {
  const [state, setState] = useState<StreamState>({
    running: false,
    lines: [],
    summary: '',
    progress: '',
  });
  const anchorRef = useRef(0);

  const reset = () => {
    anchorRef.current += 1;
    setState({ running: false, lines: [], summary: '', progress: '' });
  };

  const begin = () => {
    anchorRef.current += 1;
    setState({ running: true, lines: [], summary: '', progress: '' });
  };

  const accept = (anchor: number, event: StreamEvent) => {
    if (anchor !== anchorRef.current) return;
    const isTerminal = event.type === 'done' || event.type === 'result';
    const describe = (): string => {
      if (event.type === 'result') {
        if (event.ok === false) return `失败：${String(event.error ?? '未知错误')}`;
        const added = typeof event.added === 'number' ? `新增 ${event.added} 篇` : '';
        const total = typeof event.total === 'number' ? `共 ${event.total} 项` : '';
        const graph =
          typeof event.edges === 'number' && typeof event.nodes === 'number'
            ? `${event.edges} 条引用边 / ${event.nodes} 个节点`
            : '';
        return [added, total, graph].filter(Boolean).join(' · ') || '完成';
      }
      const message =
        (typeof event.message === 'string' && event.message) ||
        (typeof event.label === 'string' && event.label) ||
        (typeof event.phase === 'string' && event.phase) ||
        (typeof event.line === 'string' && event.line) ||
        '';
      const count =
        typeof event.total === 'number' || typeof event.done === 'number'
          ? `（${String(event.done ?? '')}/${String(event.total ?? '')}）`
          : '';
      return message ? `${event.type}: ${message}${count}` : JSON.stringify(event).slice(0, 200);
    };
    /* 降噪：STAGE::/TOTAL:: 内部标记不入日志；PROG::i::n 聚合为头部实时进度（不刷屏） */
    const rawLine = typeof event.line === 'string' ? event.line : '';
    const progMatch = rawLine.match(/^PROG::(\d+)::(\d+)/);
    if (event.type === 'progress' && progMatch) {
      setState((prev) => ({ ...prev, progress: `${progMatch[1]} / ${progMatch[2]}` }));
      return;
    }
    const isNoise = event.type === 'progress' && /^(STAGE|TOTAL|DONE)::/.test(rawLine);
    /* REFERR::id::reason 人性化：裸标记转为可读的跳过提示 */
    let line = describe();
    if (event.type === 'progress' && rawLine.startsWith('REFERR::')) {
      const parts = rawLine.split('::');
      line = `跳过参考文献缺失：${String(parts[1] ?? '').slice(0, 32)}…（${parts[2] ?? 'not found'}）`;
    }
    setState((prev) => ({
      running: !isTerminal,
      progress: isTerminal ? '' : prev.progress,
      lines: isNoise ? prev.lines : [...prev.lines.slice(-200), line],
      summary: isTerminal ? describe() : prev.summary,
    }));
  };

  const fail = (anchor: number, error: unknown) => {
    if (anchor !== anchorRef.current) return;
    setState((prev) => ({
      running: false,
      progress: '',
      lines: [...prev.lines, `错误：${error instanceof Error ? error.message : String(error)}`],
      summary: '',
    }));
  };

  return { state, reset, begin, accept, fail, anchorRef };
}

export function StreamConsole({ state, placeholder }: { state: StreamState; placeholder?: string }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [state.lines.length]);

  if (state.lines.length === 0 && !state.running) return null;

  return (
    <div className="stream-console" role="log" aria-live="polite">
      <div className="stream-console__head">
        <span className={`stream-console__dot${state.running ? ' stream-console__dot--live' : ''}`} />
        {state.running
          ? `任务执行中…${state.progress ? `（进度 ${state.progress}）` : ''}`
          : state.summary || '任务结束'}
      </div>
      <ol className="stream-console__lines">
        {(state.lines.length > 0 ? state.lines : [placeholder ?? '等待输出…']).map((line, index) => (
          <li key={index}>{line}</li>
        ))}
        <div ref={endRef} />
      </ol>
    </div>
  );
}
