# 路线图 · Paper-Study

> 架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。每个阶段都能**独立交付、独立可用**。
> 状态：☐ 待办 · ◐ 进行中 · ☑ 完成

---

## P1 · 数据库地基与迁移  ◐
**目标**：把数据从散落的 JSON/文件迁到 SQLite，Web 改为读数据库。用户体验不变，但为"自动加论文"铺好地基。

- ☐ 写 `db/schema.sql`（papers / progress / notes / ingest_jobs）
- ☐ Node 接入 `better-sqlite3`，开启 WAL
- ☐ 迁移脚本：`papers.json` + `notes/*.md` + `progress.json` → SQLite（38 篇 + 现有笔记/进度）
- ☐ `server.js` 的 `/api/papers /api/note /api/progress` 改为读写 DB
- ☐ PDF 仍从 `data/pdfs|../paper` 提供
- **交付**：刷新页面，一切照旧，但数据已在 `data/app.db` 里。

## P2 · Python 采集 Agent v1（Semantic Scholar + arXiv）  ☐
**目标**：第一次能"自动把论文加进来"——**用聚合 API，免写爬虫**。

- ☐ 项目内 `.venv` + `requirements.txt`（httpx, feedparser, openai, anthropic, pydantic, tenacity, python-dotenv, pymupdf）
- ☐ `agent/llm.py`：多供应商统一封装（OpenAI兼容 + Anthropic），`.env` 配置
- ☐ `agent/models.py`：pydantic 模型（PaperStub / PaperAttributes）
- ☐ `agent/sources/semanticscholar.py`：bulk 搜索（拿 摘要/TLDR/领域/引用/开放PDF链接）
- ☐ `agent/sources/arxiv.py`：最新预印本兜底
- ☐ `agent/pipeline.py` + `__main__.py`：`python -m agent ingest --query "multimodal hallucination" --max 30`
- ☐ LLM **只做自定义分类**(type/topic)；多数无需下 PDF；写入 SQLite、去重
- **交付**：跑一条命令，网页里就多出一批自动抓取+分类好的论文（含 TLDR/引用数）。

## P3 · 抽取质量 + 自动讲解 + 相关性  ☐
- ☐ 结构化输出 + 失败重试 + pydantic 校验
- ☐ 相关性打分（与目标方向 0–1），低分过滤/标记
- ☐ 可选：LLM 自动生成"科学方法论讲解" md，存入 `papers.explainer`
- ☐ 抽取质量自检（抽样人工校对）
- **交付**：自动入库的论文属性又准又全，讲解可一键生成。

## P4 · 多源补全 + 语义检索  ☐
> 顶会覆盖已由 S2/arXiv 解决；本阶段做"补全"与"更聪明的找"。

- ☐ `sources/openalex.py`：交叉校验、补全 S2 缺失字段、用四级主题校正分类
- ☐ 跨源去重/合并（同一篇在多源出现 → 合并为一条，补全 arxiv_id/venue）
- ☐ **语义检索**：`agent/embed.py` 论文向量（SPECTER2/自算）+ 近邻检索（faiss/sqlite-vss）
- ☐ （兜底）极少数只在会议官网、且 API 查不到的，再单独一次性抓取
- **交付**：一个方向一网打尽，且能"按语义找相似/相关论文"。

## P5 · 后台任务 + 定时 + 网页触发  ☐
- ☐ `ingest_jobs` 表 + Python worker（轮询执行）
- ☐ Node API：`POST /api/ingest`（发起任务）、`GET /api/jobs`（看进度）
- ☐ 前端：搜索框旁"抓取该方向"按钮 + 任务进度
- ☐ 定时调度（如每周抓新接收的相关论文）
- **交付**：网页点一下就采集；系统能"自己养着自己长"。

## P6 · 部署上线  ☐
- ☐ `Dockerfile` + `docker-compose.yml`（web + agent worker + 共享卷）
- ☐ `.env` 密钥管理；对象存储托管 PDF（R2/OSS）
- ☐ 账号登录（多用户：笔记/进度按用户隔离）
- ☐ 反向代理 + 自动 HTTPS（Caddy）+ 域名
- ☐ 备份策略（SQLite/Postgres）
- **交付**：一个可公开访问、稳定运行的正规产品。

## P7 · 增强（可选）  ☐
- ☐ 阅读推荐（Semantic Scholar Recommendations API）、引用关系图、趋势分析
- ☐ 把论文库包成 **MCP server**，让 Claude 等客户端可"对话式"检索/分类/管理论文

---

## 当前焦点
**P1 数据库地基** —— 它是后面一切的前提。建议从这里开始。
