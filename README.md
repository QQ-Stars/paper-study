# Paper-Study

本地优先的 AI 文献研究工作台：管理论文、阅读 PDF、生成讲解与翻译、维护笔记，并提供检索、复习和 MCP 查询能力。

项目现在只有一套运行链路：`ui-redesign` React/Vite 前端 + `backend` FastAPI 后端。数据保存在本机，不需要 Docker。内置「书斋 · 昼 / 夜」两套主题，可在侧边栏「主题」切换。

## 快速启动

### Windows

安装 Python 3.10+ 后，在项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

也可以双击 `start.cmd`。脚本会自动创建 `.venv`、安装缺少的 Python 依赖，并在缺少 `ui-redesign/dist` 时构建前端。浏览器地址：<http://localhost:5173/workspace/>。

停止服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd ui-redesign && npm ci && npm run build && cd ..
.venv/bin/python -m backend.app.cli.local_runtime --root . --port 5173
```

前端已提交构建产物时，最后一条命令即可直接启动。停止前台进程使用 `Ctrl+C`。

## 配置 API Key

复制 `.env.example` 为 `.env`，或启动后在「设置」页填写供应商、Base URL、模型和 API Key。密钥只写入本机的 `data/settings.json`，该文件不会提交到 Git。

未配置大模型时，论文浏览、笔记、进度和本地 PDF 仍可使用；采集、讲解、翻译和 OCR 需要对应供应商凭证。

## 数据与目录

- `data/`：SQLite 数据库、设置、缓存和导入的 PDF。`data/app.db` 等运行时文件默认被 Git 忽略。
- `paper/`：保存的 PDF 和随库分发的论文资料。不要删除或移动其中的 PDF。
- `notes/`：论文笔记，纳入版本控制并由首次启动导入数据库。
- `ui-redesign/`：唯一前端源码和构建产物。
- `backend/`：唯一 Web/API 后端、数据库迁移、后台 worker 和采集/LLM 子系统。
- `agent/`：FastAPI 调用的 Python 采集、PDF、OCR 和 MCP 内部模块，不是第二个 Web 服务。

首次启动会把 `data/papers.json`、`data/progress.json` 和 `notes/*.md` 导入新数据库；已有数据库不会重新 seed。升级旧数据库前会在 `data/backups/` 写 SQLite 备份。

## MCP

MCP 服务是只读的 Python stdio 服务。客户端命令需要使用本项目虚拟环境中的 Python，例如：

```text
<repo>/.venv/Scripts/python.exe <repo>/agent/mcp_server.py
```

具体客户端配置可在网页「设置 → MCP」查看。服务默认只读取本地数据库，不启动额外 Web 后端。

## 开发与测试

修改前端后：

```bash
cd ui-redesign
npm ci
npm run build
npm test
```

后端测试：

```bash
.venv/Scripts/python.exe -m unittest discover -s backend/tests -p 'test_*.py' -v
```

Linux/macOS 将 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`。数据库迁移入口为 `backend/migrations/`，当前 head 为 `20260830_01`。该 head 增加研究项目公开发布元数据和文章项目类型；降级前必须确认相关表与项目类型数据符合迁移的回滚门禁。

## API 健康检查

```text
GET http://127.0.0.1:5173/health/live
GET http://127.0.0.1:5173/health/ready
GET http://127.0.0.1:5173/api/papers
```

如果使用的 Python 自带 SQLite 不支持完整 trigram tokenizer，迁移会自动使用兼容的 `trigram case_sensitive 0` 配置；不需要安装 Docker 或修改系统 SQLite。

## License

MIT
