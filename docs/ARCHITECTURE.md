# 架构设计 · Paper-Study

> 从"个人本地论文阅读工具"演进为"可自动采集顶会论文、可部署上线的系统"。
> 本文是技术蓝图；阶段计划见 [ROADMAP.md](./ROADMAP.md)。

## 0. 已锁定的技术决策（2026-06）

| 维度 | 选择 | 理由 |
|---|---|---|
| 前端 / Web API | **Node.js**（沿用现有 `server.js`，后续可升级 Fastify） | 复用现成代码 |
| 采集 Agent | **Python** | PDF 提取 / LLM / 爬虫生态最强 |
| 数据库 | **SQLite（WAL 模式）单文件** | 零运维、Node+Python 共享一个文件；将来可换 PostgreSQL |
| 大模型 | **多供应商可切换**（DeepSeek / Qwen / OpenAI / Claude） | 填 key 即用，随时换 |
| 数据源 | **全顶会**（arXiv 先行 → CVF/OpenReview/ACL/PMLR/NeurIPS/AAAI） | 覆盖面最大 |
| 部署 | **先本地，后期 Docker 上线** | 小白先把功能跑通，别被运维卡住 |

> 📦 **依赖只在项目内**：Node 包装到 `node_modules/`，Python 包装到项目内虚拟环境 `.venv/`（均 gitignore），不污染其他目录。

## 1. 系统模块

```
┌─────────────┐   读/写    ┌──────────────────┐
│  前端 Web    │ ───────▶ │   Node Web/API    │
│ (public/)   │ ◀─────── │   (server.js)     │
└─────────────┘           └─────────┬────────┘
                                    │ 读/写
                          ┌─────────▼────────┐      ┌──────────────┐
                          │  SQLite  app.db   │◀────│ Python Agent  │
                          │ (papers/notes/…)  │ 写入 │ (agent/)      │
                          └──────────────────┘      └──────┬───────┘
                                                           │ 调用
                                       ┌───────────────────▼──────────────┐
                                       │ 数据源(arXiv/CVF/…) + 大模型 API   │
                                       └──────────────────────────────────┘
```

- **前端 Web**：看板/列表/PDF阅读/笔记（现有页面，先沿用）
- **Node Web/API**：提供 REST 接口、服务 PDF、读写笔记与进度（从 SQLite 读，不再读 papers.json）
- **SQLite**：唯一数据中枢；Node 与 Python **共享同一个 `data/app.db`**（WAL 模式支持并发读写）
- **Python Agent**：采集流水线，把论文写进 SQLite
- **数据源 + 大模型**：外部依赖

## 2. 目录结构（目标）

```
study-app/
├─ public/              # 前端（现有）
├─ server.js            # Node Web/API（P1 改为读 SQLite）
├─ data/                # ★ gitignore：app.db + pdfs/ 缓存
│  └─ pdfs/
├─ agent/               # ★ Python 采集 Agent
│  ├─ __main__.py       # CLI: python -m agent ingest --query "..." --venue arxiv
│  ├─ pipeline.py       # 发现→去重→下载→提取→LLM→入库
│  ├─ db.py             # SQLite 读写（与 Node 同一文件/同一 schema）
│  ├─ extract.py        # PDF→文本（PyMuPDF）
│  ├─ llm.py            # 多供应商统一封装（OpenAI兼容 + Anthropic）
│  ├─ schema.py         # 属性 schema（pydantic）
│  └─ sources/          # 各 venue 适配器
│     ├─ arxiv.py  cvf.py  openreview.py  acl.py  pmlr.py  neurips.py  aaai.py
├─ db/schema.sql        # ★ 建表 DDL（Node 与 Python 共用）
├─ docs/                # 文档（本目录）
├─ .env.example         # 环境变量模板（真实 .env 不入库）
└─ requirements.txt / package.json
```

## 3. 采集流水线（Agent 核心）

一篇论文从发现到入库的 8 步（确定性步骤 + LLM 理解步骤）：

1. **发现** `sources/<venue>.search(query, year)` → 候选列表（标题/作者/venue/年/pdf_url/abstract）
2. **去重** 按 `arxiv_id / doi / 标准化标题` 查 DB，已存在则跳过
3. **下载 PDF** → 缓存到 `data/pdfs/`（限速、重试）
4. **提取文本** PyMuPDF：标题+摘要+前 N 页（控制 token）
5. **LLM 抽属性**（结构化 JSON，见 §4）
6. **相关性打分**（可选）LLM 判断与目标方向相关度 0–1，过滤噪音
7. **自动讲解**（可选）LLM 生成"科学方法论讲解" markdown
8. **入库** 写入 SQLite `papers`

> 设计哲学：**先确定性、后智能**。爬取用规则（稳定、便宜），LLM 只负责"理解/分类/写作"。等成熟再让 LLM 更自主。

## 4. 论文属性 Schema（LLM 结构化输出）

```jsonc
{
  "title": "...", "authors": ["..."], "venue": "CVPR", "year": "2024",
  "arxiv_id": "2310.14566", "abstract": "...",
  "type": "检测 | 缓解·解码 | 缓解·训练 | 机制 | 评测 | 定义 | ...",
  "topic": "知识-视觉冲突 | 多图 | 通用物体 | ...",
  "task": "...", "models": ["LLaVA-1.5"], "datasets": ["POPE"],
  "contribution": "一句话核心贡献",
  "tldr": "三句话速览",
  "tags": ["contrastive decoding", "language prior"],
  "relevance": 0.92
}
```

用 **pydantic** 定义并校验；LLM 输出不合规则自动重试。

## 5. 数据库 Schema（`db/schema.sql`）

- `papers`：上面所有字段 + `source/source_id/pdf_url/url/file(本地pdf)/explainer(md)/created_at/updated_at`
- `progress`：`paper_id, status, updated_at`（单用户；将来加 `user_id`）
- `notes`：`paper_id, content, updated_at`
- `ingest_jobs`：`id, query, venues, status, log, created_at`（P5 后台任务用）

> 现有 `papers.json / notes/*.md / progress.json` 在 **P1 迁移**进库（脚本一次性导入，原文件留作种子）。

## 6. 大模型多供应商封装（`agent/llm.py`）

- **OpenAI 兼容**（DeepSeek / Qwen / OpenAI / Moonshot…）：同一套 `openai` SDK，仅切换 `base_url + model + key`
- **Anthropic（Claude）**：单独适配器
- 统一接口：`llm.extract(text, schema) -> dict`，内部按 `LLM_PROVIDER` 路由
- 结构化输出：JSON mode / function calling；失败重试 + pydantic 校验

环境变量（`.env`，不入库）：
```
LLM_PROVIDER=deepseek          # deepseek|qwen|openai|anthropic
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

## 7. 各数据源接入方式

| 源 | 覆盖会议 | 接入方式 | 难度 |
|---|---|---|---|
| **arXiv** | 几乎所有预印 | 官方 API（Atom）| ⭐ 低（先做） |
| **CVF** | CVPR/ICCV/ECCV/WACV | openaccess.thecvf.com 列表页解析 | ⭐⭐ |
| **OpenReview** | ICLR / 部分 NeurIPS | 官方 API | ⭐⭐ |
| **ACL Anthology** | ACL/EMNLP/NAACL/EACL | 官方批量数据/API | ⭐⭐ |
| **PMLR** | ICML/AISTATS | proceedings.mlr.press 解析 | ⭐⭐ |
| **NeurIPS** | NeurIPS | proceedings.neurips.cc | ⭐⭐ |
| **AAAI** | AAAI | ojs.aaai.org 解析 | ⭐⭐⭐ |

## 8. 合规与礼仪（上线前必读）

- **元数据 + 原文链接**：自由使用。
- **PDF 全文**：多为开放获取，**个人研究缓存**没问题；**公开多用户部署再对外提供 PDF** 可能涉版权 → 建议线上只存元数据 + 跳转原站，PDF 仅私有缓存。
- **抓取礼仪**：遵守 robots.txt、限速、优先官方 API；arXiv 大批量走其 OAI-PMH / 数据集，别猛刷。
