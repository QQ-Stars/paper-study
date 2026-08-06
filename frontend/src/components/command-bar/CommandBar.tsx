import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useWorkspaceStore } from '../../app/stores/workspaceStore';

const triggerIds = {
  command: 'workspace-command-trigger',
  queue: 'workspace-queue-trigger',
  inspector: 'workspace-inspector-trigger',
} as const;

const workspaceCommands = [
  { label: '打开今日工作台', keywords: 'dashboard 今日 概览', to: '/dashboard' },
  { label: '打开文献库', keywords: 'library 论文 搜索', to: '/library' },
  { label: '查看复习队列', keywords: 'reviews 计划', to: '/reviews' },
  { label: '采集新论文', keywords: 'acquire 搜索 导入', to: '/acquire' },
  { label: '查看后台任务', keywords: 'jobs schedules', to: '/jobs' },
  { label: '查看研究洞察', keywords: 'insights 图表 引用', to: '/insights' },
  { label: '打开设置', keywords: 'settings 模型 目录', to: '/settings' },
] as const;

export function CommandBar() {
  const activePanel = useWorkspaceStore((state) => state.panel.active);
  const openPanel = useWorkspaceStore((state) => state.openPanel);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        openPanel('command', triggerIds.command);
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [openPanel]);

  return (
    <div className="workspace-command-bar" aria-label="工作区命令">
      <button
        id={triggerIds.command}
        className="workspace-command-bar__command"
        type="button"
        aria-expanded={activePanel === 'command'}
        aria-controls="workspace-responsive-panel"
        onClick={() => openPanel('command', triggerIds.command)}
      >
        <span>搜索或运行命令</span>
        <kbd>Ctrl K</kbd>
      </button>

      <div className="workspace-command-bar__panels">
        <button
          id={triggerIds.queue}
          type="button"
          aria-expanded={activePanel === 'queue'}
          aria-controls="workspace-responsive-panel"
          onClick={() => openPanel('queue', triggerIds.queue)}
        >
          研究队列
        </button>
        <button
          id={triggerIds.inspector}
          type="button"
          aria-expanded={activePanel === 'inspector'}
          aria-controls="workspace-responsive-panel"
          onClick={() => openPanel('inspector', triggerIds.inspector)}
        >
          论文上下文
        </button>
      </div>
    </div>
  );
}

export function CommandPanel() {
  const [query, setQuery] = useState('');
  const navigate = useNavigate();
  const dismissPanel = useWorkspaceStore((state) => state.dismissPanel);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const commands = useMemo(
    () =>
      workspaceCommands.filter((command) =>
        `${command.label} ${command.keywords}`
          .toLocaleLowerCase()
          .includes(normalizedQuery),
      ),
    [normalizedQuery],
  );

  return (
    <div className="workspace-command-panel">
      <label>
        <span className="visually-hidden">筛选工作区命令</span>
        <input
          type="search"
          data-panel-autofocus="true"
          value={query}
          placeholder="输入页面或研究动作"
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
      </label>

      <ul className="workspace-command-panel__results">
        {commands.map((command) => (
          <li key={command.to}>
            <button
              type="button"
              onClick={() => {
                dismissPanel();
                void navigate(command.to);
              }}
            >
              {command.label}
            </button>
          </li>
        ))}
      </ul>

      {commands.length === 0 ? (
        <p className="workspace-command-panel__empty">没有匹配的工作区命令。</p>
      ) : null}
    </div>
  );
}
