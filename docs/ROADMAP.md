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

## P2 · Python 采集 Agent v1（arXiv）  ☐
**目标**：第一次能"自动把论文加进来"。

- ☐ 项目内 `.venv` + `requirements.txt`（pymupdf, openai, anthropic, pydantic, httpx, feedparser…）
- ☐ `agent/llm.py`：多供应商统一封装（OpenAI兼容 + Anthropic），`.env` 配置
- ☐ `agent/schema.py`：pydantic 属性 schema
- ☐ `agent/extract.py`：PDF→文本（PyMuPDF，取摘要+前N页）
- ☐ `agent/sources/arxiv.py`：官方 API 检索
- ☐ `agent/pipeline.py` + `__main__.py`：`python -m agent ingest --query "multimodal hallucination" --max 20`
- ☐ 写入 SQLite，去重（arxiv_id）
- **交付**：跑一条命令，网页里就多出一批自动抓取+分类好的 arXiv 论文。

## P3 · 抽取质量 + 自动讲解 + 相关性  ☐
- ☐ 结构化输出 + 失败重试 + pydantic 校验
- ☐ 相关性打分（与目标方向 0–1），低分过滤/标记
- ☐ 可选：LLM 自动生成"科学方法论讲解" md，存入 `papers.explainer`
- ☐ 抽取质量自检（抽样人工校对）
- **交付**：自动入库的论文属性又准又全，讲解可一键生成。

## P4 · 全顶会适配器  ☐
- ☐ `sources/cvf.py`（CVPR/ICCV/ECCV）
- ☐ `sources/openreview.py`（ICLR/NeurIPS 部分）
- ☐ `sources/acl.py`（ACL/EMNLP）
- ☐ `sources/pmlr.py`（ICML）· `sources/neurips.py` · `sources/aaai.py`
- ☐ 统一 `search(query, year)` 接口；各源限速与缓存
- **交付**：一个方向，能从所有主流顶会一网打尽。

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
- ☐ 语义搜索/相似论文（embedding + 向量检索）
- ☐ 阅读推荐、引用关系图、趋势分析

---

## 当前焦点
**P1 数据库地基** —— 它是后面一切的前提。建议从这里开始。
