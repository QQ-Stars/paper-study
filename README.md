# Paper-Study · 多模态幻觉论文精读工具

**文献管理 + 学术搜索 + 论文精读** 三合一的本地 Web 应用，为系统研读 MLLM（多模态大模型）幻觉方向的论文而做。

- 🔎 **采集**：输入研究方向（中/英皆可）→ 大模型扩展检索词 → 多源（arXiv / Semantic Scholar / OpenAlex / DBLP）检索去重 → 预览候选 → 勾选入库。
- 📖 **精读**：左侧论文列表 · 中间内嵌 PDF（PDF.js）· 右侧「论文讲解 / 译文 / 我的笔记 / 相似论文」四栏。
- 🧠 **大模型辅助**：一键生成**论文讲解**、**全文中文翻译**（读 PDF 全文、跳过参考文献、公式用 KaTeX 渲染）。
- 🔮 **语义检索**：本地嵌入（model2vec，无需 GPU/联网模型服务）按**大意**找论文，**中文 query 直接匹配英文论文**；顶栏「🔮 语义」开关，结果带相关度。
- 🔗 **相似论文**：阅读时一键找内容相近的论文（Semantic Scholar Recommendations），标注是否在库、可直接收录。
- ✅ **会议核实**：查权威库（DBLP / S2 / OpenAlex）还原真实发表会议，绝不臆造。
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
│  └─ verify.py       #   会议核实（查权威库，非 LLM 臆测）
├─ public/            # 前端（vanilla JS/CSS/HTML）
│  ├─ index.html / app.js / style.css
│  └─ vendor/         #   本地化第三方库：marked / pdf.js / echarts / katex / 字体
└─ docs/              # 设计文档（ARCHITECTURE / AGENT / DATABASE / ROADMAP）
```

- **三方共享同一个 `data/app.db`**：Node（better-sqlite3）读写、Python（sqlite3）读写，均开 WAL。
- **流式接口**走 NDJSON：检索 `/api/search`、入库 `/api/ingest-selected`、会议核实 `/api/verify-venue`、讲解 `/api/explain`、翻译 `/api/translate`、相似论文 `/api/recommend`、语义检索 `/api/semsearch`、建索引 `/api/embed` 都把进度逐行推给前端做动画。

## Python Agent 命令

```bash
python -m agent search   --query "多模态大模型 幻觉检测" --sources arxiv,semanticscholar --expand   # 只出候选
python -m agent explain  --id <论文id> [--deep]     # 生成讲解（--deep 读 PDF 全文）
python -m agent translate --id <论文id>             # 全文翻译
python -m agent recommend --id <论文id> [--limit 14]  # 相似论文推荐（候选 JSON→stdout）
python -m agent embed    --scope all|missing        # 建/更新语义检索向量索引
python -m agent semsearch --query "缓解物体幻觉的解码方法" --k 30   # 语义检索（结果 JSON→stdout）
python -m agent verify-venue --sources dblp,semanticscholar   # 会议核实（stdin 读候选）
python -m agent ping                                # 测大模型连通性
```

## 用法要点

- **采集**：管理页输入方向 → 「检索」→（可编辑扩展检索词）→ 勾选候选 → 「入库」。搜不到时可「✍️ 手动添加」。
- **讲解**：阅读页「论文讲解」→「✨ 生成讲解」。勾「读PDF全文」让大模型通读全文（更准、更慢）。
- **翻译**：阅读页「译文」→「🌐 翻译全文」。自动读 PDF 全文、**跳过参考文献与表格**、分段并发翻译（约 1~3 分钟），公式由 KaTeX 渲染。
- **会议核实**：候选区选核实源 →「✓ 核实会议」，把 arXiv 预印还原成真实会议（查不到只标「仅预印本」）。
- **语义检索**：顶栏点「🔮 语义」→ 用一句话/中文描述（如「缓解物体幻觉的对比解码」）回车，按相关度排序全库，徽标显示分数。首次会**联网下载一次嵌入模型**（缓存进项目内 `.models/`）；新采集的论文检索时自动补索引，也可在 ⚙ 设置「重建语义索引」。
- **相似论文**：阅读页「相似论文」tab →「🔗 找相似论文」，列出内容相近的论文，可「+ 收录」一键入库（收录时自动分类）。
- **收藏 ★ / 进度 / 笔记**：阅读页右栏即可标记；顶栏「☆ 收藏」筛选只看收藏。

## 说明 / 约定

- **PDF、数据库、密钥不入 git**（见 `.gitignore`）。换机器后重新采集或放回 `data/pdfs/` 即可。
- 讲解、翻译、收藏等都缓存进数据库，开论文时直接载入，不必重复生成。
- 第三方库全部**本地化**在 `public/vendor/`，离线可用。
- 大模型调用按 `data/settings.json` > `.env` 的优先级取配置。**请妥善保管 API Key**。
