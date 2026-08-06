import { StrictMode } from 'react';

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';

import {
  useEChart,
  type EChartInstance,
  type EChartRuntime,
} from './useEChart';

const option: EChartsOption = { series: [{ type: 'bar', data: [1] }] };

let width = 480;
let height = 240;
let resizeCallback: ResizeObserverCallback | null = null;
let nextFrame: FrameRequestCallback | null = null;
let lifecycle: string[] = [];
let liveCharts = 0;

const chart = (): EChartInstance => ({
  setOption: vi.fn(),
  resize: vi.fn(() => lifecycle.push('resize')),
  on: vi.fn(),
  off: vi.fn(() => lifecycle.push('off')),
  dispose: vi.fn(() => {
    lifecycle.push('dispose');
    liveCharts -= 1;
  }),
});

let runtime: EChartRuntime;
let boundsSpy: ReturnType<typeof vi.spyOn>;

function ChartHarness({
  hasData = true,
}: {
  readonly hasData?: boolean;
}) {
  const ref = useEChart({ option, hasData, runtime });
  return <div ref={ref} data-testid="chart" />;
}

beforeEach(() => {
  width = 480;
  height = 240;
  resizeCallback = null;
  nextFrame = null;
  lifecycle = [];
  liveCharts = 0;
  runtime = {
    init: vi.fn((container) => {
      const instance = chart();
      const canvas = document.createElement('canvas');
      container.append(canvas);
      liveCharts += 1;
      lifecycle.push('init');
      return instance;
    }),
  };
  boundsSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    .mockImplementation(() => ({
      width,
      height,
      x: 0,
      y: 0,
      top: 0,
      right: width,
      bottom: height,
      left: 0,
      toJSON: () => ({}),
    }));
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) {
      resizeCallback = callback;
    }

    observe() {
      lifecycle.push('observe');
    }

    unobserve() {}

    disconnect() {
      lifecycle.push('disconnect');
    }
  });
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    nextFrame = callback;
    lifecycle.push('request');
    return 17;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {
    nextFrame = null;
    lifecycle.push('cancel');
  });
});

afterEach(() => {
  boundsSpy.mockRestore();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useEChart lifecycle', () => {
  it('does not initialize without data or a non-zero container', () => {
    const empty = render(<ChartHarness hasData={false} />);
    expect(runtime.init).not.toHaveBeenCalled();
    empty.unmount();

    width = 0;
    height = 0;
    const zero = render(<ChartHarness />);
    expect(runtime.init).not.toHaveBeenCalled();

    width = 320;
    height = 180;
    act(() => resizeCallback?.([], {} as ResizeObserver));
    expect(runtime.init).toHaveBeenCalledOnce();
    zero.unmount();
  });

  it('keeps one live chart after the StrictMode probe and none after unmount', () => {
    const view = render(
      <StrictMode>
        <ChartHarness />
      </StrictMode>,
    );

    expect(liveCharts).toBe(1);
    expect(runtime.init).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(liveCharts).toBe(0);
  });

  it('coalesces resize frames and cleans up in resource-owner order', () => {
    const view = render(<ChartHarness />);
    const container = view.getByTestId('chart');
    const replaceChildren = container.replaceChildren.bind(container);
    vi.spyOn(container, 'replaceChildren').mockImplementation((...nodes) => {
      lifecycle.push('clear');
      replaceChildren(...nodes);
    });
    lifecycle = [];

    act(() => {
      resizeCallback?.([], {} as ResizeObserver);
      resizeCallback?.([], {} as ResizeObserver);
    });
    expect(window.requestAnimationFrame).toHaveBeenCalledOnce();

    view.unmount();

    expect(lifecycle).toEqual([
      'request',
      'cancel',
      'disconnect',
      'off',
      'dispose',
      'clear',
    ]);
    expect(container.childElementCount).toBe(0);
    expect(nextFrame).toBeNull();
  });
});
