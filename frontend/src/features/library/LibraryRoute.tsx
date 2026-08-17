/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata and slots with their component. */
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { Button, Checkbox, Empty, Input, Select } from '@cloudflare/kumo';

import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { artifactKeys, paperKeys, pdfKeys, reviewKeys } from '../../lib/api/keys';
import { paperApi, type PaperDraft } from '../../lib/api/paperApi';
import type { PaperListItem, PaperRecord, StudyStatus } from '../../lib/api/types';
import { insightsGateway } from '../../lib/api/insightsGateway';
import {
  applyLibraryFilters,
  reconcileLibrarySelection,
  type LibraryFilters,
  type LibrarySort,
  type LibrarySourceFilter,
} from './filters';
import { ExplainerBatchManager } from './ExplainerBatchManager';
import { LibraryPager } from './LibraryPager';
import { PaperDeleteConfirmation, PaperEditor } from './PaperEditor';
import { PaperPreview } from './PaperPreview';
import { PaperTable } from './PaperTable';
import './library.css';

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
  return {
    ...filters,
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
      <Empty title={title} description={detail} />
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
      insightsGateway.semanticSearch(requestedQuery, 60)
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
  // 客户端分页：筛选/语义检索变化或论文增删时回到第 1 页。
  // 用渲染期派生重置（对比签名引用），避免在 effect 里 setState 引发级联渲染；
  // 删除导致总页数变少时由 currentPage 的 clamp 保底。
  const [pagerState, setPagerState] = useState(() => ({
    page: 1,
    pageSize: 30,
    filtersSignature: null as LibraryFilters | null,
    papersSignature: null as readonly PaperListItem[] | null,
  }));
  if (pagerState.filtersSignature !== filters || pagerState.papersSignature !== papers) {
    setPagerState((current) => ({
      ...current,
      page: 1,
      filtersSignature: filters,
      papersSignature: papers,
    }));
  }
  const page = pagerState.page;
  const pageSize = pagerState.pageSize;
  const setPage = (next: number) => setPagerState((current) => ({ ...current, page: next }));
  // 切换每页条数时回到第 1 页，避免页码语义漂移（如 30 条/页的第 2 页变成 50 条/页的中段）。
  const setPageSize = (next: number) => setPagerState((current) => ({ ...current, page: 1, pageSize: next }));
  const pageCount = Math.max(1, Math.ceil(visiblePapers.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const pagedPapers = useMemo(
    () => visiblePapers.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [currentPage, pageSize, visiblePapers],
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
      {/* 页标题已在顶部命令栏展示；原「文献台账」大块 intro 移除，
          「添加论文」并入筛选行，把纵向空间让给列表本身。 */}
      {/* 批量讲解保持紧凑单行卡并置顶：入口需随时可见，不能藏在列表底部。 */}
      <ExplainerBatchManager />

      <div className="library-filters" aria-label="文献筛选">
        <Input
          label="检索"
          type="search"
          className="w-full library-filters__search"
          aria-label="搜索文献"
          value={filters.query}
          placeholder="题名 / 中文题名 / venue / type / topic"
          onChange={(event) => {
            semanticGeneration.current += 1;
            setSemanticScores(null);
            setSemanticError('');
            patchFilters({ query: (event.target as HTMLInputElement).value });
          }}
        />
        <Select
          label="来源"
          aria-label="来源"
          className="w-full"
          value={filters.source}
          onValueChange={(value) => patchFilters({ source: value as LibrarySourceFilter })}
        >
          <Select.Option value="all">全部来源</Select.Option>
          <Select.Option value="seed">种子文献</Select.Option>
          <Select.Option value="collected">后续采集</Select.Option>
        </Select>
        <Select
          label="状态"
          aria-label="状态"
          className="w-full"
          value={filters.status}
          onValueChange={(value) => patchFilters({ status: value as 'all' | StudyStatus })}
        >
          <Select.Option value="all">全部状态</Select.Option>
          <Select.Option value="未开始">未开始</Select.Option>
          <Select.Option value="学习中">学习中</Select.Option>
          <Select.Option value="已理解">已理解</Select.Option>
        </Select>
        <Select
          label="年份"
          aria-label="年份"
          className="w-full"
          value={filters.year}
          onValueChange={(value) => patchFilters({ year: value ?? 'all' })}
        >
          <Select.Option value="all">全部年份</Select.Option>
          {facets.years.map((year) => <Select.Option key={year} value={year}>{year}</Select.Option>)}
        </Select>
        <Select
          label="会议"
          aria-label="会议"
          className="w-full"
          value={filters.venue}
          onValueChange={(value) => patchFilters({ venue: value ?? 'all' })}
        >
          <Select.Option value="all">全部会议</Select.Option>
          {facets.venues.map((venue) => <Select.Option key={venue} value={venue}>{venue}</Select.Option>)}
        </Select>
        <Select
          label="类型"
          aria-label="类型"
          className="w-full"
          value={filters.type}
          onValueChange={(value) => patchFilters({ type: value ?? 'all' })}
        >
          <Select.Option value="all">全部类型</Select.Option>
          {facets.types.map((type) => <Select.Option key={type} value={type}>{type}</Select.Option>)}
        </Select>
        <Select
          label="主题"
          aria-label="主题"
          className="w-full"
          value={filters.topic}
          onValueChange={(value) => patchFilters({ topic: value ?? 'all' })}
        >
          <Select.Option value="all">全部主题</Select.Option>
          {facets.topics.map((topic) => <Select.Option key={topic} value={topic}>{topic}</Select.Option>)}
        </Select>
        <Select
          label="排序"
          aria-label="排序"
          className="w-full"
          value={filters.sort}
          onValueChange={(value) => patchFilters({ sort: value as LibrarySort })}
        >
          <Select.Option value="added">最近加入</Select.Option>
          <Select.Option value="relevance">相关度</Select.Option>
          <Select.Option value="year">年份</Select.Option>
          <Select.Option value="citations">引用数</Select.Option>
          <Select.Option value="title">题名</Select.Option>
        </Select>
        <div className="library-filters__favorite">
          <Checkbox
            label="仅看收藏"
            checked={filters.favorite}
            onCheckedChange={(checked) => patchFilters({ favorite: checked })}
          />
        </div>
        <Button
          type="button"
          variant="outline"
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
        </Button>
        <Button
          type="button"
          variant="primary"
          className="library-route__add"
          onClick={() => {
            mutations.add.reset();
            setAddEditorOpen(true);
          }}
        >
          添加论文
        </Button>
      </div>

      {semanticError ? <p className="library-route__audit library-route__audit--error" role="alert">语义检索失败：{semanticError}</p> : null}

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
          <Button type="button" onClick={() => void query.refetch()}>重试</Button>
        </div>
      ) : visiblePapers.length === 0 ? (
        <EmptyLibrary filters={filters} />
      ) : (
        <>
          <PaperTable
            papers={pagedPapers}
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
          <div className="library-route__table-footer">
            <div className="library-route__batch" aria-live="polite">
              <span>已选择 {validBatchSelection.size} 篇</span>
              {validBatchSelection.size > 0 ? (
                <Button type="button" variant="ghost" onClick={() => setBatchSelection(new Set())}>清除选择</Button>
              ) : null}
            </div>
            <LibraryPager
              page={currentPage}
              pageCount={pageCount}
              pageSize={pageSize}
              total={visiblePapers.length}
              rangeStart={(currentPage - 1) * pageSize + 1}
              rangeEnd={Math.min(currentPage * pageSize, visiblePapers.length)}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
            />
          </div>
        </>
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
