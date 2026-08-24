# Paper-Study 架构

Paper-Study 是一个本地优先的研究工作台，运行时只有一套 Web 链路：

```text
浏览器
  │
  ▼
ui-redesign/dist  ── FastAPI (/workspace/)
                         │
                         ├─ /api/v2  新领域 API
                         ├─ /api     兼容现有前端工作流的 API
                         ├─ /pdfbytes 与本地 PDF
                         └─ SQLite + 后台 worker / scheduler
```

## 目录职责

- `ui-redesign/`：唯一前端源码。`dist/` 是随仓库提交的生产构建产物。
- `backend/`：唯一 Web/API 后端、Alembic 迁移、后台处理队列和领域服务。
- `agent/`：Python 采集、PDF/OCR、LLM 适配器和只读 MCP stdio 服务；不是第二个 Web 服务。
- `db/`：可重复执行的基础 SQLite schema。
- `data/`：本地数据库、缓存、设置和 PDF 运行数据，默认不提交。
- `notes/`、`data/papers.json`、`data/progress.json`：可迁移的用户资料种子，首次创建数据库时导入。
- `paper/`：保留的论文资料和 PDF，不由启动脚本删除。

## 启动流程

`start.ps1` 创建或复用项目 `.venv`，检查 `ui-redesign/dist`，然后运行
`backend.app.cli.local_runtime`。运行器会：

1. 创建 SQLite 数据库并导入已有论文、进度和笔记；
2. 自动执行 Alembic 迁移，升级前在 `data/backups/` 生成备份；
3. 启动 FastAPI、处理 worker 和定时 scheduler。

服务默认绑定 `127.0.0.1:5173`。停止使用 `stop.ps1`；Docker 不属于当前运行方式。

## 数据边界

所有读写接口都通过 FastAPI 的依赖容器访问数据库。论文 PDF 仅从受限的本地目录提供，
前端静态资源只来自 `ui-redesign/dist`。数据库迁移是向前兼容的，已有数据库不会重复导入
种子文件。
