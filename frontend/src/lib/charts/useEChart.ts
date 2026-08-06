import { useLayoutEffect, useRef, type RefObject } from 'react';
import { init as initEChart, type ECharts, type EChartsOption } from 'echarts';

export interface EChartRuntime {
  init(container: HTMLElement): EChartInstance;
}

export interface EChartInstance {
  setOption(option: EChartsOption, notMerge?: boolean): void;
  resize(): void;
  on(eventName: 'click', handler: (params: unknown) => void): void;
  off(eventName?: string): void;
  dispose(): void;
}

export interface UseEChartOptions {
  readonly option: EChartsOption | null;
  readonly hasData: boolean;
  readonly onClick?: (params: unknown) => void;
  readonly runtime?: EChartRuntime;
}

const browserRuntime: EChartRuntime = {
  init(container) {
    return initEChart(container) as ECharts;
  },
};

function hasArea(element: HTMLElement): boolean {
  const bounds = element.getBoundingClientRect();
  return bounds.width > 0 && bounds.height > 0;
}

export function useEChart({
  option,
  hasData,
  onClick,
  runtime = browserRuntime,
}: UseEChartOptions): RefObject<HTMLDivElement | null> {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartInstance | null>(null);
  const optionRef = useRef(option);
  const onClickRef = useRef(onClick);

  useLayoutEffect(() => {
    optionRef.current = option;
    onClickRef.current = onClick;
  }, [onClick, option]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container || !hasData) return;

    let frame: number | null = null;
    let observer: ResizeObserver | null = null;

    const ensureChart = () => {
      if (chartRef.current || !hasArea(container)) return;
      const chart = runtime.init(container);
      chart.on('click', (params) => onClickRef.current?.(params));
      chartRef.current = chart;
      if (optionRef.current) chart.setOption(optionRef.current);
    };

    const requestResize = () => {
      ensureChart();
      if (!chartRef.current || frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        chartRef.current?.resize();
      });
    };

    ensureChart();
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(requestResize);
      observer.observe(container);
    }

    return () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      observer?.disconnect();
      observer = null;
      const chart = chartRef.current;
      if (chart) {
        chart.off();
        chart.dispose();
      }
      chartRef.current = null;
      container.replaceChildren();
    };
  }, [hasData, runtime]);

  useLayoutEffect(() => {
    if (option) chartRef.current?.setOption(option, true);
  }, [option]);

  return containerRef;
}
