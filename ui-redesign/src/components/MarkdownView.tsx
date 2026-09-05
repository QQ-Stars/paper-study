import { useEffect, useMemo, useRef } from 'react';

import katex from 'katex';
import { marked, Renderer } from 'marked';

import 'katex/dist/katex.min.css';

/* Markdown 渲染：先把四种定界符的公式抽出占位保护，再走 marked，最后回填 KaTeX。
 * 落库内容实测存在四种写法：$$...$$、\[...\]（块级）与 $...$、\(...\)（行内）——
 * DeepSeek-OCR 与部分翻译输出主用 \( \) / \[ \] 变体。
 * 必须在 marked 之前保护：否则 \[...\] 会被当作链接文本、公式内 _ 会被 emphasis 吞掉。
 * 内容来自本地 LLM 生成文件，非用户可控输入。 */

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderKatex(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex.trim(), { displayMode, throwOnError: false });
  } catch {
    /* 非法公式：原样代码样式展示，不丢字、不残留占位符 */
    return `<code>${escapeHtml(tex)}</code>`;
  }
}

let mermaidSequence = 0;
let mermaidInitialized = false;
type MermaidApi = (typeof import('mermaid'))['default'];
let mermaidPromise: Promise<MermaidApi> | null = null;

async function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import('mermaid').then((module) => module.default);
  const api = await mermaidPromise;
  setupMermaid(api);
  return api;
}

function setupMermaid(api: MermaidApi): void {
  if (mermaidInitialized) return;
  api.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'base',
    themeVariables: {
      primaryColor: '#1b1d24',
      primaryTextColor: '#f4f0eb',
      primaryBorderColor: '#4a4d57',
      lineColor: '#c9535b',
      secondaryColor: '#20232c',
      tertiaryColor: '#15171d',
    },
  });
  mermaidInitialized = true;
}

function renderVisibleMermaid(root: HTMLElement): () => void {
  const blocks = [...root.querySelectorAll<HTMLElement>('[data-mermaid-source]')];
  if (blocks.length === 0) return () => undefined;
  let disposed = false;
  const render = async (block: HTMLElement) => {
    if (block.dataset.mermaidRendered || disposed) return;
    const source = block.textContent ?? '';
    if (!source.trim() || source.length > 50_000) {
      block.dataset.mermaidRendered = 'failed';
      block.setAttribute('aria-label', 'Mermaid 图表内容无效或过长');
      return;
    }
    block.dataset.mermaidRendered = 'pending';
    try {
      const api = await loadMermaid();
      const id = `research-mermaid-${++mermaidSequence}`;
      const { svg, bindFunctions } = await api.render(id, source, block);
      if (disposed || !block.isConnected) return;
      block.innerHTML = svg;
      bindFunctions?.(block);
      block.dataset.mermaidRendered = 'done';
    } catch {
      if (disposed || !block.isConnected) return;
      block.dataset.mermaidRendered = 'failed';
      block.setAttribute('aria-label', 'Mermaid 图表语法无效');
    }
  };
  if (typeof IntersectionObserver === 'undefined') {
    blocks.forEach((block) => void render(block));
    return () => { disposed = true; };
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      void render(entry.target as HTMLElement);
    });
  }, { rootMargin: '480px 0px' });
  blocks.forEach((block) => observer.observe(block));
  return () => {
    disposed = true;
    observer.disconnect();
  };
}

function renderMathAndMarkdown(source: string): string {
  const blocks: string[] = [];
  const placeholder = (index: number) => `\u0000KATEX${index}\u0000`;
  const protect = (tex: string, displayMode: boolean) => {
    blocks.push(renderKatex(tex, displayMode));
    return placeholder(blocks.length - 1);
  };

  let text = source
    /* 块级 $$...$$（可跨行） */
    .replace(/\$\$([\s\S]+?)\$\$/g, (_match, tex: string) => protect(tex, true))
    /* 块级 \[...\]（LaTeX 原生定界符，可跨行） */
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, tex: string) => protect(tex, true))
    /* 行内 \(...\) */
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, tex: string) => protect(tex, false))
    /* 行内 $...$：前导不能是 \ 或 $，首尾非空白，结束 $ 后不能再跟 $（防误吃 $$） */
    .replace(
      /(^|[^\\$])\$(?!\s)([^$\n]*?\S)\$(?!\$)/g,
      (_match, prefix: string, tex: string) => `${prefix}${protect(tex, false)}`,
    );

  const renderer = new Renderer();
  renderer.code = ({ text: code, lang }) => {
    const language = (lang ?? '').trim().toLowerCase();
    if (language === 'mermaid') {
      return `<pre class="md-mermaid" data-mermaid-source="true"><code>${escapeHtml(code)}</code></pre>`;
    }
    const safeLanguage = language.replace(/[^a-z0-9_-]/g, '') || 'text';
    return `<pre class="md-code"><code class="language-${safeLanguage}">${escapeHtml(code)}</code></pre>`;
  };
  renderer.html = ({ text: html }) => `<code class="md-escaped-html">${escapeHtml(html)}</code>`;
  const html = marked.parse(text, { async: false, gfm: true, breaks: true, renderer }) as string;
  return html.replace(/\u0000KATEX(\d+)\u0000/g, (_match, index: string) => {
    return blocks[Number(index)] ?? '';
  });
}

export function MarkdownView({ source }: { source: string }) {
  const html = useMemo(() => renderMathAndMarkdown(source), [source]);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!rootRef.current) return;
    return renderVisibleMermaid(rootRef.current);
  }, [html]);

  return <div ref={rootRef} className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
}
