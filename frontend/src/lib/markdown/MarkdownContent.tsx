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

function renderNodes(nodes: SafeNode[], path: string): ReactNode[] {
  return nodes.map((node, index) => {
    const key = `${path}.${index}`;
    switch (node.type) {
      case 'text':
        return <Fragment key={key}>{node.value}</Fragment>;
      case 'paragraph':
        return <p key={key}>{renderNodes(node.children, key)}</p>;
      case 'link':
        return (
          <a href={node.href} key={key} rel="noreferrer noopener">
            {renderNodes(node.children, key)}
          </a>
        );
      case 'code':
        return (
          <pre key={key}>
            <code data-language={node.language}>{node.value}</code>
          </pre>
        );
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
  workerClientFactory?: () => MarkdownWorkerClient;
}

export function MarkdownContent({
  source,
  generation,
  className,
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
      {renderNodes(document.nodes, 'markdown')}
    </div>
  );
}
