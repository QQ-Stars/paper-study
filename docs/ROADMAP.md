# 路线图 · Paper-Study

> 架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。每个阶段都能**独立交付、独立可用**。
> 状态：☐ 待办 · ◐ 进行中 · ☑ 完成

---

## P1 · 数据库地基与迁移  ☑
**目标**：把数据从散落的 JSON/文件迁到 SQLite，Web 改为读数据库。用户体验不变，但为"自动加论文"铺好地基。

- ☑ 写 `db/schema.sql`（papers / progress / notes / paper_vectors / ingest_jobs / schema_migrations）
- ☑ Node 接入 `better-sqlite3`，开启 WAL
- ☑ 迁移脚本 `scripts/migrate.js`：papers.json + notes + progress + 讲解 md → SQLite（38 篇全部，含讲解）
- ☑ `server.js` 的 `/api/papers /api/note /api/progress /api/explainer` 改为读写 DB（前端零改动）
- ☑ PDF 仍从 `../paper` 提供（`/pdfbytes` 不变）
- **交付**：✅ 刷新页面一切照旧，数据已在 `data/app.db`。

## P2 · Python 采集 Agent v1（Semantic Scholar + arXiv）  ☑
**目标**：第一次能"自动把论文加进来"——**用聚合 API，免写爬虫**。

- ☑ 项目内 `.venv` + `requirements.txt`（httpx/feedparser/openai/anthropic/pydantic/tenacity/dotenv/pymupdf）
- ☑ `agent/llm.py`：多供应商封装（OpenAI兼容，DeepSeek 已验证）+ JSON结构化输出 + 重试；`.env` 配置
- ☑ `agent/models.py`：pydantic 模型（PaperStub / PaperAttributes）+ 受控词表
- ☑ `agent/sources/semanticscholar.py`（主力）+ `arxiv.py`（相关度排序，venue从comment识别）
- ☑ `agent/pipeline.py` + `__main__.py`：`ingest / ping / purge` 命令
- ☑ LLM 只做自定义分类 + 相关性打分；下载开放PDF到 data/pdfs；去重；server 改为多目录找PDF
- **交付**：✅ `python -m agent ingest --query "multimodal hallucination" --sources arxiv --max 8 --min-relevance 0.5` → 网页里多出 6 篇自动抓取+分类好、可直接阅读的论文。
- ⚠️ 注：S2 匿名接口高峰期会 429（已优雅跳过）；要稳定用 S2，建议申请其免费 API key 填入 `.env`。

## P3 · 抽取质量 + 自动讲解 + 相关性  ☑
- ☑ 结构化输出 + 失败重试 + pydantic 校验
- ☑ 相关性打分（与目标方向 0–1），低分过滤/标记
- ☑ LLM 自动生成"科学方法论讲解" md，存入 `papers.explainer`（阅读页一键生成，可读 PDF 全文）
- **交付**：✅ 自动入库的论文属性又准又全；阅读页「✨ 生成讲解」一键出讲解。

## P3.5 · 阅读体验增强  ☑（已交付）
> 围绕"读懂一篇论文"做厚右栏与采集体验。

- ☑ **两阶段采集向导**：扩词（可编辑）→ 流式检索候选 → 勾选入库，全程 NDJSON 进度动画
- ☑ **会议核实** `agent/verify.py`：查 DBLP/S2/OpenAlex 权威库还原真实会议，arXiv→CVPR 更正，查不到只标「仅预印本」，**故意不用 LLM**（避免对新论文幻觉会议）
- ☑ **全文翻译** `agent/translate.py`：PDF→Markdown(pymupdf4llm)→去参考文献/表格→分块→并发译中文，公式 KaTeX 渲染，存 `translations`
- ☑ **收藏 ★**、手动添加/编辑论文、阅读栏可拖拽调宽、设置移到顶栏全局弹窗
- ☑ **UI v3 学刊风重设计**（赤陶刊头、领域配色徽章、ECharts 看板、深色模式）

## P4 · 多源补全 + 语义检索  ☑
> 顶会覆盖已由 S2/arXiv 解决；本阶段做"补全"与"更聪明的找"。

- ☑ `sources/openalex.py` + `sources/dblp.py`：多源检索 + 跨源去重（arxiv_id/标题归一）
- ☑ **语义检索** `agent/embed.py`：本地 **model2vec** 静态嵌入（纯 numpy/tokenizers，无 torch/onnx，多语种→中文 query 直接匹配英文论文）→ 向量存 `paper_vectors` → 余弦暴力排序。顶栏「🔮 语义」开关，结果带相关度徽标；新论文检索时自动补索引，⚙ 可全量重建
- ☑ **相似论文推荐** `agent/recommend.py`：S2 Recommendations API（`forpaper/{ARXIV|DOI|paperId}`，`from=all-cs`）→ 阅读页「相似论文」tab 一键找相近论文，标注是否在库，可直接收录（收录时现做分类，保持库内类别一致）
- ☑ **本地 PDF 批量导入** `agent/importer.py`：管理页填文件夹 → Node 递归扫 PDF → 勾选 → 大模型抽标题/摘要(parse_pdf_meta) →（可选 S2 补全会议/年份/引用）→ 分类 → 入库；**PDF 原地引用不复制**，按 pdf_path/arxiv_id/标题三重去重（重复扫描幂等）
- **交付**：✅ 一个方向一网打尽——多源采集 + 语义找相似/相关 + 把手头一堆 PDF 一键变成分好类、可读可译的库。

## P5 · 后台任务 + 定时 + 网页触发  ☑
> 独立「采集」页：后台跑、评审门、定时三合一。管理页的即时采集保留并存。

- ☑ `ingest_jobs` 表扩列（only_a/queries/schedule_id）+ 新表 `job_candidates`（暂存待确认候选）、`job_schedules`（定时规格）；启动时 `resetOrphanJobs` 把残留 running 重置为 failed
- ☑ `agent/jobs.py` → `run_job(job_id)`：后台扩词检索（`expand_n=12`、只取不在库的新候选）→ 暂存 job_candidates → 置 `review` 等人工确认
- ☑ Node API：`POST/GET /api/jobs`（发起/列表）、`/api/jobs/detail|confirm|delete`；`/api/schedules` 增删/启停
- ☑ 前端「采集」页：发起后台采集 + 任务卡（状态徽标/轮询/可滚动）+ **评审门**——候选勾选「确认入库」才真正入库
- ☑ 定时调度：Node `setInterval(checkSchedules, 10min)` + 启动延迟跑一次；到期自动建 job 跑、`next_run` 顺延
- ☑ **检索词多样化**（需求2）：`expand_queries` 6 角度、temp 0.6、去重——同步采集一并受益
- **交付**：✅ 网页发起后台采集、定时养库、评审后入库；已端到端验证（confirm 1/3 → 恰好 1 篇入库）。

## P6 · 部署上线  ☐
- ☐ `Dockerfile` + `docker-compose.yml`（web + agent worker + 共享卷）
- ☐ `.env` 密钥管理；对象存储托管 PDF（R2/OSS）
- ☐ 账号登录（多用户：笔记/进度按用户隔离）
- ☐ 反向代理 + 自动 HTTPS（Caddy）+ 域名
- ☐ 备份策略（SQLite/Postgres）
- **交付**：一个可公开访问、稳定运行的正规产品。

## P7 · 增强（可选）  ◐
- ☑ 阅读/相似推荐（Semantic Scholar Recommendations，见 P4 相似论文）
- ☑ **洞察视图**：研究趋势（年份 × 方向堆叠柱）+ **引用关系图**（`agent/citegraph.py` 抓 S2 参考文献建库内互引边 `cite_edges`，ECharts 力导向图，节点大小=被库内引用数）
- ☑ 把论文库包成 **MCP server**（`agent/mcp_server.py`，FastMCP/stdio，只读库）：8 个工具
  `search_papers` / `semantic_search` / `related_papers` / `get_paper` / `get_explainer` / `get_translation` / `list_categories` / `library_overview`，
  让 Claude 等客户端对话式检索、读讲解与属性、做综述 / 找研究空白（注册见 README「MCP 服务」）

---

## 当前焦点
P1–P5 全部交付（数据库、采集 Agent、多源、讲解、翻译、会议核实、收藏、UI、语义检索、相似论文、本地 PDF 导入、**后台任务 + 定时 + 评审入库**）。
另：PDF 多源解析链已落地（arXiv → Unpaywall → Semantic Scholar → OpenReview），库内 124/130 篇有本地 PDF。
已完成：**PDF 解析质量升级**（讲解裁参考文献 + first_pages 结构化）、**P7 MCP server**（库以工具暴露给 Claude，对话式检索/读讲解/找空白）。
**下一步候选**：P6 **部署上线**（Docker / 多用户 / 对象存储 / HTTPS，仅在要对外共享或远程访问时才需要）；或继续按需做 P7 其余增强。
