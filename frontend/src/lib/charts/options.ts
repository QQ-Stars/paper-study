import type { EChartsOption } from 'echarts';

import type {
  CitationGraph,
  PaperListItem,
} from '../api/types';

type InsightPaper = Pick<
  PaperListItem,
  'id' | 'title' | 'year' | 'type' | 'topic' | 'venue' | 'citations'
>;

const colors = {
  accent: '#2fe586',
  accentSoft: '#59eda0',
  text: '#f2f6f3',
  muted: '#9ca8a1',
  weak: '#667069',
  line: 'rgba(255,255,255,0.09)',
  surface: '#141916',
} as const;

const tooltip = {
  renderMode: 'richText' as const,
  backgroundColor: colors.surface,
  borderColor: colors.line,
  textStyle: { color: colors.text, fontSize: 12 },
  formatter: formatChartTooltip,
};

function label(value: string | null | undefined): string {
  const normalized = value?.trim();
  return normalized || '未分类';
}

function countBy(values: readonly string[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return [...counts.entries()];
}

function tooltipValue(params: Record<string, unknown>): unknown {
  const data = params.data;
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>;
    return record.value ?? record.name;
  }
  return params.value;
}

export function safeChartText(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '＆')
    .replaceAll('<', '‹')
    .replaceAll('>', '›')
    .replaceAll('"', '＂')
    .replaceAll("'", '＇');
}

export function formatChartTooltip(params: unknown): string {
  if (Array.isArray(params)) {
    return params.map(formatChartTooltip).filter(Boolean).join('\n');
  }
  if (!params || typeof params !== 'object') return safeChartText(params);
  const record = params as Record<string, unknown>;
  const name = safeChartText(record.name ?? record.seriesName ?? '');
  const value = safeChartText(tooltipValue(record));
  return value && value !== name ? `${name}\n${value}`.trim() : name;
}

export function buildYearTrendOption(
  papers: readonly InsightPaper[],
): EChartsOption | null {
  const rows = countBy(
    papers
      .map((paper) => paper.year?.trim() ?? '')
      .filter((year) => /^\d{4}$/.test(year)),
  ).sort(([left], [right]) => left.localeCompare(right));
  if (rows.length === 0) return null;

  return {
    animationDuration: 320,
    color: [colors.accent],
    grid: { top: 18, right: 12, bottom: 28, left: 34 },
    tooltip: { ...tooltip, trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: rows.map(([year]) => year),
      axisLine: { lineStyle: { color: colors.line } },
      axisLabel: { color: colors.weak },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: colors.line } },
      axisLabel: { color: colors.weak },
    },
    series: [{
      name: '入库论文',
      type: 'line',
      smooth: 0.28,
      symbol: 'circle',
      symbolSize: 7,
      areaStyle: { color: 'rgba(210,96,96,0.13)' },
      lineStyle: { width: 2 },
      data: rows.map(([, count]) => count),
    }],
  };
}

export function buildTopicTreemapOption(
  papers: readonly InsightPaper[],
): EChartsOption | null {
  if (papers.length === 0) return null;
  const groups = new Map<string, Map<string, number>>();
  for (const paper of papers) {
    const type = label(paper.type);
    const topic = label(paper.topic);
    const topics = groups.get(type) ?? new Map<string, number>();
    topics.set(topic, (topics.get(topic) ?? 0) + 1);
    groups.set(type, topics);
  }
  const data = [...groups.entries()]
    .map(([name, topics]) => ({
      name,
      value: [...topics.values()].reduce((sum, value) => sum + value, 0),
      children: [...topics.entries()]
        .map(([topic, value]) => ({ name: topic, value }))
        .sort((left, right) => right.value - left.value || left.name.localeCompare(right.name)),
    }))
    .sort((left, right) => right.value - left.value || left.name.localeCompare(right.name));

  return {
    animationDuration: 320,
    color: [colors.accent, colors.accentSoft, '#278d5c', '#37664d'],
    tooltip,
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: { color: colors.text },
      upperLabel: { show: true, color: colors.text, height: 24 },
      itemStyle: { borderColor: colors.surface, borderWidth: 2, gapWidth: 2 },
      data,
    }],
  };
}

export function buildVenueCompositionOption(
  papers: readonly InsightPaper[],
): EChartsOption | null {
  const rows = countBy(
    papers
      .map((paper) => paper.venue?.trim() ?? '')
      .filter(Boolean),
  ).sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  if (rows.length === 0) return null;

  return {
    animationDuration: 320,
    color: [colors.accent, colors.accentSoft, '#278d5c', '#37664d', '#344b3e'],
    tooltip,
    series: [{
      name: '发表场所',
      type: 'pie',
      radius: ['48%', '76%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: colors.surface, borderWidth: 2 },
      label: { color: colors.muted, formatter: '{b}  {c}' },
      data: rows.map(([name, value]) => ({ name, value })),
    }],
  };
}

export function buildTopCitationsOption(
  papers: readonly InsightPaper[],
  limit = 10,
): EChartsOption | null {
  const rows = papers
    .filter((paper) => typeof paper.citations === 'number' && paper.citations >= 0)
    .sort((left, right) => (
      (right.citations ?? 0) - (left.citations ?? 0)
      || left.title.localeCompare(right.title)
    ))
    .slice(0, Math.max(1, limit));
  if (rows.length === 0) return null;

  return {
    animationDuration: 320,
    color: [colors.accent],
    grid: { top: 8, right: 18, bottom: 24, left: 120 },
    tooltip,
    xAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: colors.line } },
      axisLabel: { color: colors.weak },
    },
    yAxis: {
      type: 'category',
      data: rows.map((paper) => paper.title),
      inverse: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: colors.muted, width: 104, overflow: 'truncate' },
    },
    series: [{
      name: '引用',
      type: 'bar',
      barMaxWidth: 12,
      data: rows.map((paper) => ({
        id: paper.id,
        name: paper.title,
        value: paper.citations ?? 0,
      })),
    }],
  };
}

export function buildCitationGraphOption(
  graph: CitationGraph | undefined,
): EChartsOption | null {
  if (!graph || graph.nodes.length === 0) return null;
  const citations = graph.nodes.map((node) => node.citations ?? 0);
  const maximum = Math.max(1, ...citations);

  return {
    animationDuration: 420,
    color: [colors.accent, colors.accentSoft],
    tooltip,
    series: [{
      name: '引用网络',
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: false,
      force: { repulsion: 150, edgeLength: [55, 130], gravity: 0.08 },
      label: { show: false, color: colors.text },
      emphasis: { focus: 'adjacency', label: { show: true } },
      lineStyle: { color: colors.weak, opacity: 0.44, curveness: 0.08 },
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: [0, 6],
      data: graph.nodes.map((node) => ({
        id: node.id,
        name: node.title,
        value: node.citations ?? 0,
        symbolSize: 8 + 18 * Math.sqrt((node.citations ?? 0) / maximum),
        category: node.type ?? '未分类',
      })),
      links: graph.links.map((link) => ({
        source: link.source,
        target: link.target,
      })),
    }],
  };
}
