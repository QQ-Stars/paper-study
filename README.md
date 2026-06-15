# Paper-Study · 多模态幻觉论文精读工具

**文献管理 + 学术搜索 + 论文精读** 三合一的本地 Web 应用，为系统研读 MLLM（多模态大模型）幻觉方向的论文而做。

- 🔎 **采集**：输入研究方向（中/英皆可）→ 大模型扩展检索词 → 多源（arXiv / Semantic Scholar / OpenAlex / DBLP）检索去重 → 预览候选 → 勾选入库。
- 📖 **精读**：左侧论文列表 · 中间内嵌 PDF（PDF.js）· 右侧「论文讲解 / 译文 / 我的笔记 / 相似论文」四栏。
- 🧠 **大模型辅助**：一键生成**论文讲解**、**全文中文翻译**（读 PDF 全文、跳过参考文献、公式用 KaTeX 渲染）。
- 🔮 **语义检索**：本地嵌入（model2vec，无需 GPU/联网模型服务）按**大意**找论文，**中文 query 直接匹配英文论文**；顶栏「🔮 语义」开关，结果带相关度。
- 🔗 **相似论文**：阅读时一键找内容相近的论文（Semantic Scholar Recommendations），标注是否在库、可直接收录。
- 📂 **本地 PDF 导入**：把一个文件夹里的 PDF 一键扫描、抽取标题/摘要、自动分类入库（PDF 原地引用，不复制）。
- 📊 **洞察**：研究趋势（年份 × 方向堆叠图）+ **引用关系图**（库内谁引用谁，节点越大被引越多，点开即读）。
- 🏅 **CCF 分级**：每篇论文标注 CCF 推荐目录（第七版/2026）级别 A/B/C（据 `db/ccf_ranks.json`，618 个会议/期刊）；采集可勾「只采 CCF-A」，只收顶会/顶刊论文。
- ✅ **会议核实**：查权威库（DBLP / S2 / OpenAlex）还原真实发表会议，绝不臆造。
- 🤖 **MCP 服务**：把整库以 MCP 工具暴露给 Claude 等客户端，**对话式**检索文献、读讲解与属性、做综述、找研究空白（见下方「MCP 服务」）。
- ★ **收藏** · 学习进度 · 笔记 · 手动添加/编辑 · 总览看板（ECharts）。

> 主题：暖中性 + 赤陶「学刊」风，含深色模式、可拖拽调宽的三栏。

---

## 快速开始

需要 **Node.js**（已验证 v22）和 **Python 3.10+**。

```bash
cd study-app

# 1) Node 依赖（better-sqlite3）
npm install

# 2) Python agent 依赖（装在项目内 .venv，不污染系统）
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt      # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

# 3) 配置大模型 key（二选一）
#    a. 复制 .env.example 为 .env 填 LLM_API_KEY
#    b. 启动后在网页右上角 ⚙ 里填（存 data/settings.json）

# 4) 启动
node server.js
```

打开 **http://localhost:5173**。

> 大模型默认走 **DeepSeek**（OpenAI 兼容协议）；在 ⚙ 设置里可切换 DeepSeek / 通义千问 / OpenAI / Anthropic，或填自定义 Base URL 与模型名。

---

## 架构

```
study-app/
├─ server.js          # Node Web/API：静态资源 + PDF 流 + 各 REST/NDJSON 接口
├─ db.js              # SQLite 访问层（better-sqlite3，同步）
├─ db/schema.sql      # 表结构（papers / progress / notes / favorites / translations …）
├─ data/
│  ├─ app.db          # SQLite 数据库（WAL，gitignore）
│  ├─ pdfs/           # 采集下载的 PDF（gitignore）
│  └─ settings.json   # 模型/数据源/PDF 目录设置（gitignore，脱敏显示）
├─ agent/             # Python 采集 + 大模型 Agent（python -m agent <cmd>）
│  ├─ pipeline.py     #   两阶段检索：search（出候选）/ ingest-selected（入库）
│  ├─ sources/        #   数据源适配：arxiv / semanticscholar / openalex / dblp
│  ├─ llm.py          #   分类 / 扩词 / 讲解 / 翻译 的大模型调用
│  ├─ extract.py      #   PDF→文本：full_text 用 pymupdf4llm 转 Markdown（保留版面）
│  ├─ explain.py      #   生成论文讲解
│  ├─ translate.py    #   全文翻译（去参考文献/表格 → 分块 → 并发译 → 拼接）
│  ├─ recommend.py    #   相似论文推荐（S2 Recommendations）
│  ├─ embed.py        #   论文向量 + 语义检索（本地 model2vec 静态嵌入，余弦排序）
│  ├─ importer.py     #   本地 PDF 批量导入（扫描→抽取→分类→入库，原地引用）
│  ├─ citegraph.py    #   引用关系图：抓 S2 参考文献 → 建库内互引边 cite_edges
│  ├─ verify.py       #   会议核实（查权威库，非 LLM 臆测）
│  └─ mcp_server.py   #   MCP 服务：把论文库以工具暴露给 Claude 等客户端（对话式检索/读讲解/找空白）
├─ public/            # 前端（vanilla JS/CSS/HTML）
│  ├─ index.html / app.js / style.css
│  └─ vendor/         #   本地化第三方库：marked / pdf.js / echarts / katex / 字体
└─ docs/              # 设计文档（ARCHITECTURE / AGENT / DATABASE / ROADMAP）
```

- **三方共享同一个 `data/app.db`**：Node（better-sqlite3）读写、Python（sqlite3）读写，均开 WAL。
- **流式接口**走 NDJSON：检索 `/api/search`、入库 `/api/ingest-selected`、会议核实 `/api/verify-venue`、讲解 `/api/explain`、翻译 `/api/translate`、相似论文 `/api/recommend`、语义检索 `/api/semsearch`、建索引 `/api/embed`、本地导入 `/api/import-pdfs`、引用图构建 `/api/cite-build` 都把进度逐行推给前端做动画（引用图读取走 `GET /api/citegraph`）。

## Python Agent 命令

```bash
python -m agent search   --query "多模态大模型 幻觉检测" --sources arxiv,semanticscholar --expand   # 只出候选
python -m agent explain  --id <论文id> [--deep]     # 生成讲解（--deep 读 PDF 全文）
python -m agent translate --id <论文id>             # 全文翻译
python -m agent recommend --id <论文id> [--limit 14]  # 相似论文推荐（候选 JSON→stdout）
python -m agent embed    --scope all|missing        # 建/更新语义检索向量索引
python -m agent semsearch --query "缓解物体幻觉的解码方法" --k 30   # 语义检索（结果 JSON→stdout）
python -m agent import-pdfs [--no-enrich]           # 本地 PDF 批量导入（stdin 读路径数组）
python -m agent citegraph                           # 构建库内引用关系边（抓 S2 参考文献）
python -m agent norm-venues                         # 用大模型把库内会议名规整成标准简称
python -m agent verify-venue --sources dblp,semanticscholar   # 会议核实（stdin 读候选）
python -m agent ping                                # 测大模型连通性
```

## MCP 服务（让 Claude 直接查库）

把论文库以 [MCP](https://modelcontextprotocol.io) 工具暴露给 **Claude Code / Claude Desktop** 等客户端，在对话里检索文献、读讲解与属性、做研究综述、找研究空白。stdio 传输，**只读库**；服务用 `__file__` 定位 `data/app.db`，与启动目录无关。

注册到 Claude Code（换成你自己的绝对路径）：

```bash
claude mcp add paper-study -- F:/paper/研究方向细化/study-app/.venv/Scripts/python.exe -m agent.mcp_server
```

或写进 Claude Desktop 的 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "paper-study": {
      "command": "F:/paper/研究方向细化/study-app/.venv/Scripts/python.exe",
      "args": ["-m", "agent.mcp_server"]
    }
  }
}
```

**工具（8 个）**：

| 工具 | 作用 |
|---|---|
| `search_papers` | 关键词 + 属性过滤（方向/会议/年份/相关度/有无讲解/收藏，可排序） |
| `semantic_search` | 自然语言语义检索（中文 query 直接匹配英文论文） |
| `related_papers` | 库内与某篇语义相近的论文 |
| `get_paper` | 一篇的**全部属性**（题录 + AI 分类：方向/子主题/任务/模型/数据集/贡献/标签/相关度 + 笔记/进度/收藏 + 有无讲解/翻译/PDF） |
| `get_explainer` / `get_translation` | 取**讲解** / 中文翻译全文（Markdown） |
| `list_categories` | 库中在用的方向/子主题/任务词表及计数 |
| `library_overview` | 全库画像（方向/会议/年份分布、覆盖、相关度分桶）——**开题 / 找空白**用 |

例：对 Claude 说「用 `library_overview` 看看库里哪个方向论文最少」「`search_papers` 找 CVPR 2026 的物体幻觉缓解工作，逐篇 `get_explainer`，总结共同思路和没人做的点」。

## 用法要点

- **采集**：管理页输入方向 → 「检索」→（可编辑扩展检索词）→ 勾选候选 → 「入库」。搜不到时可「✍️ 手动添加」。
- **讲解**：阅读页「论文讲解」→「✨ 生成讲解」。勾「读PDF全文」让大模型通读全文（更准、更慢）。
- **翻译**：阅读页「译文」→「🌐 翻译全文」。自动读 PDF 全文、**跳过参考文献与表格**、分段并发翻译（约 1~3 分钟），公式由 KaTeX 渲染。
- **会议核实**：候选区选核实源 →「✓ 核实会议」，把 arXiv 预印还原成真实会议（查不到只标「仅预印本」）。
- **语义检索**：顶栏点「🔮 语义」→ 用一句话/中文描述（如「缓解物体幻觉的对比解码」）回车，按相关度排序全库，徽标显示分数。首次会**联网下载一次嵌入模型**（缓存进项目内 `.models/`）；新采集的论文检索时自动补索引，也可在 ⚙ 设置「重建语义索引」。
- **相似论文**：阅读页「相似论文」tab →「🔗 找相似论文」，列出内容相近的论文，可「+ 收录」一键入库（收录时自动分类）。
- **本地 PDF 导入**：管理页「📂 本地 PDF 批量导入」→ 填文件夹路径 → 扫描 → 勾选 → 导入；大模型抽标题/摘要并分类，默认联网补全会议/年份/引用。PDF 留在原处（原地引用），按 文件路径/arXiv/标题 三重去重。
- **收藏 ★ / 进度 / 笔记**：阅读页右栏即可标记；顶栏「☆ 收藏」筛选只看收藏。

## 说明 / 约定

- **PDF、数据库、密钥不入 git**（见 `.gitignore`）。换机器后重新采集或放回 `data/pdfs/` 即可。
- 讲解、翻译、收藏等都缓存进数据库，开论文时直接载入，不必重复生成。
- 第三方库全部**本地化**在 `public/vendor/`，离线可用。
- 大模型调用按 `data/settings.json` > `.env` 的优先级取配置。**请妥善保管 API Key**。
