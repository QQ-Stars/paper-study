import { Fragment, useEffect, useState, type ReactNode } from 'react';

import {
  plainTextDocument,
  type SafeDocument,
  type SafeNode,
} from './ast';
import { TrustedMathHtml } from './TrustedMathHtml';
import {
  createMarkdownWorkerClient,
  type MarkdownWorkerClient,
} from './workerClient';

interface ResolvedDocument {
  source: string;
  generation: number;
  document: SafeDocument;
}

function renderNodes(
  nodes: SafeNode[],
  path: string,
  headingLevelOffset: number,
): ReactNode[] {
  return nodes.map((node, index) => {
    const key = `${path}.${index}`;
    const renderChildren = (children: SafeNode[], childPath = key) => (
      renderNodes(children, childPath, headingLevelOffset)
    );
    switch (node.type) {
      case 'text':
        return <Fragment key={key}>{node.value}</Fragment>;
      case 'paragraph':
        return <p key={key}>{renderChildren(node.children)}</p>;
      case 'heading': {
        const level = Math.min(6, node.depth + headingLevelOffset);
        const Heading = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
        return (
          <Heading data-markdown-depth={node.depth} key={key}>
            {renderChildren(node.children)}
          </Heading>
        );
      }
      case 'strong':
        return <strong key={key}>{renderChildren(node.children)}</strong>;
      case 'emphasis':
        return <em key={key}>{renderChildren(node.children)}</em>;
      case 'deletion':
        return <del key={key}>{renderChildren(node.children)}</del>;
      case 'link':
        return (
          <a href={node.href} key={key} rel="noreferrer noopener">
            {renderChildren(node.children)}
          </a>
        );
      case 'inlineCode':
        return <code key={key}>{node.value}</code>;
      case 'code':
        return (
          <pre key={key}>
            <code data-language={node.language}>{node.value}</code>
          </pre>
        );
      case 'lineBreak':
        return <br key={key} />;
      case 'thematicBreak':
        return <hr key={key} />;
      case 'blockquote':
        return <blockquote key={key}>{renderChildren(node.children)}</blockquote>;
      case 'list':
        return node.ordered ? (
          <ol key={key} start={node.start}>{renderChildren(node.children)}</ol>
        ) : (
          <ul key={key}>{renderChildren(node.children)}</ul>
        );
      case 'listItem':
        return (
          <li
            data-task-state={node.checked === undefined ? undefined : (node.checked ? 'checked' : 'unchecked')}
            key={key}
          >
            {node.checked === undefined ? null : (
              <input
                aria-label={node.checked ? '已完成' : '未完成'}
                checked={node.checked}
                disabled
                type="checkbox"
              />
            )}
            {renderChildren(node.children)}
          </li>
        );
      case 'table': {
        const [header, ...rows] = node.children;
        return (
          <div
            aria-label="Markdown 表格"
            className="markdown-content__table-wrap"
            key={key}
            role="region"
            tabIndex={0}
          >
            <table>
              {header ? <thead>{renderChildren([header], `${key}.head`)}</thead> : null}
              {rows.length ? <tbody>{renderChildren(rows, `${key}.body`)}</tbody> : null}
            </table>
          </div>
        );
      }
      case 'tableRow':
        return <tr key={key}>{renderChildren(node.children)}</tr>;
      case 'tableCell': {
        const Cell = node.header ? 'th' : 'td';
        return (
          <Cell data-align={node.align ?? undefined} key={key}>
            {renderChildren(node.children)}
          </Cell>
        );
      }
      case 'math':
        return (
          <TrustedMathHtml
            className="markdown-content__math"
            display={node.display}
            key={key}
            value={node.value}
          />
        );
    }
  });
}

export interface MarkdownContentProps {
  source: string;
  generation: number;
  className?: string;
  headingLevelOffset?: 0 | 1 | 2 | 3 | 4 | 5;
  workerClientFactory?: () => MarkdownWorkerClient;
}

export function MarkdownContent({
  source,
  generation,
  className,
  headingLevelOffset = 0,
  workerClientFactory = createMarkdownWorkerClient,
}: MarkdownContentProps) {
  const [resolved, setResolved] = useState<ResolvedDocument | null>(null);
  const isCurrent = resolved?.source === source && resolved.generation === generation;
  const document = isCurrent ? resolved.document : plainTextDocument(source);

  useEffect(() => {
    let client: MarkdownWorkerClient;
    try {
      client = workerClientFactory();
    } catch {
      return undefined;
    }

    let mounted = true;
    void client.render(source, { generation }).then((nextDocument) => {
      if (!mounted) return;
      setResolved({ source, generation, document: nextDocument });
    }).catch(() => {
      if (!mounted) return;
      setResolved({ source, generation, document: plainTextDocument(source) });
    });

    return () => {
      mounted = false;
      client.dispose();
    };
  }, [generation, source, workerClientFactory]);

  return (
    <div
      className={className}
      data-markdown-state={isCurrent ? 'resolved' : 'plain-text'}
    >
      {renderNodes(document.nodes, 'markdown', headingLevelOffset)}
    </div>
  );
}
