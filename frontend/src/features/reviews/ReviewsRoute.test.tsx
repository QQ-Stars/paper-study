import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resetWorkspaceStore } from '../../app/stores/workspaceStore';
import { reviewKeys } from '../../lib/api/keys';
import type { ReviewItem, ReviewSnapshot, ReviewState } from '../../lib/api/types';
import { Component } from './ReviewsRoute';

const apiMocks = vi.hoisted(() => ({
  getReviews: vi.fn(),
  completeReview: vi.fn(),
}));

vi.mock('../../lib/api/paperApi', () => ({
  paperApi: {
    getReviews: apiMocks.getReviews,
    completeReview: apiMocks.completeReview,
  },
}));

function reviewItem(
  paperId: string,
  reviewState: ReviewState,
  overrides: Partial<ReviewItem> = {},
): ReviewItem {
  return {
    paperId,
    startedAt: '2026-08-01',
    currentStep: 2,
    completedSteps: 1,
    nextDueAt: '2026-08-06',
    completedAt: null,
    updatedAt: '2026-08-06',
    title: `Paper ${paperId}`,
    titleZh: null,
    venue: 'CSCW',
    year: '2026',
    status: '已理解',
    reviewState,
    totalSteps: 7,
    ...overrides,
  };
}

function snapshot(overrides: Partial<ReviewSnapshot> = {}): ReviewSnapshot {
  const value: ReviewSnapshot = {
    today: '2026-08-06',
    counts: { overdue: 1, dueToday: 1, upcoming: 1, completed: 1 },
    overdue: [reviewItem('overdue', 'overdue', { nextDueAt: '2026-08-05' })],
    dueToday: [reviewItem('today', 'dueToday')],
    upcoming: [reviewItem('upcoming', 'upcoming', { nextDueAt: '2026-08-10' })],
    completed: [reviewItem('completed', 'completed', {
      currentStep: 7,
      completedSteps: 7,
      nextDueAt: '2026-08-31',
      completedAt: '2026-08-31',
    })],
  };
  return { ...value, ...overrides };
}

function renderReviews() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/reviews']}>
        <Component />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

beforeEach(() => {
  resetWorkspaceStore();
  apiMocks.getReviews.mockReset();
  apiMocks.completeReview.mockReset();
  apiMocks.getReviews.mockResolvedValue(snapshot());
});

describe('ReviewsRoute', () => {
  it('renders every authoritative group and only exposes completion for actionable items', async () => {
    renderReviews();

    const overdue = await screen.findByRole('region', { name: '逾期复习' });
    const today = screen.getByRole('region', { name: '今日复习' });
    const upcoming = screen.getByRole('region', { name: '后续复习' });
    const completed = screen.getByRole('region', { name: '已完成复习' });

    expect(within(overdue).getByText('Paper overdue')).toBeInTheDocument();
    expect(within(today).getByText('Paper today')).toBeInTheDocument();
    expect(within(upcoming).getByText('Paper upcoming')).toBeInTheDocument();
    expect(within(completed).getByText('Paper completed')).toBeInTheDocument();
    expect(within(overdue).getByRole('button', { name: '完成本轮' })).toBeEnabled();
    expect(within(today).getByRole('button', { name: '完成本轮' })).toBeEnabled();
    expect(within(upcoming).queryByRole('button', { name: '完成本轮' })).not.toBeInTheDocument();
    expect(within(completed).queryByRole('button', { name: '完成本轮' })).not.toBeInTheDocument();
  });

  it('atomically keeps the completion response when a stale pre-mutation load resolves later', async () => {
    const user = userEvent.setup();
    const initial = snapshot();
    const staleLoad = deferred<ReviewSnapshot>();
    const advanced = reviewItem('overdue', 'upcoming', {
      currentStep: 3,
      completedSteps: 2,
      nextDueAt: '2026-08-08',
    });
    const authoritative = snapshot({
      counts: { overdue: 0, dueToday: 1, upcoming: 2, completed: 1 },
      overdue: [],
      upcoming: [advanced, ...initial.upcoming],
    });
    apiMocks.getReviews
      .mockResolvedValueOnce(initial)
      .mockReturnValueOnce(staleLoad.promise);
    apiMocks.completeReview.mockResolvedValueOnce({
      plan: {
        paperId: advanced.paperId,
        startedAt: advanced.startedAt,
        currentStep: advanced.currentStep,
        completedSteps: advanced.completedSteps,
        nextDueAt: advanced.nextDueAt,
        completedAt: advanced.completedAt,
        updatedAt: advanced.updatedAt,
      },
      reviews: authoritative,
    });
    const { queryClient } = renderReviews();

    const overdue = await screen.findByRole('region', { name: '逾期复习' });
    await user.click(screen.getByRole('button', { name: '刷新快照' }));
    await waitFor(() => expect(apiMocks.getReviews).toHaveBeenCalledTimes(2));
    await user.click(within(overdue).getByRole('button', { name: '完成本轮' }));

    await waitFor(() => {
      expect(queryClient.getQueryData(reviewKeys.list())).toEqual(authoritative);
    });
    expect(apiMocks.completeReview).toHaveBeenCalledWith('overdue');
    expect(screen.getByRole('region', { name: '后续复习' })).toHaveTextContent('Paper overdue');

    staleLoad.resolve(initial);
    await Promise.resolve();
    await Promise.resolve();

    expect(queryClient.getQueryData(reviewKeys.list())).toEqual(authoritative);
    expect(apiMocks.getReviews).toHaveBeenCalledTimes(2);
  });

  it('serializes completion writes while keeping each request bound to its clicked paper id', async () => {
    const user = userEvent.setup();
    const firstWrite = deferred<{
      plan: ReviewItem;
      reviews: ReviewSnapshot;
    }>();
    const initial = snapshot();
    const afterFirst = snapshot({
      counts: { overdue: 0, dueToday: 1, upcoming: 2, completed: 1 },
      overdue: [],
      upcoming: [
        reviewItem('overdue', 'upcoming', {
          currentStep: 3,
          completedSteps: 2,
          nextDueAt: '2026-08-08',
        }),
        ...initial.upcoming,
      ],
    });
    const afterSecond = snapshot({
      counts: { overdue: 0, dueToday: 0, upcoming: 3, completed: 1 },
      overdue: [],
      dueToday: [],
      upcoming: [
        reviewItem('overdue', 'upcoming', { currentStep: 3, completedSteps: 2 }),
        reviewItem('today', 'upcoming', { currentStep: 3, completedSteps: 2 }),
        ...initial.upcoming,
      ],
    });
    apiMocks.completeReview
      .mockReturnValueOnce(firstWrite.promise)
      .mockResolvedValueOnce({
        plan: afterSecond.upcoming[1],
        reviews: afterSecond,
      });
    renderReviews();

    const overdue = await screen.findByRole('region', { name: '逾期复习' });
    const today = screen.getByRole('region', { name: '今日复习' });
    const overdueButton = within(overdue).getByRole('button', { name: '完成本轮' });
    await user.click(overdueButton);
    expect(overdueButton).toBeDisabled();
    await user.click(overdueButton);
    await user.click(within(today).getByRole('button', { name: '完成本轮' }));

    expect(apiMocks.completeReview).toHaveBeenCalledTimes(1);
    expect(apiMocks.completeReview).toHaveBeenNthCalledWith(1, 'overdue');

    firstWrite.resolve({
      plan: afterFirst.upcoming[0],
      reviews: afterFirst,
    });

    await waitFor(() => expect(apiMocks.completeReview).toHaveBeenCalledTimes(2));
    expect(apiMocks.completeReview).toHaveBeenNthCalledWith(2, 'today');
  });

  it('leaves the prior snapshot untouched and restores retry after completion fails', async () => {
    const user = userEvent.setup();
    const initial = snapshot();
    apiMocks.completeReview.mockRejectedValueOnce(new Error('completion write failed'));
    const { queryClient } = renderReviews();

    const overdue = await screen.findByRole('region', { name: '逾期复习' });
    await user.click(within(overdue).getByRole('button', { name: '完成本轮' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('completion write failed');
    expect(queryClient.getQueryData(reviewKeys.list())).toEqual(initial);
    expect(within(overdue).getByRole('button', { name: '完成本轮' })).toBeEnabled();
    expect(apiMocks.getReviews).toHaveBeenCalledTimes(1);
  });
});
