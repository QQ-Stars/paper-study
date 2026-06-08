# 采集 Agent 详细设计 · Paper-Study

> Python 实现的"论文采集 + 大模型理解"流水线。把一个方向关键词变成一批**已下载、已分类、已写好讲解**的论文，写进 `data/app.db`。
> 数据库见 [DATABASE.md](./DATABASE.md)。

---

## 0. 给小白：这个"Agent"到底是什么？

它**不是**一个会自己乱跑的机器人，而是一条**自动化流水线**：
- **确定性步骤**（爬取、下载、提取文本）用普通代码完成——稳定、便宜、好排错；
- **需要"理解"的步骤**（这篇是什么方向？核心贡献是啥？）交给**大模型**完成。

> 等流水线跑顺了，再逐步加"智能/自主"（让模型自己决定抓哪些、怎么判重）。**先简单后智能**。

---

## 1. 目录结构

```
agent/
├─ __main__.py      # CLI 入口: python -m agent ingest --query "..." ...
├─ config.py        # 读取 .env；各供应商默认 base_url/model
├─ db.py            # SQLite 读写(见 DATABASE.md §7)
├─ models.py        # pydantic 数据模型: PaperStub / PaperAttributes
├─ extract.py       # PDF -> 文本 (PyMuPDF)
├─ llm.py           # 多供应商统一封装(OpenAI兼容 + Anthropic) + 结构化输出
├─ pipeline.py      # 编排: 发现→去重→下载→提取→LLM→(相关性/讲解)→入库
├─ relevance.py     # 可选: LLM 相关性打分
├─ explainer.py     # 可选: LLM 生成讲解 markdown
├─ embed.py         # 可选: 论文向量(SPECTER2/自算) + 相似检索
├─ util.py          # slug / title_norm / 带限速重试的 http
└─ sources/         # 聚合 API 客户端（不是爬虫）
   ├─ base.py            # Source 抽象基类
   ├─ semanticscholar.py # ← P2 主力: 发现+元数据+TLDR+领域+PDF链接
   ├─ openalex.py        # ← P4 副力: 交叉校验+四级主题
   └─ arxiv.py           # ← P2 兜底: 最新预印本
```

---

## 2. 数据模型（`models.py`，用 pydantic 校验）

```python
from pydantic import BaseModel, Field
from typing import Optional

class PaperStub(BaseModel):           # 数据源给出的"半成品"(LLM 之前)
    source: str                       # arxiv|cvf|...
    source_id: str
    title: str
    authors: list[str] = []
    venue: Optional[str] = None       # 能确定就填(如 "Accepted to CVPR 2024")
    year: Optional[str] = None
    abstract: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None

TYPES  = ["检测","缓解·解码","缓解·训练","机制","评测","定义","其他"]
TOPICS = ["知识-视觉冲突","多图","多物体","通用物体","语言先验","其他"]

class PaperAttributes(BaseModel):     # 大模型抽取的"成品"
    type: str = Field(description=f"研究方向，从{TYPES}里选最贴切的")
    topic: str = Field(description=f"主题，从{TOPICS}里选；都不符可自拟简短词")
    task: Optional[str] = None
    models: list[str] = []
    datasets: list[str] = []
    contribution: str                 # 一句话核心贡献
    tldr: str                         # 三句话速览
    tags: list[str] = []
    relevance: Optional[float] = None # 0~1，与本次 query 的相关度
```

> **受控词表**（TYPES/TOPICS）来自我们手工精读 38 篇的经验，让自动分类与现有体系一致；模型选不出再自拟。

---

## 3. 流水线（`pipeline.py`）

一次 `ingest` 的时序：

```
source.search(query, years, limit)  →  [PaperStub(含 摘要/TLDR/领域/引用/PDF链接), ...]
        │
        ▼  对每个 stub:
   ┌─ 去重: exists(arxiv_id or title_norm)? ──是──▶ 跳过(skipped++)
   │否
   ▼
   llm.classify(stub)  →  PaperAttributes(type/topic/...)   # 用 摘要+TLDR 即可, 多数不必下PDF
   ▼
   (可选) 若 query 且 relevance < 阈值 → 标记/跳过
   ▼
   db.insert_paper(slug, stub, attrs)   (added++)
   ▼
   (可选, --explain 时) 下载开放PDF → extract.first_pages → explainer.generate → 入库
   ▼
   (可选) embed.add(slug)   # 向量入库, 供相似/语义检索
```

伪代码：
```python
def ingest(query, sources, years, limit, min_rel=0.0, explain=False):
    con = db.connect()
    for src in [get_source(s) for s in sources]:   # semanticscholar / openalex / arxiv
        for stub in src.search(query, years, limit):   # stub 已含 摘要/TLDR/领域/引用/pdf_url
            tn = util.title_norm(stub.title)
            if db.exists(con, arxiv_id=stub.arxiv_id, title_norm=tn):
                continue
            try:
                attrs = llm.classify(stub)              # 用 摘要+TLDR 分类, 多数不必下PDF; 带重试+校验
                if query and (attrs.relevance or 1) < min_rel:
                    continue
                db.insert_paper(con, build_row(stub, attrs, tn))
                if explain:                             # 需要正文时才下开放PDF
                    pdf = util.download(stub.pdf_url, dest=pdf_path_for(stub))
                    text = extract.first_pages(pdf, 8, with_abstract=stub.abstract)
                    db.set_explainer(con, stub.slug, explainer.generate(stub, text))
                # embed.add(con, stub)                  # 可选: 向量入库
            except Exception as e:
                log.warning("skip %s: %s", stub.title[:40], e)   # 单篇失败不影响整体
    con.close()
```
> **幂等**：重复跑同一 query 安全（去重跳过）。**可恢复**：单篇异常被捕获、记录、继续。

---

## 4. 多供应商大模型封装（`llm.py`）— 关键

### 4.1 配置（`.env`，只填 2 项即可）
```
LLM_PROVIDER=deepseek          # deepseek|qwen|openai|anthropic
LLM_API_KEY=sk-xxx
# 下面两项不填则用 config.py 里的默认值
# LLM_BASE_URL=...
# LLM_MODEL=...
```
`config.py` 预置默认：
```python
PRESETS = {
  "deepseek":  ("https://api.deepseek.com",                         "deepseek-chat"),
  "qwen":      ("https://dashscope.aliyuncs.com/compatible-mode/v1","qwen-plus"),
  "openai":    ("https://api.openai.com/v1",                        "gpt-4o-mini"),
  "anthropic": (None,                                               "claude-3-5-sonnet-latest"),
}
```

### 4.2 统一接口
```python
def extract(stub, text) -> PaperAttributes:
    sys = SYSTEM_PROMPT                       # 含受控词表与输出要求
    user = build_user_prompt(stub, text)      # 标题/摘要/正文片段 + query
    data = chat_json(sys, user, schema=PaperAttributes)   # 返回已校验 dict
    return PaperAttributes(**data)
```
- **DeepSeek / Qwen / OpenAI**：都兼容 OpenAI 协议 → 用 `openai` SDK，仅换 `base_url+model+key`；结构化输出用 `response_format={"type":"json_object"}`（或工具调用）。
- **Anthropic(Claude)**：用 `anthropic` SDK；用 tool/JSON 指令拿结构化输出。
- **校验+重试**：`chat_json` 解析 JSON → `pydantic` 校验；失败则把"你的JSON不合法，请修正"回灌，最多重试 2 次（`tenacity`）。

骨架：
```python
def chat_json(system, user, schema, retries=2):
    for i in range(retries+1):
        raw = _call_provider(system, user)        # 按 LLM_PROVIDER 路由
        try:
            return schema.model_validate_json(_extract_json(raw)).model_dump()
        except ValidationError as e:
            user = f"{user}\n\n上次输出无效:{e}\n请只输出严格符合schema的JSON。"
    raise RuntimeError("LLM 结构化输出多次失败")
```

### 4.3 Prompt 设计（要点）
- **System**：你是论文分析助手；给定论文文本，按受控词表分类；**只输出 JSON**，字段见 schema；分不准就给最接近项并降低 confidence。
- **User**：`query`(本次方向) + 标题 + 摘要 + 正文前几页片段。
- 加 1~2 个 **few-shot 例子**（用我们已写好的论文当样例）提升一致性。

---

## 5. PDF 文本提取（`extract.py`）

```python
import fitz   # PyMuPDF
def first_pages(path, n=8, with_abstract=None):
    doc = fitz.open(path)
    text = "\n".join(doc[i].get_text() for i in range(min(n, len(doc))))
    if with_abstract: text = f"摘要:{with_abstract}\n\n{text}"
    return text[:24000]      # 约 8k token，控制成本
```
- 加密/乱码 PDF：PyMuPDF 多数仍能取文；失败则**退化为只用摘要**。
- 只取前几页（含标题/摘要/引言/方法开头）即可满足分类与速览，省 token。

---

## 6. 数据源适配器（`sources/`）

统一接口：
```python
# base.py
class Source(ABC):
    name: str
    @abstractmethod
    def search(self, query: str, years: tuple[int,int], limit: int) -> Iterable[PaperStub]: ...
```

### 6.1 Semantic Scholar（主力，P2）
- 批量搜索：`GET https://api.semanticscholar.org/graph/v1/paper/search/bulk`
  `?query=<q>&year=2024-2026&fields=title,authors,venue,year,abstract,tldr,s2FieldsOfStudy,citationCount,externalIds,openAccessPdf`
- 每页最多 **1000 条**；每条直接给 标题/作者/venue/年/摘要/**TLDR**/领域/引用数/arxivId/**开放PDF链接**。
- 速率：未鉴权 5000 次/5 分钟；有 key 1 RPS；需指数退避。
- 实现：`httpx` 直接调，或 `semanticscholar` 包。

### 6.2 OpenAlex（副力，P4：交叉校验+主题）
- `GET https://api.openalex.org/works?search=<q>&filter=from_publication_date:2024-01-01&per-page=200`（建议带 `mailto=` 进礼貌池）
- 给 摘要(倒排索引需还原)、**四级主题(置信度)**、引用、开放获取状态。
- 用途：补全 S2 缺的字段、用其主题层级**校正分类**。

### 6.3 arXiv（时效兜底，P2）
- `http://export.arxiv.org/api/query?search_query=all:<q>&sortBy=submittedDate&sortOrder=descending`（`feedparser` 解析 Atom）
- 抓"刚出、聚合平台还没收录"的最新预印本；venue 从 `comment` 里识别 "Accepted to CVPR 2024"，否则 "arXiv"。
- 限速：**每 3 秒 ≤1 次**。

### 6.4 兜底（很少用到）
- 极少数**只在某会议官网、且 S2/OpenAlex 都查不到**的论文，才单独写一次性抓取（`httpx`+`bs4`）。

> **成本/效率关键**：S2/OpenAlex 已免费给 摘要/TLDR/领域 → **分类多数只用摘要+TLDR 就够，不必下 PDF**；只有要"写讲解"时才下载开放 PDF 取正文。

---

## 7. 去重 / 限速 / 错误处理 / 幂等（`util.py`）

- **去重**：`title_norm()` + DB 唯一约束(arxiv_id/doi)；跨源补全不新建。
- **限速**：每个源一个最小请求间隔（如 arXiv 3s、爬站 1~2s）+ 随机抖动；统一 `User-Agent`。
- **重试**：`tenacity` 指数退避（网络/5xx）。
- **缓存**：抓过的列表页/PDF 落盘，重跑不重抓。
- **幂等**：`INSERT OR IGNORE` + 去重，重复运行安全。

---

## 8. 命令行（`__main__.py`）

```bash
# 抓某方向的论文(先 arXiv，后续可多源)
python -m agent ingest --query "multimodal hallucination" \
       --venues arxiv --years 2024-2026 --max 30 --min-relevance 0.5 --explain

python -m agent reextract --id <slug>     # 用已存文本重抽属性(换了模型/改了prompt)
python -m agent explain   --id <slug>     # 仅生成讲解
python -m agent stats                     # 打印库里统计
```

---

## 9. 运行环境与依赖（**只装在项目内**）

```bash
# 在 study-app 目录
python -m venv .venv               # 项目内虚拟环境(已 gitignore)
.\.venv\Scripts\activate           # Windows
pip install -r requirements.txt
```
`requirements.txt`：
```
httpx              # 调 Semantic Scholar / OpenAlex API、下载开放PDF
feedparser         # arXiv Atom 解析
openai             # DeepSeek/Qwen/OpenAI 兼容
anthropic          # Claude
pydantic>=2        # 结构化校验
python-dotenv      # 读 .env
tenacity           # 重试/指数退避
pymupdf            # PDF 提取（仅在要写讲解/取正文时）
# 可选:
# beautifulsoup4 lxml   # 仅当需要兜底抓某会议官网
# numpy faiss-cpu       # 可选: 语义检索向量（或用 sqlite-vss）
```
> Node 依赖（`better-sqlite3`）装到 `node_modules`。两者都在项目内，不污染系统/其他目录。

---

## 10. 成本与质量

- **成本**：S2/OpenAlex 已免费给 摘要+TLDR+领域 → 分类**只喂 标题+摘要+TLDR** 给**便宜模型**、多数**无需下载/读取 PDF** → 约 **¥0.005~0.03/篇**，更省；只有"写讲解"按需下 PDF、贵一些。
- **质量自检**：首批抽样人工校对 `type/topic/contribution`；不准就调 prompt / 加 few-shot / 换模型，再 `reextract`（不用重抓 PDF）。

---

## 11. 与 Node 的协作（现在 & P5）

- **现在(本地)**：你手动跑 `python -m agent ingest ...`，论文进库，刷新网页就能看到。Node 只管读库。
- **P5(网页触发)**：网页 `POST /api/ingest` 往 `ingest_jobs` 插一条任务；Python worker 轮询该表 → 执行流水线 → 回写进度；网页 `GET /api/jobs` 看进度。**两端通过数据库解耦，互不直接调用**，简单可靠。

---

## 12. 语义搜索 / 相似论文（可选，体验升级）

固定标签（type/topic）适合"分类浏览"，但"按方向找论文/找相似"用**向量检索**更聪明：

- **算向量**：用 Semantic Scholar 提供的 **SPECTER2** 论文向量，或自己用 embedding 模型算"标题+摘要"向量。
- **存**：写入 `paper_vectors` 表（见 DATABASE.md）。
- **检索**：`faiss` / `sqlite-vss` 做近邻搜索 → "找和这篇相似的"、"按语义找某方向"。
- 建议放在**功能稳定后**做（ROADMAP P4）。

