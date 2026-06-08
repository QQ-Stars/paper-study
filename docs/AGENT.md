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
├─ util.py          # slug / title_norm / 带限速重试的 http
└─ sources/
   ├─ base.py       # Source 抽象基类
   ├─ arxiv.py      # ← P2 先实现这个
   ├─ cvf.py  openreview.py  acl.py  pmlr.py  neurips.py  aaai.py   # ← P4
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
source.search(query, years, limit)   →  [PaperStub, ...]
        │
        ▼  对每个 stub:
   ┌─ 去重: exists(arxiv_id or title_norm)? ──是──▶ 跳过(skipped++)
   │否
   ▼
   下载 PDF(限速/缓存)  →  data/pdfs/<slug>.pdf
   ▼
   extract.first_pages(pdf, n=8) + abstract   →  text(截断到~8k token)
   ▼
   llm.extract(stub, text)  →  PaperAttributes(已 pydantic 校验)
   ▼
   (可选) 若 query 且 relevance < 阈值 → 标记/跳过
   ▼
   db.insert_paper(slug, stub, attrs, pdf_path)   (added++)
   ▼
   (可选) explainer = explainer.generate(stub, text); db.set_explainer(slug, ...)
```

伪代码：
```python
def ingest(query, venues, years, limit, min_rel=0.0, explain=False):
    con = db.connect()
    for src in [get_source(v) for v in venues]:
        for stub in src.search(query, years, limit):
            tn = util.title_norm(stub.title)
            if db.exists(con, arxiv_id=stub.arxiv_id, title_norm=tn):
                continue
            try:
                pdf = util.download(stub.pdf_url, dest=pdf_path_for(stub))
                text = extract.first_pages(pdf, 8, with_abstract=stub.abstract)
                attrs = llm.extract(stub, text)             # 带重试+校验
                if query and (attrs.relevance or 1) < min_rel:
                    continue
                db.insert_paper(con, build_row(stub, attrs, pdf, tn))
                if explain:
                    db.set_explainer(con, slug, explainer.generate(stub, text))
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

### 6.1 arXiv（P2 先做，最简单）
- 接口：`http://export.arxiv.org/api/query?search_query=all:<query>&start=0&max_results=N&sortBy=submittedDate&sortOrder=descending`
- 解析：`feedparser` 读 Atom；每条取 id→arxiv_id、title、authors、summary→abstract、`pdf` 链接、published→year。
- **venue 识别**：`arxiv_comment` 里常有 "Accepted to CVPR 2024" → 正则提取 venue/year，否则 venue="arXiv"。
- 限速：**每 3 秒 ≤1 次请求**（arXiv 要求）。

### 6.2 其它顶会（P4，逐个接）
| 源 | 会议 | 取数方式 | 备注 |
|---|---|---|---|
| `cvf.py` | CVPR/ICCV/ECCV | 解析 `openaccess.thecvf.com/<CONF><YEAR>?day=all` 列表 | 无API→礼貌爬+缓存；按 query 过滤标题 |
| `openreview.py` | ICLR/部分NeurIPS | OpenReview API v2 (`api2.openreview.net`) 查 venue 下 notes | 结构化、稳定 |
| `acl.py` | ACL/EMNLP/NAACL/EACL | ACL Anthology 批量数据(GitHub) 或卷页 | 有 bibtex/元数据 |
| `pmlr.py` | ICML | 解析 `proceedings.mlr.press/v<NNN>/` | 每年对应卷号 |
| `neurips.py` | NeurIPS | 解析 `proceedings.neurips.cc/paper_files/paper/<year>` | |
| `aaai.py` | AAAI | 解析 `ojs.aaai.org` 期号页 | 结构最杂 |

> **成本控制关键**：顶会一年几千篇。**先用关键词在标题/摘要里粗筛**，只对命中的少量论文调用 LLM；再用 `relevance` 精筛。别对全量调用大模型。

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
pymupdf            # PDF 提取
openai             # DeepSeek/Qwen/OpenAI 兼容
anthropic          # Claude
pydantic>=2        # 结构化校验
httpx              # HTTP
feedparser         # arXiv Atom
beautifulsoup4     # 解析顶会页面
lxml
python-dotenv      # 读 .env
tenacity           # 重试
```
> Node 依赖（`better-sqlite3`）装到 `node_modules`。两者都在项目内，不污染系统/其他目录。

---

## 10. 成本与质量

- **成本**：仅对**粗筛命中**的论文、用**标题+摘要+前几页**调用**便宜模型** → 约 **¥0.01~0.07/篇**；写讲解更贵些（输出长），按需开启。
- **质量自检**：首批抽样人工校对 `type/topic/contribution`；不准就调 prompt / 加 few-shot / 换模型，再 `reextract`（不用重抓 PDF）。

---

## 11. 与 Node 的协作（现在 & P5）

- **现在(本地)**：你手动跑 `python -m agent ingest ...`，论文进库，刷新网页就能看到。Node 只管读库。
- **P5(网页触发)**：网页 `POST /api/ingest` 往 `ingest_jobs` 插一条任务；Python worker 轮询该表 → 执行流水线 → 回写进度；网页 `GET /api/jobs` 看进度。**两端通过数据库解耦，互不直接调用**，简单可靠。
