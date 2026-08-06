import { describe, expect, it } from 'vitest';
import type { EChartsOption } from 'echarts';

import type { CitationGraph, PaperListItem } from '../api/types';
import {
  buildCitationGraphOption,
  buildTopicTreemapOption,
  buildYearTrendOption,
  formatChartTooltip,
} from './options';

type AxisOption = {
  xAxis: { data: string[] };
  series: Array<{ data: unknown[]; links?: Array<{ source: string; target: string }> }>;
};

function paper(
  id: string,
  patch: Partial<PaperListItem> = {},
): PaperListItem {
  return {
    id,
    file: `${id}.pdf`,
    title: `Paper ${id}`,
    titleZh: null,
    venue: null,
    year: null,
    type: null,
    topic: null,
    pdfUrl: null,
    pdfPath: null,
    url: null,
    tldr: null,
    contribution: null,
    citations: null,
    createdAt: null,
    source: null,
    arxivId: null,
    doi: null,
    s2Id: null,
    openalexId: null,
    relevance: null,
    order: null,
    ccf: null,
    status: '未开始',
    hasNote: false,
    favorite: false,
    hasPdf: false,
    ...patch,
  };
}

function asAxis(option: EChartsOption | null): AxisOption {
  return option as unknown as AxisOption;
}

describe('insight chart options', () => {
  it('derives a sorted year trend and returns null without year evidence', () => {
    const option = asAxis(buildYearTrendOption([
      paper('1', { year: '2025' }),
      paper('2', { year: '2024' }),
      paper('3', { year: '2025' }),
      paper('4', { year: 'unknown' }),
    ]));

    expect(option.xAxis.data).toEqual(['2024', '2025']);
    expect(option.series[0].data).toEqual([1, 2]);
    expect(buildYearTrendOption([paper('x')])).toBeNull();
  });

  it('groups the treemap by type and topic using only paper facts', () => {
    const option = buildTopicTreemapOption([
      paper('1', { type: '系统', topic: '检索' }),
      paper('2', { type: '系统', topic: '检索' }),
      paper('3', { type: '研究', topic: '可视化' }),
    ]) as unknown as { series: Array<{ data: unknown[] }> };

    expect(option.series[0].data).toEqual([
      {
        name: '系统',
        value: 2,
        children: [{ name: '检索', value: 2 }],
      },
      {
        name: '研究',
        value: 1,
        children: [{ name: '可视化', value: 1 }],
      },
    ]);
  });

  it('preserves the server citation direction source cites target', () => {
    const graph: CitationGraph = {
      nodes: [
        { id: 'a', title: 'A', venue: null, year: null, type: null, topic: null, citations: 2, indeg: 0, outdeg: 1 },
        { id: 'b', title: 'B', venue: null, year: null, type: null, topic: null, citations: 5, indeg: 1, outdeg: 0 },
      ],
      links: [{ source: 'a', target: 'b' }],
      edgeCount: 1,
    };
    const option = buildCitationGraphOption(graph) as unknown as AxisOption;

    expect(option.series[0].links).toEqual([{ source: 'a', target: 'b' }]);
  });

  it('formats tooltip text without executable HTML delimiters', () => {
    const text = formatChartTooltip({
      name: '<img src=x onerror=alert(1)>',
      value: 'A&B',
    });

    expect(text).not.toContain('<');
    expect(text).not.toContain('>');
    expect(text).toContain('‹img');
    expect(text).toContain('A＆B');
  });
});
