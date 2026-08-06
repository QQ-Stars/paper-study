/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata and slots with their component. */
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { artifactKeys, paperKeys, pdfKeys, reviewKeys } from '../../lib/api/keys';
import { paperApi, type PaperDraft } from '../../lib/api/paperApi';
import type { PaperListItem, PaperRecord, StudyStatus } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import {
  applyLibraryFilters,
  defaultLibraryFilters,
  reconcileLibrarySelection,
  type LibraryFilters,
  type LibrarySort,
  type LibrarySourceFilter,
} from './filters';
import { PaperDeleteConfirmation, PaperEditor } from './PaperEditor';
import { PaperPreview } from './PaperPreview';
import { PaperTable } from './PaperTable';
import './library.css';

const sortValues = new Set<LibrarySort>([
  'added',
  'relevance',
  'year',
  'citations',
  'title',
]);
const sourceValues = new Set<LibrarySourceFilter>(['all', 'seed', 'collected']);
const libraryMutationScope = { id: 'library-paper-write' } as const;
const emptyPapers: readonly PaperListItem[] = [];

interface MutationAudit {
  readonly phase: 'idle' | 'pending' | 'success' | 'error';
  readonly message: string;
}

export const handle = {
  title: '文献库',
  layout: 'inspector',
  inspector: LibraryInspectorSlot,
} satisfies WorkspaceRouteHandle;

export const ErrorBoundary = RouteErrorBoundary;

function uniqueValues(
  papers: readonly PaperListItem[],
  read: (paper: PaperListItem) => string | null,
): string[] {
  return [...new Set(papers.map(read).filter((value): value is string => Boolean(value)))]
    .sort((left, right) => left.localeCompare(right));
}

function filtersFromStore(
  filters: ReturnType<typeof useWorkspaceStore.getState>['filters']['library'],
  semanticScores: ReadonlyMap<string, number> | null,
): LibraryFilters {
  const sort = sortValues.has(filters.sort as LibrarySort)
    ? filters.sort as LibrarySort
    : defaultLibraryFilters.sort;
  const source = sourceValues.has(filters.source as LibrarySourceFilter)
    ? filters.source as LibrarySourceFilter
    : defaultLibraryFilters.source;
  const status = filters.status === '未开始'
    || filters.status === '学习中'
    || filters.status === '已理解'
    ? filters.status
    : 'all';
  return {
    query: filters.query,
    status,
    sort,
    venue: filters.venue || 'all',
    type: filters.type || 'all',
    topic: filters.topic || 'all',
    year: filters.year || 'all',
    source,
    favorite: Boolean(filters.favorite),
    semanticScores,
  };
}

function EmptyLibrary({ filters }: { readonly filters: LibraryFilters }) {
  let title = '没有匹配当前筛选的论文';
  let detail = '调整检索词或筛选条件后重试。';
  if (filters.favorite) {
    title = '收藏夹还是空的';
    detail = '收藏任意论文后，它会出现在这个视图。';
  } else if (filters.semanticScores != null) {
    title = '语义检索没有命中';
    detail = '换一个更具体的研究问题，或返回普通检索。';
  }
  return (
    <div className="library-route__empty" role="status">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '服务器拒绝了这次修改';
}

function patchPaperCache(
  queryClient: QueryClient,
  paperId: string,
  patch: Partial<PaperListItem>,
): void {
  queryClient.setQueryData<PaperListItem[]>(paperKeys.list(), (current) => (
    current?.map((paper) => paper.id === paperId ? { ...paper, ...patch } : paper)
  ));
}

function listPatchFromDraft(draft: PaperDraft): Partial<PaperListItem> {
  return {
    title: draft.title,
    titleZh: draft.titleZh ?? null,
    venue: draft.venue ?? null,
    year: draft.year ?? null,
    type: draft.type ?? null,
    topic: draft.topic ?? null,
    url: draft.url ?? null,
    pdfUrl: draft.pdfUrl ?? null,
    pdfPath: draft.pdfPath ?? null,
    tldr: draft.tldr ?? null,
    contribution: draft.contribution ?? null,
  };
}

function detailPatchFromDraft(draft: PaperDraft): Partial<PaperRecord> {
  return {
    title: draft.title,
    titleZh: draft.titleZh ?? null,
    venue: draft.venue ?? null,
    year: draft.year ?? null,
    type: draft.type ?? null,
    topic: draft.topic ?? null,
    url: draft.url ?? null,
    pdfUrl: draft.pdfUrl ?? null,
    pdfPath: draft.pdfPath ?? null,
    tldr: draft.tldr ?? null,
    contribution: draft.contribution ?? null,
    abstract: draft.abstract ?? null,
    authors: draft.authors ?? [],
  };
}

function draftFromPaper(
  paper: PaperListItem,
  detail: PaperRecord | null | undefined,
): PaperDraft {
  const matchingDetail = detail?.id === paper.id ? detail : null;
  return {
    title: matchingDetail?.title ?? paper.title,
    titleZh: matchingDetail?.titleZh ?? paper.titleZh,
    authors: matchingDetail?.authors ?? [],
    venue: matchingDetail?.venue ?? paper.venue,
    year: matchingDetail?.year ?? paper.year,
    type: matchingDetail?.type ?? paper.type,
    topic: matchingDetail?.topic ?? paper.topic,
    url: matchingDetail?.url ?? paper.url,
    pdfUrl: matchingDetail?.pdfUrl ?? paper.pdfUrl,
    pdfPath: matchingDetail?.pdfPath ?? paper.pdfPath,
    tldr: matchingDetail?.tldr ?? paper.tldr,
    abstract: matchingDetail?.abstract ?? null,
    contribution: matchingDetail?.contribution ?? paper.contribution,
  };
}

function useLibraryLedgerMutations() {
  const queryClient = useQueryClient();
  const setSelectedId = useWorkspaceStore((state) => state.setWorkspaceSelectionId);
  const [audit, setAudit] = useState<MutationAudit>({
    phase: 'idle',
    message: '',
  });
  const favorite = useMutation<
    void,
    unknown,
    { paperId: string; favorite: boolean },
    { previous: boolean }
  >({
    mutationKey: ['papers', 'favorite'],
    scope: libraryMutationScope,
    mutationFn: ({ paperId, favorite: nextFavorite }) => (
      paperApi.setFavorite(paperId, nextFavorite)
    ),
    onMutate: async ({ paperId, favorite: nextFavorite }) => {
      setAudit({ phase: 'pending', message: `正在保存 ${paperId} 的收藏状态` });
      await queryClient.cancelQueries({ queryKey: paperKeys.list() });
      const previous = queryClient
        .getQueryData<PaperListItem[]>(paperKeys.list())
        ?.find((paper) => paper.id === paperId)?.favorite ?? false;
      patchPaperCache(queryClient, paperId, { favorite: nextFavorite });
      return { previous };
    },
    onError: (error, { paperId }, context) => {
      patchPaperCache(queryClient, paperId, { favorite: context?.previous ?? false });
      setAudit({ phase: 'error', message: `收藏状态保存失败：${errorText(error)}` });
    },
    onSuccess: (_, { paperId, favorite: nextFavorite }) => {
      patchPaperCache(queryClient, paperId, { favorite: nextFavorite });
      setAudit({ phase: 'success', message: `已保存 ${paperId} 的收藏状态` });
    },
  });
  const status = useMutation<
    void,
    unknown,
    { paperId: string; status: StudyStatus },
    { previous: StudyStatus }
  >({
    mutationKey: ['papers', 'status'],
    scope: libraryMutationScope,
    mutationFn: ({ paperId, status: nextStatus }) => (
      paperApi.setStatus(paperId, nextStatus)
    ),
    onMutate: async ({ paperId, status: nextStatus }) => {
      setAudit({ phase: 'pending', message: `正在保存 ${paperId} 的学习状态` });
      await queryClient.cancelQueries({ queryKey: paperKeys.list() });
      const previous = queryClient
        .getQueryData<PaperListItem[]>(paperKeys.list())
        ?.find((paper) => paper.id === paperId)?.status ?? '未开始';
      patchPaperCache(queryClient, paperId, { status: nextStatus });
      return { previous };
    },
    onError: (error, { paperId }, context) => {
      patchPaperCache(queryClient, paperId, { status: context?.previous ?? '未开始' });
      setAudit({ phase: 'error', message: `学习状态保存失败：${errorText(error)}` });
    },
    onSuccess: (_, { paperId, status: nextStatus }) => {
      patchPaperCache(queryClient, paperId, { status: nextStatus });
      void queryClient.invalidateQueries({ queryKey: reviewKeys.list() });
      setAudit({ phase: 'success', message: `已保存 ${paperId} 的学习状态` });
    },
  });
  const add = useMutation<string, unknown, PaperDraft>({
    mutationKey: ['papers', 'add'],
    scope: libraryMutationScope,
    mutationFn: (draft) => paperApi.addPaper(draft),
    onMutate: () => {
      setAudit({ phase: 'pending', message: '正在添加论文记录' });
    },
    onError: (error) => {
      setAudit({ phase: 'error', message: `论文添加失败：${errorText(error)}` });
    },
    onSuccess: async (paperId) => {
      await queryClient.invalidateQueries({ queryKey: paperKeys.list(), exact: true });
      setSelectedId(paperId);
      setAudit({ phase: 'success', message: `已添加论文 ${paperId}` });
    },
  });
  const update = useMutation<
    number,
    unknown,
    { paperId: string; draft: PaperDraft },
    {
      previousPapers: PaperListItem[] | undefined;
      previousDetail: PaperRecord | null | undefined;
      hadDetailQuery: boolean;
    }
  >({
    mutationKey: ['papers', 'update'],
    scope: libraryMutationScope,
    mutationFn: ({ paperId, draft }) => paperApi.updatePaper(paperId, draft),
    onMutate: async ({ paperId, draft }) => {
      setAudit({ phase: 'pending', message: `正在保存 ${paperId} 的论文元数据` });
      const detailKey = paperKeys.detail(paperId);
      await Promise.all([
        queryClient.cancelQueries({ queryKey: paperKeys.list(), exact: true }),
        queryClient.cancelQueries({ queryKey: detailKey, exact: true }),
      ]);
      const previousPapers = queryClient.getQueryData<PaperListItem[]>(paperKeys.list());
      const previousDetail = queryClient.getQueryData<PaperRecord | null>(detailKey);
      const hadDetailQuery = queryClient.getQueryState(detailKey) != null;
      patchPaperCache(queryClient, paperId, listPatchFromDraft(draft));
      queryClient.setQueryData<PaperRecord | null>(detailKey, (current) => (
        current == null ? current : { ...current, ...detailPatchFromDraft(draft) }
      ));
      return { previousPapers, previousDetail, hadDetailQuery };
    },
    onError: (error, { paperId }, context) => {
      if (context?.previousPapers === undefined) {
        queryClient.removeQueries({ queryKey: paperKeys.list(), exact: true });
      } else {
        queryClient.setQueryData(paperKeys.list(), context.previousPapers);
      }
      const detailKey = paperKeys.detail(paperId);
      if (!context?.hadDetailQuery) {
        queryClient.removeQueries({ queryKey: detailKey, exact: true });
      } else {
        queryClient.setQueryData(detailKey, context.previousDetail);
      }
      setAudit({ phase: 'error', message: `论文编辑失败：${errorText(error)}` });
    },
    onSuccess: (_, { paperId, draft }) => {
      patchPaperCache(queryClient, paperId, listPatchFromDraft(draft));
      queryClient.setQueryData<PaperRecord | null>(paperKeys.detail(paperId), (current) => (
        current == null ? current : { ...current, ...detailPatchFromDraft(draft) }
      ));
      void queryClient.invalidateQueries({
        queryKey: paperKeys.list(),
        exact: true,
        refetchType: 'none',
      });
      void queryClient.invalidateQueries({
        queryKey: paperKeys.detail(paperId),
        exact: true,
        refetchType: 'none',
      });
      setAudit({ phase: 'success', message: `已保存 ${paperId} 的论文元数据` });
    },
  });
  const remove = useMutation<
    void,
    unknown,
    { paperId: string; selectionId: string | null }
  >({
    mutationKey: ['papers', 'delete'],
    scope: libraryMutationScope,
    mutationFn: ({ paperId }) => paperApi.deletePaper(paperId),
    onMutate: async ({ paperId }) => {
      setAudit({ phase: 'pending', message: `正在删除论文 ${paperId}` });
      await Promise.all([
        queryClient.cancelQueries({ queryKey: paperKeys.list(), exact: true }),
        queryClient.cancelQueries({ queryKey: paperKeys.detail(paperId), exact: true }),
        queryClient.cancelQueries({ queryKey: artifactKeys.all(paperId) }),
        queryClient.cancelQueries({ queryKey: pdfKeys.status(paperId), exact: true }),
      ]);
    },
    onError: (error) => {
      setAudit({ phase: 'error', message: `论文删除失败：${errorText(error)}` });
    },
    onSuccess: (_, { paperId, selectionId }) => {
      queryClient.setQueryData<PaperListItem[]>(paperKeys.list(), (current) => (
        current?.filter((paper) => paper.id !== paperId)
      ));
      queryClient.removeQueries({ queryKey: paperKeys.detail(paperId), exact: true });
      queryClient.removeQueries({ queryKey: artifactKeys.all(paperId) });
      queryClient.removeQueries({ queryKey: pdfKeys.status(paperId), exact: true });
      if (selectionId === paperId) setSelectedId(null);
      void queryClient.invalidateQueries({ queryKey: reviewKeys.list(), exact: true });
      setAudit({ phase: 'success', message: `已删除论文 ${paperId}` });
    },
  });
  const pendingPaperIds = new Set<string>();
  if (favorite.isPending && favorite.variables) pendingPaperIds.add(favorite.variables.paperId);
  if (status.isPending && status.variables) pendingPaperIds.add(status.variables.paperId);
  if (update.isPending && update.variables) pendingPaperIds.add(update.variables.paperId);
  if (remove.isPending && remove.variables) pendingPaperIds.add(remove.variables.paperId);

  return { add, audit, favorite, pendingPaperIds, remove, status, update };
}

export function Component() {
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const storedFilters = useWorkspaceStore((state) => state.filters.library);
  const setSurfaceFilters = useWorkspaceStore((state) => state.setSurfaceFilters);
  const selectedId = useWorkspaceStore((state) => state.workspaceSelectionId);
  const setSelectedId = useWorkspaceStore((state) => state.setWorkspaceSelectionId);
  const [batchSelection, setBatchSelection] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [semanticScores, setSemanticScores] = useState<ReadonlyMap<string, number> | null>(null);
  const [semanticError, setSemanticError] = useState('');
  const [addEditorOpen, setAddEditorOpen] = useState(false);
  const semanticGeneration = useRef(0);
  const mutations = useLibraryLedgerMutations();
  const semanticSearch = useMutation({
    mutationKey: ['papers', 'semantic-search'],
    mutationFn: ({ query: requestedQuery }: { query: string; generation: number }) => (
      workspaceApi.semanticSearch(requestedQuery, 60)
    ),
    onMutate: () => setSemanticError(''),
    onSuccess: (result, { generation }) => {
      if (generation !== semanticGeneration.current || !result.ok) return;
      setSemanticScores(new Map(result.results.map((hit) => [hit.id, hit.score])));
    },
    onError: (error, { generation }) => {
      if (generation !== semanticGeneration.current) return;
      setSemanticScores(null);
      setSemanticError(errorText(error));
    },
  });
  const papers = query.data ?? emptyPapers;
  const filters = useMemo(
    () => filtersFromStore(storedFilters, semanticScores),
    [semanticScores, storedFilters],
  );
  const visiblePapers = useMemo(
    () => applyLibraryFilters(papers, filters),
    [filters, papers],
  );
  const validBatchSelection = useMemo(() => {
    const availableIds = new Set(papers.map((paper) => paper.id));
    return new Set([...batchSelection].filter((paperId) => availableIds.has(paperId)));
  }, [batchSelection, papers]);
  const facets = useMemo(() => ({
    venues: uniqueValues(papers, (paper) => paper.venue),
    types: uniqueValues(papers, (paper) => paper.type),
    topics: uniqueValues(papers, (paper) => paper.topic),
    years: uniqueValues(papers, (paper) => paper.year).reverse(),
  }), [papers]);

  useEffect(() => {
    const nextSelection = reconcileLibrarySelection(visiblePapers, selectedId);
    if (nextSelection !== selectedId) setSelectedId(nextSelection);
  }, [selectedId, setSelectedId, visiblePapers]);

  const patchFilters = (patch: Partial<LibraryFilters>) => {
    setSurfaceFilters('library', patch);
  };
  const toggleBatch = (paperId: string) => {
    setBatchSelection((current) => {
      const next = new Set(current);
      if (next.has(paperId)) next.delete(paperId);
      else next.add(paperId);
      return next;
    });
  };
  const openPaper = (paperId: string) => {
    setSelectedId(paperId);
    void navigate(`/reader/${encodeURIComponent(paperId)}`);
  };

  return (
    <section className="library-route" aria-label="文献库">
      <header className="library-route__intro">
        <div>
          <p className="library-route__eyebrow">LIBRARY / EVIDENCE LEDGER</p>
          <h2>文献库</h2>
          <p>{papers.length} 篇论文 · {visiblePapers.length} 篇当前可见</p>
        </div>
        <button
          type="button"
          className="library-route__add"
          onClick={() => {
            mutations.add.reset();
            setAddEditorOpen(true);
          }}
        >
          添加论文
        </button>
      </header>

      <div className="library-filters" aria-label="文献筛选">
        <label className="library-filters__search">
          <span>检索</span>
          <input
            type="search"
            aria-label="搜索文献"
            value={filters.query}
            placeholder="题名 / 中文题名 / venue / type / topic"
            onChange={(event) => {
              semanticGeneration.current += 1;
              setSemanticScores(null);
              setSemanticError('');
              patchFilters({ query: event.target.value });
            }}
          />
        </label>
        <label>
          <span>来源</span>
          <select
            aria-label="来源"
            value={filters.source}
            onChange={(event) => patchFilters({ source: event.target.value as LibrarySourceFilter })}
          >
            <option value="all">全部来源</option>
            <option value="seed">种子文献</option>
            <option value="collected">后续采集</option>
          </select>
        </label>
        <label>
          <span>状态</span>
          <select
            aria-label="状态"
            value={filters.status}
            onChange={(event) => patchFilters({ status: event.target.value as 'all' | StudyStatus })}
          >
            <option value="all">全部状态</option>
            <option value="未开始">未开始</option>
            <option value="学习中">学习中</option>
            <option value="已理解">已理解</option>
          </select>
        </label>
        <label>
          <span>年份</span>
          <select aria-label="年份" value={filters.year} onChange={(event) => patchFilters({ year: event.target.value })}>
            <option value="all">全部年份</option>
            {facets.years.map((year) => <option key={year} value={year}>{year}</option>)}
          </select>
        </label>
        <label>
          <span>会议</span>
          <select aria-label="会议" value={filters.venue} onChange={(event) => patchFilters({ venue: event.target.value })}>
            <option value="all">全部会议</option>
            {facets.venues.map((venue) => <option key={venue} value={venue}>{venue}</option>)}
          </select>
        </label>
        <label>
          <span>类型</span>
          <select aria-label="类型" value={filters.type} onChange={(event) => patchFilters({ type: event.target.value })}>
            <option value="all">全部类型</option>
            {facets.types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
        <label>
          <span>主题</span>
          <select aria-label="主题" value={filters.topic} onChange={(event) => patchFilters({ topic: event.target.value })}>
            <option value="all">全部主题</option>
            {facets.topics.map((topic) => <option key={topic} value={topic}>{topic}</option>)}
          </select>
        </label>
        <label>
          <span>排序</span>
          <select aria-label="排序" value={filters.sort} onChange={(event) => patchFilters({ sort: event.target.value as LibrarySort })}>
            <option value="added">最近加入</option>
            <option value="relevance">相关度</option>
            <option value="year">年份</option>
            <option value="citations">引用数</option>
            <option value="title">题名</option>
          </select>
        </label>
        <label className="library-filters__favorite">
          <input
            type="checkbox"
            checked={filters.favorite}
            onChange={(event) => patchFilters({ favorite: event.target.checked })}
          />
          <span>仅看收藏</span>
        </label>
        <button
          type="button"
          className="library-filters__semantic"
          disabled={semanticSearch.isPending || (semanticScores == null && !filters.query.trim())}
          onClick={() => {
            if (semanticScores != null) {
              semanticGeneration.current += 1;
              setSemanticScores(null);
              setSemanticError('');
              return;
            }
            const requestedQuery = filters.query.trim();
            const generation = semanticGeneration.current + 1;
            semanticGeneration.current = generation;
            semanticSearch.mutate({ query: requestedQuery, generation });
          }}
        >
          {semanticSearch.isPending
            ? '语义检索中…'
            : semanticScores == null ? '语义检索' : '返回普通检索'}
        </button>
      </div>

      {semanticError ? <p className="library-route__audit library-route__audit--error" role="alert">语义检索失败：{semanticError}</p> : null}

      <div className="library-route__batch" aria-live="polite">
        <span>已选择 {validBatchSelection.size} 篇</span>
        {validBatchSelection.size > 0 ? (
          <button type="button" onClick={() => setBatchSelection(new Set())}>清除选择</button>
        ) : null}
      </div>

      {mutations.audit.phase !== 'idle' ? (
        <p
          className={`library-route__audit library-route__audit--${mutations.audit.phase}`}
          role={mutations.audit.phase === 'error' ? 'alert' : 'status'}
        >
          {mutations.audit.message}
        </p>
      ) : null}

      {query.isPending ? (
        <div className="library-route__state" role="status">正在载入真实文献台账…</div>
      ) : query.isError ? (
        <div className="library-route__state" role="alert">
          <strong>无法载入文献库</strong>
          <button type="button" onClick={() => void query.refetch()}>重试</button>
        </div>
      ) : visiblePapers.length === 0 ? (
        <EmptyLibrary filters={filters} />
      ) : (
        <PaperTable
          papers={visiblePapers}
          selectedId={selectedId}
          batchSelection={validBatchSelection}
          semanticScores={semanticScores}
          pendingPaperIds={mutations.pendingPaperIds}
          onSelect={setSelectedId}
          onToggleBatch={toggleBatch}
          onToggleFavorite={(paperId, favorite) => {
            mutations.favorite.mutate({ paperId, favorite });
          }}
          onStatusChange={(paperId, status) => {
            mutations.status.mutate({ paperId, status });
          }}
          onOpen={openPaper}
        />
      )}

      {addEditorOpen ? (
        <PaperEditor
          mode="create"
          pending={mutations.add.isPending}
          error={mutations.add.error ? errorText(mutations.add.error) : ''}
          onCancel={() => {
            mutations.add.reset();
            setAddEditorOpen(false);
          }}
          onSubmit={(draft) => {
            mutations.add.mutate(draft, {
              onSuccess: () => setAddEditorOpen(false),
            });
          }}
        />
      ) : null}
    </section>
  );
}

export function LibraryInspectorSlot() {
  const navigate = useNavigate();
  const selectedId = useWorkspaceStore((state) => state.workspaceSelectionId);
  const [editingTarget, setEditingTarget] = useState<{
    readonly paperId: string;
    readonly draft: PaperDraft;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    readonly paperId: string;
    readonly paperTitle: string;
    readonly selectionId: string | null;
  } | null>(null);
  const mutations = useLibraryLedgerMutations();
  const query = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const paper = selectedId == null
    ? null
    : query.data?.find((candidate) => candidate.id === selectedId) ?? null;
  const detailQuery = useQuery({
    queryKey: paperKeys.detail(selectedId ?? ''),
    queryFn: ({ queryKey, signal }) => paperApi.getPaper(queryKey[2], signal),
    enabled: selectedId != null,
  });
  const detail = detailQuery.data?.id === paper?.id ? detailQuery.data : null;

  return (
    <>
      <PaperPreview
        paper={paper}
        detail={detail}
        mutationPending={detailQuery.isPending || mutations.update.isPending || mutations.remove.isPending}
        onOpen={(paperId) => void navigate(`/reader/${encodeURIComponent(paperId)}`)}
        onEdit={(fixedPaper) => {
          mutations.update.reset();
          setEditingTarget({
            paperId: fixedPaper.id,
            draft: draftFromPaper(fixedPaper, detail),
          });
        }}
        onDelete={(paperId) => {
          const fixedPaper = paper?.id === paperId ? paper : null;
          if (!fixedPaper) return;
          mutations.remove.reset();
          setDeleteTarget({
            paperId,
            paperTitle: fixedPaper.title,
            selectionId: selectedId,
          });
        }}
      />
      {editingTarget ? (
        <PaperEditor
          key={editingTarget.paperId}
          mode="edit"
          initialDraft={editingTarget.draft}
          pending={mutations.update.isPending}
          error={mutations.update.error ? errorText(mutations.update.error) : ''}
          onCancel={() => {
            mutations.update.reset();
            setEditingTarget(null);
          }}
          onSubmit={(draft) => {
            const paperId = editingTarget.paperId;
            mutations.update.mutate({ paperId, draft }, {
              onSuccess: () => setEditingTarget(null),
            });
          }}
        />
      ) : null}
      {deleteTarget ? (
        <PaperDeleteConfirmation
          paperTitle={deleteTarget.paperTitle}
          pending={mutations.remove.isPending}
          error={mutations.remove.error ? errorText(mutations.remove.error) : ''}
          onCancel={() => {
            mutations.remove.reset();
            setDeleteTarget(null);
          }}
          onConfirm={() => {
            const { paperId, selectionId } = deleteTarget;
            mutations.remove.mutate({ paperId, selectionId }, {
              onSuccess: () => setDeleteTarget(null),
            });
          }}
        />
      ) : null}
    </>
  );
}
