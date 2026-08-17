import { useMemo } from 'react';

import katex from 'katex';
import { marked } from 'marked';

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

  const html = marked.parse(text, { async: false, gfm: true, breaks: true }) as string;
  return html.replace(/\u0000KATEX(\d+)\u0000/g, (_match, index: string) => {
    return blocks[Number(index)] ?? '';
  });
}

export function MarkdownView({ source }: { source: string }) {
  const html = useMemo(() => renderMathAndMarkdown(source), [source]);
  return <div className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
}
