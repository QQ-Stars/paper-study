# Paper-Study · 文献管理 · 学术搜索 · 论文精读

> 一个**在本地运行**的网页应用：自动检索论文 → AI 自动分类入库 → 一键生成中文**讲解 / 翻译** → **语义检索**辅助发现研究空白。
> 论文、笔记、API 密钥**仅保存在本地**，不上传任何服务器。

**适用于任意研究方向**：在 ⚙ 设置中填写研究主题，大模型采集时即按该主题对论文分类、评定相关度；更换主题即可用于其他领域。

当前 `main` 分支即 v1.0 稳定版，包含学术清爽界面、论文复习队列、PDF 归档命名、翻译浮窗优化与 Codex/Claude MCP 支持。

---

## 目录

1. [功能概览](#功能概览)
2. [环境准备](#一环境准备)
3. [安装与启动](#二安装与启动)
4. [配置大模型 API Key](#三配置大模型-api-key)
5. [日常使用](#四日常使用)
6. [使用 Docker 部署（可选）](#五使用-docker-部署可选)
7. [配置外部嵌入 API（可选）](#六配置外部嵌入-api可选)
8. [MCP 服务（可选）](#七mcp-服务可选)
9. [常见问题](#八常见问题)
10. [开发者说明](#九开发者说明)

---

## 功能概览

- 🔎 **自动采集**：输入研究方向（中/英皆可）→ 大模型扩展检索词 → 多源（arXiv / Semantic Scholar / OpenAlex / DBLP）检索去重 → 预览候选 → 勾选入库。
- 📖 **沉浸精读**：左侧论文列表 · 中间内嵌 PDF · 右侧「论文讲解 / 译文 / 我的笔记 / 相似论文」四栏。
- 🧠 **大模型辅助**：一键生成**论文讲解**与**全文中文翻译**（读取 PDF 全文、跳过参考文献、公式由 KaTeX 渲染）。
- 🔮 **语义检索**：按语义查找论文，**中文描述可直接匹配英文论文**；默认本地嵌入（无需 GPU），也可切换至更高精度的外部 API（如硅基流动 `BAAI/bge-m3`）。
- 🔗 **相似论文**：阅读时一键查找内容相近的论文，标注是否在库，可直接收录。
- 📂 **本地 PDF 导入**：批量扫描指定文件夹中的 PDF，抽取标题/摘要并自动分类入库，PDF 会归档到项目 `data/pdfs/` 并按论文标题命名。
- 🗓️ **艾宾浩斯复习**：论文标记为「已理解」后自动进入复习计划，在「复习」页查看今日到期、逾期与未来计划。
- 📊 **洞察看板**：研究趋势（年份 × 方向）与引用关系图（库内互引关系）。
- 🏅 **CCF 分级**：为每篇论文标注 CCF 推荐目录级别 A/B/C；采集时可勾选「只采 CCF-A」仅收录顶会/顶刊。
- ✅ **会议核实**：查询权威库还原论文真实发表会议，不依赖大模型臆测。
- 🤖 **MCP 服务**：将论文库以工具形式暴露给 Codex / Claude 等客户端，支持对话式检索、讲解阅读、复习队列与研究空白分析。
- ★ 收藏 · 学习进度 · 笔记 · 手动添加 · 深色模式 · 三栏宽度可拖拽调节。

---

## 一、环境准备

> **不熟悉命令行环境的用户**：可直接采用 [Docker 部署](#五使用-docker-部署可选)，仅需安装 Docker Desktop，一条命令即可启动。以下为「手动安装」方式，步骤更透明、便于排查。

运行本工具前，需先安装以下 3 个免费软件。安装时保持默认选项即可，注意下方标 ★ 的事项。

| 软件 | 用途 | 下载地址 | 验证方式 |
|---|---|---|---|
| **Node.js**（建议 LTS 20 或更高） | 运行网页服务器 | <https://nodejs.org> | 终端执行 `node -v`，显示版本号（如 v20.x） |
| **Python 3.10 或更高** | 运行 AI 采集 / 讲解 / 翻译 | <https://www.python.org/downloads/> | 终端执行 `python --version`，显示版本号 |
| **Git**（可选） | 获取本项目源码 | <https://git-scm.com> | 终端执行 `git --version`，显示版本号 |

> ★ **Windows 安装 Python 时，请务必勾选安装页底部的「Add Python to PATH」**，否则后续 `python` 命令将无法识别。
> ★ 未安装 Git 时，可在本项目 GitHub 页面点击 **Code → Download ZIP**，解压后使用。

**打开终端的方式：**

- Windows：在开始菜单搜索 **PowerShell**（或「终端 / Terminal」）并打开。
- macOS：通过聚焦搜索（⌘ + 空格）输入 **终端 / Terminal**。

---

## 二、安装与启动

在终端中**逐条**执行以下命令（每条执行完成后再执行下一条）：

### 1) 获取项目源码

使用 Git：

```bash
git clone https://github.com/QQ-Stars/paper-study.git
cd paper-study
```

> 若下载的是 ZIP 包：解压后在该文件夹中打开终端即可（文件夹内应包含 `server.js`、`package.json`）。

### 2) 安装网页端依赖

```bash
npm install
```

### 3) 安装 AI 端（Python）依赖

依赖将安装至项目自带的 `.venv` 目录，**不影响系统 Python 环境**。请按所用系统二选一：

**Windows（PowerShell）：**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**macOS / Linux：**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> 此步骤会联网下载若干 Python 包，可能耗时数分钟；**末尾若无 ERROR 即为成功**（warning 可忽略）。

### 4) 启动

```bash
node server.js
```

终端显示 **`论文学习 App 已启动`** 后，在浏览器中打开 👉 **<http://localhost:5173>**

- **停止服务**：在终端按 `Ctrl + C`。
- **后续启动**：进入项目目录执行 `node server.js` 即可，无需重新安装依赖。

---

## 三、配置大模型 API Key

「自动分类、讲解生成、翻译」均需调用大模型，因此需提供一个 **API Key**（调用大模型的凭证）。**未配置 Key 时，采集与讲解功能将无法使用。**

**推荐方式（在网页中配置）：**

1. 注册大模型服务并获取 Key。新手推荐 **DeepSeek**（价格低、兼容性好）：
   打开 <https://platform.deepseek.com> → 注册 → 创建 API Key（形如 `sk-` 开头的字符串，请妥善复制）。
2. 返回本工具网页，点击右上角 **⚙ 设置** → 供应商选择 **DeepSeek** → 将 Key 粘贴至「API Key」框 → **保存** → 点击「测试连接」确认正常。

> 同时支持**通义千问 / OpenAI / Anthropic**，也可自填 Base URL 与模型名（兼容任意 OpenAI 协议接口）。
> 🔒 Key 仅写入本地 `data/settings.json`，**不会上传**，亦不会进入 Git。

---

## 四、日常使用

中文题名补全：在「管理」页先于「设置」中配置大模型，然后运行「生成中文题名」；可通过论文的编辑操作手动修改单篇中文题名。

| 功能 | 操作 |
|---|---|
| **采集论文** | 顶栏「采集 / 管理」→ 输入研究方向（如「多模态大模型 物体幻觉 缓解」）→ 检索 →（可调整大模型扩展出的检索词）→ 勾选 → 入库 |
| **阅读与讲解** | 「阅读」页：左侧选择论文、中间查看 PDF、右栏「论文讲解」点击「✨ 生成讲解」生成中文精读（勾选「读 PDF 全文」更准、耗时更长） |
| **全文翻译** | 阅读页「译文」→「🌐 翻译全文」（自动跳过参考文献，约 1~3 分钟） |
| **语义检索** | 顶栏开启「🔮 语义」→ 输入一句话描述（如「用对比解码缓解幻觉」）→ 按相关度排序全库 |
| **相似论文** | 阅读页「相似论文」标签 →「🔗 找相似论文」，可「+ 收录」一键入库 |
| **导入本地 PDF** | 管理页「📂 本地 PDF 批量导入」→ 填写文件夹路径 → 扫描 → 勾选 → 导入，PDF 会复制/移动到项目 `data/pdfs/` |
| **复习论文** | 阅读页将论文标记为「已理解」后进入复习计划；「复习」页查看今日到期、逾期、未来计划并完成本轮 |
| **趋势与空白分析** | 「洞察」页查看趋势与引用图；或配置 [MCP](#七mcp-服务可选) 由 Codex / Claude 协助分析 |
| **收藏 / 进度 / 笔记** | 在阅读页右栏标记；顶栏「☆ 收藏」可筛选仅看收藏 |

> 讲解、翻译、收藏等结果均**缓存至数据库**，再次打开同一篇时直接载入，无需重复生成。

---

## 五、使用 Docker 部署（可选）

适用于**不希望手动安装 Node/Python** 的用户。仅需先安装 **Docker Desktop**（<https://www.docker.com/products/docker-desktop>）并启动，然后在项目目录中执行：

```bash
docker compose up -d --build      # 首次会构建镜像，耗时数分钟 → http://localhost:5173
docker compose logs -f            # 查看运行日志（Ctrl+C 仅退出日志查看，不影响服务）
docker compose down               # 停止
```

- **数据持久化**：论文库、PDF、讲解、译文、复习计划与设置均存于项目的 `data/` 目录（挂载进容器），重启容器后保留；本地嵌入模型缓存存于 `.models/`。
- **配置 Key**：启动后按 [第三步](#三配置大模型-api-key)在网页 ⚙ 设置中填写即可（写入 `data/settings.json`）。
- **修改端口**：编辑 `docker-compose.yml` 中 `ports: "5173:5173"` 左侧的数字。
- **健康检查**：新版 `docker-compose.yml` 内置健康检查，可用 `docker compose ps` 查看 `paper-study` 是否为 `healthy`。
- **Docker 内运行 MCP**：如需让 MCP 客户端连接容器内论文库，可使用容器命令作为 stdio 启动入口：

  ```bash
  docker exec -i paper-study .venv/bin/python /app/agent/mcp_server.py
  ```

  本机直接运行 MCP 时仍推荐使用 [MCP 服务](#七mcp-服务可选) 中的本地 `.venv` 配置。
- **国内无法拉取基础镜像**（构建报 `node:20-bookworm-slim … 403/timeout`）时，改用国内镜像源构建：

  **macOS / Linux：**
  ```bash
  NODE_IMAGE=docker.m.daocloud.io/library/node:20-bookworm-slim docker compose up -d --build
  ```
  **Windows PowerShell：**
  ```powershell
  $env:NODE_IMAGE="docker.m.daocloud.io/library/node:20-bookworm-slim"; docker compose up -d --build
  ```

> 多用户登录、HTTPS、对象存储托管 PDF 等「对外公开」能力参见 [docs/ROADMAP.md](docs/ROADMAP.md) 的 P6——单机自用无需配置。

---

## 六、配置外部嵌入 API（可选）

语义检索默认使用**本地嵌入模型**（首次运行会联网下载一次，存入项目内 `.models/`，此后离线可用，无需 GPU）。如需**更高检索精度**，可改用外部嵌入 API（兼容任意 OpenAI 协议的 `/embeddings` 接口，如硅基流动 `BAAI/bge-m3`，多语种、8K 上下文）：

1. 网页 **⚙ 设置 → 「语义检索嵌入」** → 来源选择「外部 API」。
2. 填写 **Base URL**（如 `https://api.siliconflow.cn/v1`）、**模型**（如 `BAAI/bge-m3`）、**API Key** → 保存。
3. 切换后，下次语义检索将**自动以新模型重嵌全库**，无需手动操作。

---

## 七、MCP 服务（可选）

将论文库以 [MCP](https://modelcontextprotocol.io) 工具形式暴露给 **Codex / Claude Code / Claude Desktop**，即可在对话中直接检索文献、阅读讲解与属性、查看复习队列、进行综述与研究空白分析。该服务只读数据库，采用 stdio 传输。

> 下列命令中的 **`<项目路径>`** 需替换为**本机 `paper-study` 文件夹的完整路径**。
> 查询路径：在该文件夹的终端中执行 `pwd`（macOS/Linux）或 `(Get-Location).Path`（Windows PowerShell）。
> Python 解释器位置：Windows 为 `<项目路径>\.venv\Scripts\python.exe`，macOS/Linux 为 `<项目路径>/.venv/bin/python`。

**注册到 Claude Code**（Windows 示例，路径使用正斜杠 `/`）：

```bash
claude mcp add paper-study -- <项目路径>/.venv/Scripts/python.exe <项目路径>/agent/mcp_server.py
```

**或写入 Claude Desktop 的 `claude_desktop_config.json`：**

```json
{
  "mcpServers": {
    "paper-study": {
      "command": "<项目路径>/.venv/Scripts/python.exe",
      "args": ["<项目路径>/agent/mcp_server.py"]
    }
  }
}
```

**或写入 Codex 的 `config.toml`：**

```toml
[mcp_servers.paper_study]
command = '<项目路径>\.venv\Scripts\python.exe'
args = ['<项目路径>\agent\mcp_server.py']
startup_timeout_sec = 180

[mcp_servers.paper_study.env]
PYTHONUTF8 = "1"
PYTHONIOENCODING = "utf-8"
DB_PATH = '<项目路径>\data\app.db'
```

> Windows 路径建议在 TOML 中使用单引号，避免反斜杠被当成转义字符。macOS/Linux 将 `.venv/Scripts/python.exe` 替换为 `.venv/bin/python`。修改后需**新开一个 Claude / Codex 会话**方可生效。

**提供的工具（9 个）：**

| 工具 | 作用 |
|---|---|
| `search_papers` | 关键词 + 属性过滤（方向 / 会议 / 年份 / 相关度 / 有无讲解 / 收藏，可排序） |
| `semantic_search` | 自然语言语义检索（中文描述可直接匹配英文论文） |
| `related_papers` | 库内与某篇语义相近的论文 |
| `get_paper` | 单篇的**全部属性**（题录 + AI 分类 + 笔记 / 进度 / 收藏 + 有无讲解 / 翻译 / PDF） |
| `get_explainer` / `get_translation` | 分页获取**讲解** / 中文翻译正文，返回 `content`、`next_offset`、`total_chars` |
| `list_categories` | 库中在用的方向 / 子主题 / 任务词表及计数 |
| `list_due_reviews` | 按艾宾浩斯复习计划列出今天应复习和逾期论文 |
| `library_overview` | 全库画像（方向 / 会议 / 年份分布），用于开题与空白分析 |

示例：可指示 Codex / Claude「用 `library_overview` 查看库中论文最少的方向」，或「用 `search_papers` 找出 CVPR 2026 的物体幻觉缓解工作，逐篇 `get_explainer` 后归纳共性思路与尚未覆盖的方向」。需要继续读取长文时，使用上一次返回的 `next_offset` 再次调用 `get_explainer` 或 `get_translation`。

---

## 八、常见问题

- **端口 5173 被占用 / 无法打开**：更换端口启动 ——
  Windows PowerShell：`$env:PORT=5174; node server.js`；macOS/Linux：`PORT=5174 node server.js`，随后访问 <http://localhost:5174>。
- **采集 / 讲解报错或无响应**：通常是**未配置 API Key 或 Key 有误**，请在 ⚙ 设置中点击「测试连接」排查。
- **提示 `python` 命令不存在**：安装 Python 时未勾选「Add Python to PATH」。请重装并勾选，或改用 `py`（Windows）/ `python3`（macOS/Linux）。
- **`npm install` 时 better-sqlite3 编译失败**：缺少编译工具链。建议改用 [Docker 部署](#五使用-docker-部署可选)；或在 Windows 安装「Visual Studio Build Tools（含 C++）」后重试。
- **首次语义检索较慢**：需联网下载一次本地嵌入模型（存入项目内 `.models/`），之后即恢复正常；亦可改用 [外部嵌入 API](#六配置外部嵌入-api可选) 免去下载。
- **DBLP 源返回结果较少**：属正常现象。DBLP 限流较严，本工具有意仅用其查询前几个检索词以获取准确的会议名称，论文召回主要由 arXiv / Semantic Scholar / OpenAlex 承担。
- **Semantic Scholar 偶发限流**：可在 ⚙ 设置中填写免费的 S2 API Key 以提升稳定性（不填亦可使用）。

---

## 九、开发者说明

### 技术栈与目录结构

```
paper-study/            # 克隆后的项目根目录
├─ server.js            # Node Web/API：静态资源 + PDF 流 + 各 REST/NDJSON 接口
├─ db.js                # SQLite 访问层（better-sqlite3，同步）
├─ db/schema.sql        # 表结构（papers / progress / notes / favorites / translations …）
├─ data/                # 运行期数据（全部 gitignore，不入库）
│  ├─ app.db            #   SQLite 数据库（WAL）
│  ├─ pdfs/             #   采集下载的 PDF
│  └─ settings.json     #   模型/数据源/目录设置（含各类 Key，脱敏显示）
├─ agent/               # Python 采集 + 大模型 Agent（python -m agent <cmd>）
│  ├─ pipeline.py       #   两阶段检索：search（出候选）/ ingest-selected（入库）
│  ├─ sources/          #   数据源适配：arxiv / semanticscholar / openalex / dblp
│  ├─ llm.py            #   分类 / 扩词 / 讲解 / 翻译 的大模型调用
│  ├─ extract.py        #   PDF→Markdown（pymupdf4llm，保留版面、裁参考文献）
│  ├─ explain.py        #   生成论文讲解
│  ├─ translate.py      #   全文翻译（去参考文献/表格 → 分块 → 并发译 → 拼接）
│  ├─ recommend.py      #   相似论文推荐（S2 Recommendations）
│  ├─ embed.py          #   论文向量 + 语义检索（本地 model2vec 或外部嵌入 API，余弦排序）
│  ├─ importer.py       #   本地 PDF 批量导入（扫描→抽取→分类→入库，原地引用）
│  ├─ citegraph.py      #   引用关系图：抓 S2 参考文献 → 建库内互引边
│  ├─ verify.py         #   会议核实（查权威库，非大模型臆测）
│  └─ mcp_server.py     #   MCP 服务（将库以工具暴露给 Claude 等客户端）
├─ public/              # 前端（原生 JS/CSS/HTML）；vendor/ 内置 marked/pdf.js/echarts/katex
├─ Dockerfile / docker-compose.yml   # 单机自用容器化
└─ docs/                # 设计文档（ARCHITECTURE / AGENT / DATABASE / ROADMAP）
```

- **三方共享同一 `data/app.db`**：Node（better-sqlite3）与 Python（sqlite3）均读写，统一开启 WAL。
- **流式接口采用 NDJSON**：检索 / 入库 / 讲解 / 翻译 / 语义检索 / 本地导入等均将进度逐行推送至前端。

### Python Agent 命令

```bash
python -m agent search    --query "多模态大模型 幻觉检测" --sources arxiv,semanticscholar --expand   # 仅输出候选
python -m agent explain   --id <论文id> [--deep]      # 生成讲解（--deep 读取 PDF 全文）
python -m agent translate --id <论文id>               # 全文翻译
python -m agent recommend --id <论文id> [--limit 14]   # 相似论文推荐
python -m agent embed     --scope all|missing         # 建立/更新语义检索向量索引
python -m agent semsearch --query "缓解物体幻觉的解码方法" --k 30   # 语义检索
python -m agent import-pdfs [--no-enrich]             # 本地 PDF 批量导入（stdin 读路径数组）
python -m agent citegraph                             # 构建库内引用关系边
python -m agent verify-venue --sources dblp,semanticscholar   # 会议核实（stdin 读候选）
python -m agent ping                                  # 测试大模型连通性
```

### 约定

- **PDF、数据库、密钥不入 Git**（见 `.gitignore`）；更换设备后重新采集，或将文件放回 `data/pdfs/` 即可。
- 前端第三方库全部**本地化**于 `public/vendor/`，离线可用。
- 大模型配置优先级：`data/settings.json` > `.env`。**请妥善保管 API Key。**
