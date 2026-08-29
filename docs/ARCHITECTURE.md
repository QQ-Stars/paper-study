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

论文复现工作区是当前 `/api/v2/reproductions` 领域接口及其
`ReproductionWorkspace` application seam。它把一个 `Paper` 关联到独立的
`ReproductionProject`，并分别保存 `ReproductionDocument`、人工记录的
`ExperimentRun`、`ReproductionArtifact` 和可选的 `ReproductionNote`。
`ExperimentRun` 不等同于后台 `ProcessingJob`，首版不会执行浏览器或后端传入的
任意命令。删除论文不会级联删除复现资料；项目保留论文标题快照并将关联置空。

每个复现项目同时拥有以稳定 `project_id` 命名的独立目录：

```text
data/reproduction-artifacts/projects/<project-id>/
├─ project.json
├─ document.md
├─ runs.json
├─ results.json
├─ notes.json
└─ artifacts/
```

SQLite 仍是事务和查询的主数据源；目录内 Markdown/JSON 是原子更新的可读镜像，
便于按项目备份、查看和维护，不会反向导入手工修改。`project.json` 保存聚合指纹；
镜像暂时写入失败不会回滚已提交的 SQLite 事务，后续详情读取会再次尝试同步。
旧项目在首次读取详情时自动补齐目录。新附件使用不透明文件名写入 `artifacts/`，历史附件路径继续兼容。
复制项目时会复制实际附件并重写正文中的附件 URL，因此源项目与副本可独立删除。

## 目录职责

- `ui-redesign/`：唯一前端源码。`dist/` 是随仓库提交的生产构建产物。
- `backend/`：唯一 Web/API 后端、Alembic 迁移、后台处理队列和领域服务。
- `agent/`：Python 采集、PDF/OCR、LLM 适配器和只读 MCP stdio 服务；不是第二个 Web 服务。
- `backend/app/application/reproductions.py`：复现项目、文档、运行、附件和笔记的深模块实现。
- `backend/app/api/routes/reproductions.py`：仅负责 `/api/v2/reproductions` 的 HTTP 校验和适配。
- `data/reproduction-artifacts/projects/<project-id>/`：每个复现项目的独立镜像与附件目录，项目和新附件路径均使用不透明 ID。
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
