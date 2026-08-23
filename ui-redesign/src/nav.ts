import type { ComponentType, SVGProps } from 'react';

import {
  BooksIcon,
  CompassIcon,
  CycleIcon,
  GearIcon,
  ListChecksIcon,
  PlugIcon,
  SunriseIcon,
  TrendIcon,
  WrenchIcon,
} from './components/Icons';

export type PageId =
  | 'overview'
  | 'library'
  | 'manage'
  | 'reviews'
  | 'acquire'
  | 'jobs'
  | 'insights'
  | 'mcp'
  | 'settings'
  | 'reader';

export interface NavItem {
  id: PageId;
  label: string;
  hint: string;
  Icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;
}

export const NAV_ITEMS: NavItem[] = [
  { id: 'overview', label: '今日', hint: '研究概览与待办', Icon: SunriseIcon },
  { id: 'library', label: '文献库', hint: '全部论文与深度阅读', Icon: BooksIcon },
  { id: 'manage', label: '管理', hint: '文献库增删改与批量工具', Icon: WrenchIcon },
  { id: 'reviews', label: '复习', hint: '艾宾浩斯七轮计划', Icon: CycleIcon },
  { id: 'acquire', label: '采集', hint: '多源检索与导入', Icon: CompassIcon },
  { id: 'jobs', label: '任务', hint: '后台任务与定时调度', Icon: ListChecksIcon },
  { id: 'insights', label: '洞察', hint: '引用图谱与推荐', Icon: TrendIcon },
  { id: 'mcp', label: 'MCP 工具', hint: '论文库 MCP 服务与客户端接入', Icon: PlugIcon },
  { id: 'settings', label: '设置', hint: '模型凭据与目录', Icon: GearIcon },
];
