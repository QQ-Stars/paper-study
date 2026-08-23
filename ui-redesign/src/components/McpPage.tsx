import { useEffect, useState } from 'react';

import { PlugIcon } from './Icons';

/* MCP 工具页：服务状态 → 9 个只读工具 → 客户端接入指引（纸墨风） */

const MCP_TOOLS: Array<{ name: string; desc: string }> = [
  { name: 'search_papers', desc: '关键词 + 属性过滤检索（题名/方向/会议/年份/相关度，可排序）' },
  { name: 'semantic_search', desc: '自然语言语义检索，中文描述可直接匹配英文论文' },
  { name: 'related_papers', desc: '库内与某篇论文语义相近的论文列表' },
  { name: 'get_paper', desc: '单篇全部属性：题录 + AI 分类 + 笔记/进度/收藏/PDF 状态' },
  { name: 'get_explainer', desc: '分页获取论文讲解正文（含偏移与总长）' },
  { name: 'get_translation', desc: '分页获取全文中文翻译正文' },
  { name: 'list_due_reviews', desc: '按艾宾浩斯计划列出今日应复习与逾期论文' },
  { name: 'list_categories', desc: '库内在用的方向/子主题/任务词表及计数' },
  { name: 'library_overview', desc: '全库画像（方向/会议/年份分布），用于开题与空白分析' },
];

const CLAUDE_CODE_CMD =
  'claude mcp add paper-study -- <项目路径>/.venv/Scripts/python.exe <项目路径>/agent/mcp_server.py';

const DESKTOP_JSON = `{
  "mcpServers": {
    "paper-study": {
      "command": "<项目路径>/.venv/Scripts/python.exe",
      "args": ["<项目路径>/agent/mcp_server.py"]
    }
  }
}`;

const CODEX_TOML = `[mcp_servers.paper_study]
command = '<项目路径>\\.venv\\Scripts\\python.exe'
args = ['<项目路径>\\agent\\mcp_server.py']
startup_timeout_sec = 180`;

export function McpPage() {
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking');

  useEffect(() => {
    let cancelled = false;
    fetch('/health/live')
      .then((response) => {
        if (!cancelled) setHealth(response.ok ? 'online' : 'offline');
      })
      .catch(() => {
        if (!cancelled) setHealth('offline');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="page mcp">
      <div className="mcp__grid">
        <section className="card insights__panel" aria-labelledby="mcp-status">
          <header className="insights__panel-head">
            <h3 className="section-title" id="mcp-status">
              MCP 服务状态
            </h3>
            <span className="eyebrow">stdio · 只读</span>
          </header>
          <div className="mcp__status">
            <span
              className={`mcp__dot${health === 'online' ? ' mcp__dot--on' : health === 'offline' ? ' mcp__dot--off' : ''}`}
              aria-hidden="true"
            />
            <div>
              <strong>
                {health === 'checking'
                  ? '检测中…'
                  : health === 'online'
                    ? '后端在线 · MCP 可用'
                    : '后端离线'}
              </strong>
              <p>
                MCP 服务（agent/mcp_server.py）采用 stdio 传输，由 Claude Code / Claude Desktop /
                Codex 等客户端按需拉起，无需常驻进程；数据只读本地 SQLite，不上传。
              </p>
            </div>
          </div>
          <p className="deep__fact">
            9 个只读工具 · Python ≥3.10 · 依赖项目 .venv（mcp 包已随 requirements.txt 安装）
          </p>
        </section>

        <section className="card insights__panel" aria-labelledby="mcp-setup">
          <header className="insights__panel-head">
            <h3 className="section-title" id="mcp-setup">
              客户端接入指引
            </h3>
            <span className="eyebrow">三选一</span>
          </header>
          <h4 className="mcp__sub">Claude Code</h4>
          <pre className="mcp__code">
            <code>{CLAUDE_CODE_CMD}</code>
          </pre>
          <h4 className="mcp__sub">Claude Desktop（claude_desktop_config.json）</h4>
          <pre className="mcp__code">
            <code>{DESKTOP_JSON}</code>
          </pre>
          <h4 className="mcp__sub">Codex（config.toml）</h4>
          <pre className="mcp__code">
            <code>{CODEX_TOML}</code>
          </pre>
          <p className="deep__fact">
            将「&lt;项目路径&gt;」替换为本机 paper-study 文件夹完整路径；macOS / Linux 把
            .venv\Scripts\python.exe 换成 .venv/bin/python。修改后需新开一个会话生效。
          </p>
        </section>
      </div>

      <section className="card insights__panel mcp__tools-panel" aria-labelledby="mcp-tools">
        <header className="insights__panel-head">
          <h3 className="section-title" id="mcp-tools">
            工具列表（9）
          </h3>
          <span className="eyebrow">
            <PlugIcon size={14} />
          </span>
        </header>
        <ul className="mcp__tools">
          {MCP_TOOLS.map((tool) => (
            <li key={tool.name} className="mcp__tool">
              <code>{tool.name}</code>
              <p>{tool.desc}</p>
            </li>
          ))}
        </ul>
        <p className="deep__fact">
          示例：指示 Claude「用 library_overview 查看库中论文最少的方向」，或「用 search_papers
          找出 CVPR 2026 的物体幻觉缓解工作，逐篇 get_explainer 后归纳共性思路」。
        </p>
      </section>
    </div>
  );
}
