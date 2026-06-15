# Paper-Study · 文献管理 · 学术搜索 · 论文精读

> 一个**在你自己电脑上运行**的网页应用：自动搜论文 → AI 自动分类入库 → 一键生成中文**讲解 / 翻译** → **语义检索**找研究空白。
> 论文、笔记、API 密钥**全部只存在本地**，不上传任何服务器。

**适用于任意研究方向**：在 ⚙ 设置里填上你的研究主题，大模型采集时便按它给论文分类、打相关度；换个主题即可研究别的领域。

---

## 目录

1. [它能做什么](#它能做什么)
2. [准备工作（先装好这几样）](#一准备工作先装好这几样)
3. [安装与启动（手把手）](#二安装与启动手把手)
4. [配置大模型 Key（必做）](#三配置大模型-key必做)
5. [日常怎么用](#四日常怎么用)
6. [用 Docker 一键部署（可选）](#五用-docker-一键部署可选)
7. [让语义检索更准：外部嵌入 API（可选）](#六让语义检索更准外部嵌入-api可选)
8. [接入 Claude：MCP 服务（可选）](#七接入-claudemcp-服务可选)
9. [常见问题 FAQ](#八常见问题-faq)
10. [给开发者](#九给开发者)

---

## 它能做什么

- 🔎 **自动采集**：输入研究方向（中/英皆可）→ 大模型扩展检索词 → 多源（arXiv / Semantic Scholar / OpenAlex / DBLP）检索去重 → 预览候选 → 勾选入库。
- 📖 **沉浸精读**：左侧论文列表 · 中间内嵌 PDF · 右侧「论文讲解 / 译文 / 我的笔记 / 相似论文」四栏。
- 🧠 **大模型辅助**：一键生成**论文讲解**、**全文中文翻译**（读 PDF 全文、跳过参考文献、公式用 KaTeX 渲染）。
- 🔮 **语义检索**：按**大意**找论文，**中文描述直接匹配英文论文**；默认本地嵌入（无需 GPU），也可切到更准的外部 API（如硅基流动 `BAAI/bge-m3`）。
- 🔗 **相似论文**：阅读时一键找内容相近的论文，标注是否在库、可直接收录。
- 📂 **本地 PDF 导入**：把一个文件夹里的 PDF 一键扫描、抽取标题/摘要、自动分类入库（原地引用，不复制）。
- 📊 **洞察看板**：研究趋势（年份 × 方向）+ 引用关系图（库内谁引用谁）。
- 🏅 **CCF 分级**：每篇标注 CCF 推荐目录级别 A/B/C；采集可勾「只采 CCF-A」只收顶会/顶刊。
- ✅ **会议核实**：查权威库还原论文真实发表会议，绝不臆造。
- 🤖 **MCP 服务**：把整库以工具暴露给 Claude 等客户端，**对话式**检索、读讲解、找研究空白。
- ★ 收藏 · 学习进度 · 笔记 · 手动添加 · 深色模式 · 可拖拽调宽的三栏。

---

## 一、准备工作（先装好这几样）

> **怕装环境？** 直接跳到 [用 Docker 一键部署](#五用-docker-一键部署可选)，只需装一个 Docker Desktop，一条命令搞定。下面这套是「手动安装」，每一步都看得见、更好排查。

跑这个工具，电脑里要先有 **3 个免费软件**。装的时候一路点「下一步 / Next」即可，注意下面标★的地方。

| 软件 | 作用 | 下载 | 装完怎么验证 |
|---|---|---|---|
| **Node.js**（建议 LTS 20 或更高） | 跑网页服务器 | <https://nodejs.org> | 打开终端输 `node -v`，能看到版本号（如 v20.x） |
| **Python 3.10 或更高** | 跑 AI 采集/讲解/翻译 | <https://www.python.org/downloads/> | 输 `python --version` 有版本号 |
| **Git**（可选） | 下载本项目 | <https://git-scm.com> | 输 `git --version` 有版本号 |

> ★ **装 Python 时（Windows）务必勾选页面底部的「Add Python to PATH」**，否则后面 `python` 命令会找不到。
> ★ 不想装 Git 也行：到本项目 GitHub 页面点绿色 **Code → Download ZIP**，解压即可。

**怎么打开「终端」？**
- Windows：开始菜单搜 **PowerShell**（或「终端 / Terminal」），点开。
- macOS：聚焦搜索（⌘+空格）输 **终端 / Terminal**。

---

## 二、安装与启动（手把手）

在终端里**逐条**执行（一条条复制粘贴回车，等它跑完再下一条）：

### 1) 下载项目

用 Git：

```bash
git clone https://github.com/QQ-Stars/paper-study.git
cd paper-study
```

> 如果你下载的是 ZIP：解压后，在解压出来的文件夹里打开终端即可（里面应能看到 `server.js`、`package.json`）。

### 2) 装网页端依赖

```bash
npm install
```

### 3) 装 AI 端（Python）依赖

依赖会装进项目自带的 `.venv` 文件夹，**不弄脏系统 Python**。按你的系统二选一：

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

> 这一步会联网下载若干 Python 包，可能要几分钟。只要**最后没有红色 ERROR**就算成功（黄色 warning 可忽略）。

### 4) 启动

```bash
node server.js
```

看到 **`论文学习 App 已启动`** 字样后，用浏览器打开 👉 **<http://localhost:5173>**

- **关闭**：回到终端按 `Ctrl + C`。
- **下次再启动**：进入项目文件夹，直接 `node server.js` 即可（依赖不用再装）。

---

## 三、配置大模型 Key（必做）

「自动分类、写讲解、翻译」都要调用大模型，需要你提供一个 **API Key**（相当于调用大模型的「门票」）。**没有 Key，采集和讲解会用不了。**

**最省事的做法（在网页里填）：**

1. 注册一个大模型服务、拿到 Key。新手推荐 **DeepSeek**（便宜、兼容好）：
   打开 <https://platform.deepseek.com> → 注册 → 创建 API Key（一串 `sk-` 开头的字符，**复制好**）。
2. 回到本工具网页，点右上角 **⚙ 设置** → 供应商选 **DeepSeek** → 把 Key 粘进「API Key」框 → **保存** → 点「测试连接」显示正常即可。

> 也支持**通义千问 / OpenAI / Anthropic**，或自填 Base URL 与模型名（任何 OpenAI 兼容接口都行）。
> 🔒 Key 只写进你电脑本地的 `data/settings.json`，**不会上传任何地方**，也不会进 Git。

---

## 四、日常怎么用

| 想做的事 | 怎么操作 |
|---|---|
| **采集论文** | 顶栏「采集 / 管理」→ 输入方向（如「多模态大模型 物体幻觉 缓解」）→ 检索 →（可改 AI 扩展出的检索词）→ 勾选 → 入库 |
| **读论文 + AI 讲解** | 「阅读」页：左选论文、中看 PDF、右栏「论文讲解」点「✨ 生成讲解」出中文精读（勾「读 PDF 全文」更准更慢） |
| **全文翻译** | 阅读页「译文」→「🌐 翻译全文」（自动跳过参考文献，约 1~3 分钟） |
| **语义检索** | 顶栏「🔮 语义」开 → 用一句话描述要找的（如「用对比解码缓解幻觉」）→ 按相关度排序全库 |
| **找相似论文** | 阅读页「相似论文」tab →「🔗 找相似论文」，可「+ 收录」一键入库 |
| **导入本地 PDF** | 管理页「📂 本地 PDF 批量导入」→ 填文件夹路径 → 扫描 → 勾选 → 导入 |
| **看趋势/找空白** | 「洞察」页看趋势与引用图；或配好 [MCP](#七接入-claudemcp-服务可选) 让 Claude 帮你分析 |
| **收藏 / 进度 / 笔记** | 阅读页右栏标记；顶栏「☆ 收藏」只看收藏 |

> 讲解、翻译、收藏等都会**缓存进数据库**，下次开同一篇直接载入，不必重复生成。

---

## 五、用 Docker 一键部署（可选）

适合**不想手动装 Node/Python** 的人。只需先装 **Docker Desktop**（<https://www.docker.com/products/docker-desktop>），启动它，然后在项目文件夹里：

```bash
docker compose up -d --build      # 第一次会构建镜像，等几分钟 → http://localhost:5173
docker compose logs -f            # 看运行日志（Ctrl+C 退出看日志，不会停服务）
docker compose down               # 停止
```

- **数据不丢**：论文库 / PDF / 设置都存在项目的 `data/` 文件夹（挂载进容器），重启容器照旧。
- **填 Key**：启动后照 [第三步](#三配置大模型-key必做)在网页 ⚙ 设置里填即可（会存到 `data/settings.json`）。
- **改端口**：编辑 `docker-compose.yml` 里 `ports: "5173:5173"` 左边那个数字。
- **国内拉不到基础镜像**（构建报 `node:20-bookworm-slim ... 403/timeout`）时，换国内镜像源构建：

  **macOS / Linux：**
  ```bash
  NODE_IMAGE=docker.m.daocloud.io/library/node:20-bookworm-slim docker compose up -d --build
  ```
  **Windows PowerShell：**
  ```powershell
  $env:NODE_IMAGE="docker.m.daocloud.io/library/node:20-bookworm-slim"; docker compose up -d --build
  ```

> 多用户登录、HTTPS、对象存储托管 PDF 等「对外公开」能力见 [docs/ROADMAP.md](docs/ROADMAP.md) 的 P6——单机自用不需要。

---

## 六、让语义检索更准：外部嵌入 API（可选）

默认语义检索用**本地小模型**（首次会联网下载一次，存进项目内 `.models/`，之后离线可用，无需 GPU）。
想要**更准**，可以换成外部嵌入 API（任何 OpenAI 兼容的 `/embeddings` 接口，如硅基流动的 `BAAI/bge-m3`，多语种、8K 上下文）：

1. 网页 **⚙ 设置 → 「语义检索嵌入」** → 来源选「外部 API」。
2. 填 **Base URL**（如 `https://api.siliconflow.cn/v1`）、**模型**（如 `BAAI/bge-m3`）、**API Key** → 保存。
3. 切换后，**下次语义检索会自动用新模型重嵌全库**，无需手动操作。

---

## 七、接入 Claude：MCP 服务（可选）

把你的论文库以 [MCP](https://modelcontextprotocol.io) 工具暴露给 **Claude Code / Claude Desktop**，就能在对话里直接检索文献、读讲解与属性、做综述、**找研究空白**。只读库、stdio 传输。

> 下面命令里的 **`<项目路径>`** 要换成**你电脑上 `paper-study` 文件夹的完整路径**。
> 怎么查路径：在该文件夹的终端里运行 `pwd`（macOS/Linux）或在 PowerShell 里 `(Get-Location).Path`（Windows）。
> Python 解释器位置：Windows 是 `<项目路径>\.venv\Scripts\python.exe`，macOS/Linux 是 `<项目路径>/.venv/bin/python`。

**注册到 Claude Code**（Windows 示例，路径用正斜杠 `/`）：

```bash
claude mcp add paper-study -- <项目路径>/.venv/Scripts/python.exe <项目路径>/agent/mcp_server.py
```

**或写进 Claude Desktop 的 `claude_desktop_config.json`：**

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

> macOS/Linux 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python` 即可。改完**新开一个 Claude 会话**才生效。

**提供的工具（8 个）：**

| 工具 | 作用 |
|---|---|
| `search_papers` | 关键词 + 属性过滤（方向/会议/年份/相关度/有无讲解/收藏，可排序） |
| `semantic_search` | 自然语言语义检索（中文描述直接匹配英文论文） |
| `related_papers` | 库内与某篇语义相近的论文 |
| `get_paper` | 一篇的**全部属性**（题录 + AI 分类 + 笔记/进度/收藏 + 有无讲解/翻译/PDF） |
| `get_explainer` / `get_translation` | 取**讲解** / 中文翻译全文 |
| `list_categories` | 库中在用的方向/子主题/任务词表及计数 |
| `library_overview` | 全库画像（方向/会议/年份分布）——**开题 / 找空白**用 |

例：对 Claude 说「用 `library_overview` 看看库里哪个方向论文最少」「`search_papers` 找 CVPR 2026 的物体幻觉缓解工作，逐篇 `get_explainer`，总结共同思路和还没人做的点」。

---

## 八、常见问题 FAQ

- **端口 5173 被占用 / 打不开**：换个端口启动 ——
  Windows PowerShell：`$env:PORT=5174; node server.js`；macOS/Linux：`PORT=5174 node server.js`，然后开 <http://localhost:5174>。
- **采集 / 讲解报错或没反应**：多半是**没填 API Key 或 Key 不对**。到 ⚙ 设置点「测试连接」排查。
- **`python` 命令找不到**：装 Python 时没勾「Add Python to PATH」。重装勾上，或改用 `py`（Windows）/ `python3`（macOS/Linux）。
- **`npm install` 时 better-sqlite3 编译失败**：缺构建工具。最省事是**改用 [Docker 方式](#五用-docker-一键部署可选)**；或 Windows 装「Visual Studio Build Tools（含 C++）」后重试。
- **第一次语义检索很慢**：要联网下载一次本地嵌入模型（存到项目内 `.models/`），之后就快了；也可切到[外部嵌入 API](#六让语义检索更准外部嵌入-api可选) 免下载。
- **DBLP 源出的论文很少**：正常现象。DBLP 限流很严，本工具有意只让它查前几个检索词拿**干净的会议名**，论文召回主要靠 arXiv / Semantic Scholar / OpenAlex。
- **Semantic Scholar 偶尔限流**：可在 ⚙ 设置填一个免费的 S2 API Key 更稳（不填也能用）。

---

## 九、给开发者

### 技术栈与目录

```
paper-study/            # 克隆下来的项目根目录
├─ server.js            # Node Web/API：静态资源 + PDF 流 + 各 REST/NDJSON 接口
├─ db.js                # SQLite 访问层（better-sqlite3，同步）
├─ db/schema.sql        # 表结构（papers / progress / notes / favorites / translations …）
├─ data/                # 运行期数据（全部 gitignore，不入库）
│  ├─ app.db            #   SQLite 数据库（WAL）
│  ├─ pdfs/             #   采集下载的 PDF
│  └─ settings.json     #   模型/数据源/PDF 目录设置（含各类 Key，脱敏显示）
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
│  ├─ verify.py         #   会议核实（查权威库，非 LLM 臆测）
│  └─ mcp_server.py     #   MCP 服务（把库以工具暴露给 Claude 等客户端）
├─ public/              # 前端（原生 JS/CSS/HTML）；vendor/ 内置 marked/pdf.js/echarts/katex
├─ Dockerfile / docker-compose.yml   # 单机自用容器化
└─ docs/                # 设计文档（ARCHITECTURE / AGENT / DATABASE / ROADMAP）
```

- **三方共享同一个 `data/app.db`**：Node（better-sqlite3）与 Python（sqlite3）都读写，均开 WAL。
- **流式接口走 NDJSON**：检索 / 入库 / 讲解 / 翻译 / 语义检索 / 本地导入等都把进度逐行推给前端做动画。

### Python Agent 命令

```bash
python -m agent search    --query "多模态大模型 幻觉检测" --sources arxiv,semanticscholar --expand   # 只出候选
python -m agent explain   --id <论文id> [--deep]      # 生成讲解（--deep 读 PDF 全文）
python -m agent translate --id <论文id>               # 全文翻译
python -m agent recommend --id <论文id> [--limit 14]   # 相似论文推荐
python -m agent embed     --scope all|missing         # 建/更新语义检索向量索引
python -m agent semsearch --query "缓解物体幻觉的解码方法" --k 30   # 语义检索
python -m agent import-pdfs [--no-enrich]             # 本地 PDF 批量导入（stdin 读路径数组）
python -m agent citegraph                             # 构建库内引用关系边
python -m agent verify-venue --sources dblp,semanticscholar   # 会议核实（stdin 读候选）
python -m agent ping                                  # 测大模型连通性
```

### 约定

- **PDF、数据库、密钥不入 Git**（见 `.gitignore`）；换机器后重新采集或放回 `data/pdfs/` 即可。
- 第三方前端库全部**本地化**在 `public/vendor/`，离线可用。
- 大模型配置优先级：`data/settings.json` > `.env`。**请妥善保管 API Key。**
