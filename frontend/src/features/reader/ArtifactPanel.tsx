import {
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, InputArea } from '@cloudflare/kumo';

import { artifactKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import { MarkdownContent } from '../../lib/markdown/MarkdownContent';
import {
  normalizeArtifactText,
  type ArtifactCommandState,
  type ArtifactKind,
} from './artifactSession';
import {
  useArtifactCommands,
  type ArtifactCommandOutcome,
} from './useArtifactCommands';
import './artifact-panel.css';

type ArtifactPanelTab = 'context' | ArtifactKind;

const artifactTabs: readonly { kind: ArtifactKind; label: string }[] = [
  { kind: 'note', label: '笔记' },
  { kind: 'explainer', label: '讲解' },
  { kind: 'translation', label: '翻译' },
];

const contextTab = { kind: 'context', label: '上下文' } as const;

const successMessages: Record<ArtifactKind, string> = {
  note: '笔记已保存，正在同步服务端内容。',
  explainer: '讲解已完成，正在同步服务端内容。',
  translation: '翻译已完成，正在同步服务端内容。',
};

const runningMessages: Record<ArtifactKind, string> = {
  note: '正在等待保存响应…',
  explainer: '正在接收讲解进度…',
  translation: '正在接收翻译进度…',
};

const failureLabels: Record<ArtifactKind, string> = {
  note: '笔记保存',
  explainer: '讲解',
  translation: '翻译',
};

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message.trim()
    : '未知错误';
}

interface CommandStatusProps {
  kind: ArtifactKind;
  command: ArtifactCommandState;
}

function CommandStatus({ kind, command }: CommandStatusProps) {
  if (command.status === 'idle') return null;
  if (command.status === 'running') {
    return (
      <p className="artifact-panel__status" role="status" aria-live="polite">
        {command.progress.at(-1) ?? runningMessages[kind]}
      </p>
    );
  }
  if (command.status === 'failure') {
    return (
      <p className="artifact-panel__status artifact-panel__status--error" role="alert">
        {failureLabels[kind]}失败：{command.error}
      </p>
    );
  }
  return (
    <p className="artifact-panel__status" role="status" aria-live="polite">
      {command.status === 'success'
        ? successMessages[kind]
        : '已停止接收响应；服务端可能仍在处理。'}
    </p>
  );
}

interface ArtifactBodyProps {
  kind: Exclude<ArtifactKind, 'note'>;
  source: string | undefined;
  generation: number;
  pending: boolean;
  error: unknown;
}

function ArtifactBody({
  kind,
  source,
  generation,
  pending,
  error,
}: ArtifactBodyProps) {
  const label = kind === 'explainer' ? '讲解' : '翻译';
  if (pending) {
    return <p className="artifact-panel__empty" role="status">正在读取{label}…</p>;
  }
  if (error) {
    return (
      <p className="artifact-panel__empty artifact-panel__status--error" role="alert">
        读取{label}失败：{errorMessage(error)}
      </p>
    );
  }
  const content = normalizeArtifactText(source);
  return content === null
    ? <p className="artifact-panel__empty">暂无{label}</p>
    : (
        <MarkdownContent
          className="artifact-panel__markdown"
          generation={generation}
          headingLevelOffset={2}
          source={content}
        />
      );
}

interface GeneratedArtifactProps extends ArtifactBodyProps {
  command: ArtifactCommandState;
  onGenerate(): void;
  onStop(): void;
}

function GeneratedArtifact({
  kind,
  command,
  onGenerate,
  onStop,
  ...bodyProps
}: GeneratedArtifactProps) {
  const label = kind === 'explainer' ? '讲解' : '翻译';
  const running = command.status === 'running';
  return (
    <div className="artifact-panel__pane">
      <div className="artifact-panel__actions">
        <Button type="button" disabled={running} onClick={onGenerate} size="sm" variant="primary">
          生成{label}
        </Button>
        {running ? (
          <Button
            type="button"
            className="artifact-panel__stop"
            onClick={onStop}
            size="sm"
            variant="outline"
          >
            停止接收{label}
          </Button>
        ) : null}
      </div>
      <CommandStatus kind={kind} command={command} />
      <ArtifactBody kind={kind} {...bodyProps} />
    </div>
  );
}

interface DraftState {
  paperId: string;
  generation: number;
  serverValue: string;
  value: string;
  dirty: boolean;
}

interface NoteArtifactProps {
  paperId: string;
  generation: number;
  initialContent: string;
  command: ArtifactCommandState;
  onSave(content: string): Promise<ArtifactCommandOutcome>;
  onStop(): void;
}

function NoteArtifact({
  paperId,
  generation,
  initialContent,
  command,
  onSave,
  onStop,
}: NoteArtifactProps) {
  const [draft, setDraft] = useState<DraftState>(() => ({
    paperId,
    generation,
    serverValue: initialContent,
    value: initialContent,
    dirty: false,
  }));
  const ownsDraft = draft.paperId === paperId && draft.generation === generation;
  const currentValue = !ownsDraft
    ? initialContent
    : draft.dirty || draft.serverValue === initialContent
      ? draft.value
      : initialContent;
  const running = command.status === 'running';
  const preview = normalizeArtifactText(currentValue);

  const save = () => {
    const fixedContent = currentValue;
    void onSave(fixedContent).then((outcome) => {
      if (outcome !== 'success') return;
      setDraft((current) => {
        if (
          current.paperId !== paperId
          || current.generation !== generation
          || current.value !== fixedContent
        ) {
          return current;
        }
        return { ...current, serverValue: fixedContent, dirty: false };
      });
    });
  };

  return (
    <div className="artifact-panel__pane">
      <label className="artifact-panel__editor">
        <span>笔记内容</span>
        <InputArea
          value={currentValue}
          onChange={(event) => setDraft({
            paperId,
            generation,
            serverValue: initialContent,
            value: (event.target as HTMLTextAreaElement).value,
            dirty: true,
          })}
        />
      </label>
      <div className="artifact-panel__actions">
        <Button type="button" disabled={running} onClick={save} size="sm" variant="primary">
          保存笔记
        </Button>
        {running ? (
          <Button
            type="button"
            className="artifact-panel__stop"
            onClick={onStop}
            size="sm"
            variant="outline"
          >
            停止等待保存
          </Button>
        ) : null}
      </div>
      <CommandStatus kind="note" command={command} />
      {preview === null
        ? <p className="artifact-panel__empty">暂无笔记</p>
        : (
            <MarkdownContent
              className="artifact-panel__markdown"
              generation={generation}
              headingLevelOffset={2}
              source={preview}
            />
          )}
    </div>
  );
}

export interface ArtifactPanelProps {
  paperId: string;
  generation: number;
  context?: ReactNode;
  className?: string;
}

export function ArtifactPanel({
  paperId,
  generation,
  context,
  className,
}: ArtifactPanelProps) {
  const tabs: readonly { kind: ArtifactPanelTab; label: string }[] = context
    ? [contextTab, ...artifactTabs]
    : artifactTabs;
  const [activeTab, setActiveTab] = useState<ArtifactPanelTab>(
    context ? 'context' : 'note',
  );
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const noteQuery = useQuery({
    queryKey: artifactKeys.note(paperId),
    queryFn: ({ signal }) => paperApi.getNote(paperId, signal),
  });
  const explainerQuery = useQuery({
    queryKey: artifactKeys.explainer(paperId),
    queryFn: ({ signal }) => paperApi.getExplainer(paperId, signal),
  });
  const translationQuery = useQuery({
    queryKey: artifactKeys.translation(paperId),
    queryFn: ({ signal }) => paperApi.getTranslation(paperId, signal),
  });
  const {
    commands,
    generateExplainer,
    generateTranslation,
    saveNote,
    stop,
  } = useArtifactCommands(paperId, generation);
  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    setActiveTab(tabs[nextIndex].kind);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <section
      aria-label="论文阅读工作台"
      className={['artifact-panel', className].filter(Boolean).join(' ')}
      data-has-context={Boolean(context)}
    >
      <header className="artifact-panel__header">
        <div>
          <p>WORKBENCH</p>
          <h2>阅读工作台</h2>
        </div>
        <span>{paperId}</span>
      </header>

      <div className="artifact-panel__tabs" role="tablist" aria-label="阅读工作台视图">
        {tabs.map((tab, index) => (
          <button
            aria-controls={`artifact-panel-${tab.kind}`}
            aria-selected={activeTab === tab.kind}
            id={`artifact-tab-${tab.kind}`}
            key={tab.kind}
            onClick={() => setActiveTab(tab.kind)}
            onKeyDown={(event) => moveTab(event, index)}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            role="tab"
            tabIndex={activeTab === tab.kind ? 0 : -1}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div
        aria-labelledby={`artifact-tab-${activeTab}`}
        className="artifact-panel__content"
        id={`artifact-panel-${activeTab}`}
        role="tabpanel"
        tabIndex={0}
      >
        {activeTab === 'context' ? context : null}
        {activeTab === 'note' ? (
          noteQuery.isPending
            ? <p className="artifact-panel__empty" role="status">正在读取笔记…</p>
            : noteQuery.error
              ? (
                  <p className="artifact-panel__empty artifact-panel__status--error" role="alert">
                    读取笔记失败：{errorMessage(noteQuery.error)}
                  </p>
                )
              : (
                  <NoteArtifact
                    command={commands.note}
                    generation={generation}
                    initialContent={noteQuery.data ?? ''}
                    onSave={saveNote}
                    onStop={() => stop('note')}
                    paperId={paperId}
                  />
                )
        ) : null}
        {activeTab === 'explainer' ? (
          <GeneratedArtifact
            command={commands.explainer}
            error={explainerQuery.error}
            generation={generation}
            kind="explainer"
            onGenerate={() => { void generateExplainer(false); }}
            onStop={() => stop('explainer')}
            pending={explainerQuery.isPending}
            source={explainerQuery.data}
          />
        ) : null}
        {activeTab === 'translation' ? (
          <GeneratedArtifact
            command={commands.translation}
            error={translationQuery.error}
            generation={generation}
            kind="translation"
            onGenerate={() => { void generateTranslation(); }}
            onStop={() => stop('translation')}
            pending={translationQuery.isPending}
            source={translationQuery.data}
          />
        ) : null}
      </div>
    </section>
  );
}
