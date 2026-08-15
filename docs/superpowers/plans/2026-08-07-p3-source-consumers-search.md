# P3 SourceDocument 消费者、确定性分块与 Chunk Search 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development for every behavior and verification-before-completion before any completion claim. Execute every checkbox in order. This plan contains no Git commit, push, branch, staging, or automatic Live migration step.

**Goal:** 让 explainer、translation、classification、metadata、summary、lexical search、semantic search 和 embedding 全部只消费 ready `document_sources`；用一个按 consumer policy 明确选择且不会静默丢失 eligible 后半文档的 `ContextBuilder` 建立确定性 `document_chunks`，交付 FTS5 external-content 检索、chunk embedding、翻译断点续跑、显式 stale 传播和可定位到 heading/page 的 search result。

**Architecture:** P1 五主表仍是 domain source of truth，P2 ProcessingQueue 仍是唯一 job 状态机。P3 `ContextBuilder` 深模块拥有 normalization coverage、chunk identity、batch planning 和 budget invariants；artifact handlers 只接收 ContextPlan，绝不再读 PDF 或 prefix slice。`DocumentSearch` 把 FTS5 与 embedding repositories 隐藏在一个 read-only query seam 后，hybrid ranking 使用 deterministic reciprocal-rank fusion。Source/provider/model/options/processing version 变化由 `SourceFreshnessService` 在显式写事务中 cascade stale；query path 永不 materialize chunks、下载模型、补 embedding 或写数据库。

**Tech Stack:** Python 3、FastAPI/Pydantic、SQLAlchemy 2 async/aiosqlite、Alembic final revision `20260807_03`、SQLite FTS5 external-content、NumPy float32、现有 model2vec/OpenAI-compatible embedding settings、Python `unittest.IsolatedAsyncioTestCase`、React 19/TypeScript/Vitest。

**Depends on:** P0 verified backup/restore-check；P1 five-table revision `20260807_01`；P2 revision `20260807_02`、ProcessingQueue、job events、artifact heads、source strict-mode semantics、worker/CLI 和 `/api/v2` app factory。P2 全量与恢复副本演练未满足门禁时不得开始 P3 DDL。完整 frontend suite 若为已审核 non-zero，必须在 P3 入口与出口由 P0.1 exact verifier 重新证明 IDs/signatures/related hashes 未变、相关路径未触碰，并继续报告 raw non-zero 与 `overallGreen=false`。

**Workspace constraints:** 不修改 `public/` 或 React JSX/CSS/文案/布局；不读写 Live `data/app.db` 作为 test fixture；不在 query/request thread 自动创建 chunks、重嵌、修复 stale 或运行 LLM；不把旧 `papers.abstract/tldr` 当 ready SourceDocument；不回填无法证明 source provenance 的历史 artifact；不静默 fallback 到另一 embedding provider/model；不在测试中下载 model2vec 或调用外部 embeddings/chat API。

---

## 不可协商的消费者来源契约

| Consumer | Queue job_type | 唯一正文来源 | 新 artifact/index | Legacy projection | 明确禁止 |
|---|---|---|---|---|---|
| explainer | `explain` | 摘要/引言/方法/实验/讨论/结论的 eligible chunks | `kind='explainer'` Markdown | `papers.explainer` | 全文 prefix、参考文献/致谢、遗漏后部 eligible section |
| translation | `translate` | ready source 的完整 ordered chunks | `generated_artifacts.kind='translation'` + checkpoints | `translations.content` | 打开 PDF、摘要 fallback、只翻译前缀 |
| classification | `explain` | 首页、摘要、方法概述、结论的 bounded ContextPlan | `kind='classification'` canonical JSON | `papers.type/topic/task/models/datasets/tags/relevance` | 发送整篇、发送中间无关章节、无 schema 自由文本 |
| metadata | `explain` | 首页/题录区域 bounded ContextPlan | `kind='metadata'` canonical JSON | allowlist paper metadata CAS | 从文件名猜 metadata、发送正文、覆盖更可信 identifier |
| summary | `explain` | 默认排除参考文献/致谢的 eligible body map/reduce | `kind='summary'` canonical JSON | `papers.tldr/contribution` | 只读首段、直接使用 legacy abstract |
| lexical search | 无 query job | ready source 的 ready chunks | FTS5 external-content index | 无 | LIKE 扫描正文、query-time chunk build |
| semantic/hybrid search | 无 query job | ready source 的 ready chunk embeddings | `document_chunk_embeddings` | P6 前保留 `paper_vectors` | query-time document re-embed、stale vector 命中 |
| embedding build | `embed` | ready source 的 ready chunks | chunk vectors + coverage metadata | 不改 `paper_vectors` | 直接 embed title/abstract 代替正文 |

每个 artifact/index enqueue 必须显式带 `sourceDocumentId`，并验证 source 属于 URL 中的 paper、status=ready、content SHA 非空、当前 PDF SHA 未变化。Native source 也必须有非空 provider/model，固定为 `local/pymupdf4llm-pymupdf`；OCR 使用 source row 已记录的真实 provider/model。任何 consumer failure 不得回到 PDF、legacy abstract、另一 source mode 或另一 Provider。

---

## 公共 Interface

### ContextBuilder

~~~python
class ContextBuilder(Protocol):
    async def materialize_chunks(
        self,
        source_document_id: str,
        spec: ChunkingSpec,
        *,
        now: datetime,
    ) -> ChunkSet:
        raise NotImplementedError

    async def build(
        self,
        source_document_id: str,
        request: ContextRequest,
    ) -> ContextPlan:
        raise NotImplementedError
~~~

`materialize_chunks` 只能由 worker/application command 调用；它可写 chunk rows。`build` 严格只读：source/chunks 缺失、stale、版本不匹配时抛 typed error，不自行补建。`ContextPlan` 包含 source/content/chunking identity、ordered batches、每个 batch 的 chunk IDs/sequence/heading/page/token count，以及 totalChunks/totalTokens/coveredContentSha256。

### SourceFreshnessService

~~~python
class SourceFreshnessService(Protocol):
    async def reconcile_pdf(
        self,
        paper_id: str,
        current_pdf_sha256: str,
        *,
        now: datetime,
    ) -> StaleResult:
        raise NotImplementedError

    async def activate_source(
        self,
        source_document_id: str,
        *,
        now: datetime,
    ) -> StaleResult:
        raise NotImplementedError
~~~

PDF SHA 改变会 stale 该 paper 所有旧 ready sources。新 source ready 时，只 stale 同 paper、同 mode、但 provider/model/options_hash/processing_version 不同的旧 ready sources；native 与 OCR 是显式可并存的替代来源，不因 mode 不同互相 stale。Cascade 在一个事务内标记 dependent artifacts/chunks/embeddings，移除指向 stale artifact 的 head，取消 queued jobs、请求 running jobs cooperative cancel，并保留 legacy projections供 runtime rollback。

### DocumentArtifactService

~~~python
class DocumentArtifactService(Protocol):
    async def enqueue(
        self,
        paper_id: str,
        source_document_id: str,
        source_mode: Literal["native", "ocr"],
        kind: Literal["explainer", "translation", "classification", "metadata", "summary"],
        *,
        now: datetime,
    ) -> EnqueueResult:
        raise NotImplementedError

    async def run(self, lease: JobLease, artifact_id: str) -> GeneratedArtifact:
        raise NotImplementedError
~~~

Artifact identity 必须调用 P2 唯一的 `artifact_identity_key(kind,source_document_id,source_content_sha256,provider,model,prompt_version,options_hash)` 与 `artifact_job_key` builder。Translation 的 options hash 包含目标语言、chunking/context/prompt schema version；classification、metadata、summary 分别包含其冻结 ContextBuilder/output-schema options。translation 使用 job_type `translate`；其余四种使用 `explain`，由 artifact.kind 分派 handler。P3 head 下 P2 explainer handler必须迁到 `ContextBuilder explain` policy，不再把整篇 markdown 或固定 prefix直接传给 generator。

P1 已冻结统一 `ArtifactKind` enum 为 `explainer|translation|summary|outline|study_card|classification|metadata`；P3 只实现其中 translation/classification/metadata/summary，不扩展或复制 enum。Database kind仍是P1 nonblank TEXT且无枚举CHECK，因此revision不重建generated_artifacts。

### EmbeddingProvider 与 DocumentSearch

~~~python
class EmbeddingProvider(Protocol):
    provider_id: str

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        raise NotImplementedError


class DocumentSearch(Protocol):
    async def index(
        self,
        lease: JobLease,
        source_document_id: str,
        profile: EmbeddingProfile,
    ) -> IndexResult:
        raise NotImplementedError

    async def search(self, request: SearchRequest) -> SearchResultPage:
        raise NotImplementedError

    async def status(self, source_document_id: str) -> IndexStatus:
        raise NotImplementedError
~~~

Embedding batch 返回 provider/model/embedding_version/dimensions 和与 input 顺序等长的 finite vectors。Search request mode 只允许 `lexical|semantic|hybrid`；semantic/hybrid 可以为 query 生成一个临时 query vector，但不得创建、更新或删除任何 document embedding。缺失/stale document embeddings 只进入 coverage metadata，不触发修复。

---

## 确定性 chunk 与 ContextPlan 契约

固定 `chunking_version = "markdown-coverage-v1"`，固定 `tokenizer_version = "unicode-word-v1"`，普通text chunk target/hard cap均为1600 tokens、零overlap。完整fenced code block与display-math block（`$$formula$$`、`\[formula\]`）是atomic `verbatim`；pipe-table row与含inline math的paragraph是 `structured`，boundary不得落在escaped delimiter或math span内。1601–8192 token atomic unit可超过普通cap，超过8192则整次materialization以 `CHUNK_ATOMIC_BLOCK_TOO_LARGE` 失败且零rows。算法顺序不可交换：

1. 输入必须是 P2 已规范化、SHA 已验证的 ready source markdown；P3 不二次改写字符。
2. Stateful scanner识别ATX headings、paragraphs、fenced code、pipe tables、inline/display math、backslash-escaped delimiters和page markers；heading原文仍在content。
3. 对plain text从char_start选择≤1600的最远semantic boundary，fallback sentence再fallback token end。Fenced/display-math从opening到matching closing整体为 `verbatim`；table row/inline-math paragraph整体为 `structured`；未闭合fence/math到EOF作为atomic并记录warning，不合成delimiter。
4. 每个 chunk 是原 markdown 的连续 `[char_start,char_end)` slice；sequence 从 0 开始，按 offset 递增；任何字符恰好属于一个 chunk。
5. page_start/page_end 只来自 extractor/OCR 已证明的 1-based page marker；无法证明时均为 NULL，不猜页码。
6. token_count 使用 `unicode-word-v1`：连续 Unicode letter/number 为一个 token，单个非空白 symbol 为一个 token；不依赖 Provider tokenizer。
7. `heading_path` 使用 UTF-8、NFC、无多余空白的 canonical JSON string array。chunk ID 与 key：

~~~text
chunk_key = sha256("chunk:v1\0" + source_document_id + "\0" + source_content_sha256 + "\0" + chunking_version + "\0" + sequence + "\0" + char_start + "\0" + char_end + "\0" + content_sha256)
chunk_id = "chunk_" + first_32_hex_characters(chunk_key)
~~~

ChunkSet 必须满足：

- sequences/offsets 连续且无 gap/overlap；
- `"".join(chunk.content ORDER BY sequence)` 与 source markdown byte-for-byte 相同；
- 每个 content SHA、source content SHA、token count 和 page range 可重算；
- 相同 source/spec 在不同进程产生相同 rows；
- 同 source 另一 chunking_version 先生成新 rows/验证 coverage，再原子 stale 旧 version，不就地改 ready row。
- Translation对plain text调用Provider；verbatim零Provider调用并原样checkpoint；structured先把pipe layout、inline/display formula、escaped delimiters替换为deterministic placeholders，翻译prose后逐一恢复并验证token multiset/ordering。最终fences、table pipe counts/alignment rows、math与escaped delimiters byte-for-byte不变，否则 `MARKDOWN_STRUCTURE_INVALID`。

ContextBuilder policy 固定如下：

- `translation`：每 batch 恰好一个 chunk，eligible set 是全部 0..N-1。
- `embedding`：eligible set 是全部 ready chunks，不做 LLM batch selection。
- `summary`：eligible set 是除 heading path 命中 References/Bibliography/Acknowledgements/参考文献/致谢后的全部 body chunks；每个 eligible chunk恰好进入一个 map batch，recursive reduce保存 child ranges。
- `explain`：eligible headings按 Abstract/Summary、Introduction/Background、Method/Approach、Experiment/Evaluation/Results、Discussion、Conclusion priority识别，默认排除 references/acknowledgements。每个 eligible section无论位于文档前后都必须出现；过长 section先覆盖其全部 eligible chunks生成 section summary，再由全部 section summaries生成最终讲解。
- `classification`：总 budget 3200 tokens；按顺序选择首页/首 chunk最多600、Abstract最多800、Methods overview最多1200、Conclusion最多800。扫描整篇定位后部 Conclusion，但不发送中间 experiments/references等无关 chunks；每类超 budget deterministic截断并在plan记录selected/omitted reason。
- `metadata`：总 budget 1600 tokens；只选 page_start=1 的题录 chunks，缺 page metadata时只选 sequence 0；不加入 Methods/Experiments/References正文。

每个 plan 同时记录 allChunkIds、eligibleChunkIds、selectedChunkIds、excluded ranges/reasons 和 coverage hash。Translation/embedding要求 all=eligible=selected；summary/explain要求 eligible=selected（长 section以有完整child provenance的summary node代表）；classification/metadata只要求selected符合bounded policy。任何 policy 都不得用prefix slice冒充heading selection，也不得把 eligible后部section静默丢弃。

---

## P3 SQLite Schema 契约

~~~python
revision = "20260807_03"
down_revision = "20260807_02"
~~~

这是后续 P4–P6 共用的最终 schema head。Migration 开始时必须验证 current head=`20260807_02`、P1 五主表、P2 queue columns、`paper_artifact_heads`、`processing_job_events`、`ocr_page_checkpoints` 全部存在；失败为 `P3_BASE_SCHEMA_MISSING` 且零 persistent DDL。

### 对 P1 `document_chunks` 的 additive columns

~~~text
status TEXT CHECK(status IN ('ready','stale'))
content_kind TEXT CHECK(content_kind IN ('text','verbatim','structured'))
chunk_key TEXT
chunking_version TEXT
source_content_sha256 TEXT
char_start INTEGER
char_end INTEGER
created_at TEXT
updated_at TEXT
stale_at TEXT
~~~

Migration 对既有 rows 只做可证明的 deterministic backfill；P1 初始表应为空。新写入要求 status/chunk_key/version/source hash/offset/timestamps 全部非空，offset 合法；partial unique index 覆盖 non-null `chunk_key`，query index 覆盖 `(source_document_id,status,sequence)`。

### `document_chunk_embeddings`

~~~text
id TEXT PRIMARY KEY
chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE
source_document_id TEXT NOT NULL REFERENCES document_sources(id) ON DELETE CASCADE
provider TEXT NOT NULL
model TEXT NOT NULL
embedding_version TEXT NOT NULL
dimensions INTEGER NOT NULL CHECK(dimensions > 0)
vector BLOB
vector_sha256 TEXT
chunk_content_sha256 TEXT NOT NULL CHECK(length(chunk_content_sha256)=64)
status TEXT NOT NULL CHECK(status IN ('ready','failed','stale'))
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
stale_at TEXT
UNIQUE(chunk_id,provider,model,embedding_version)
~~~

Ready row 必须有 finite little-endian float32 vector、`length(vector)=dimensions*4` 和 vector SHA；failed 必须有 typed error_code 且 vector 为 NULL；stale 必须有 stale_at。索引覆盖 `(source_document_id,status,provider,model,embedding_version)` 与 `chunk_id`。Vector 在写入前 L2 normalize；zero/NaN/Infinity/dimension mismatch 为 `EMBEDDING_RESPONSE_INVALID`。

### `artifact_translation_checkpoints`

~~~text
artifact_id TEXT NOT NULL REFERENCES generated_artifacts(id) ON DELETE CASCADE
chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE
sequence INTEGER NOT NULL CHECK(sequence >= 0)
source_content_sha256 TEXT NOT NULL CHECK(length(source_content_sha256)=64)
provider TEXT NOT NULL
model TEXT NOT NULL
prompt_version TEXT NOT NULL
status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed'))
translated_markdown TEXT
content_sha256 TEXT
attempt INTEGER NOT NULL CHECK(attempt >= 0)
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
PRIMARY KEY(artifact_id,sequence)
UNIQUE(artifact_id,chunk_id)
~~~

Succeeded checkpoint 必须有 non-empty translated_markdown/content SHA；resume 只复用 source/chunk/provider/model/prompt 全部相同的 succeeded row。最终 artifact 只有在 0..N-1 全部 succeeded 且 source 仍 ready 时才按 sequence 组装并 publish。

### FTS5 external-content table 与 triggers

~~~sql
CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
  heading_path,
  content,
  content='document_chunks',
  content_rowid='rowid',
  tokenize='trigram case_sensitive 0 remove_diacritics 1'
);
~~~

Migration 创建三个 named triggers：

- `document_chunks_fts_ai`：after insert 写 new.rowid/heading_path/content；
- `document_chunks_fts_ad`：after delete 以 FTS5 `delete` command 删除 old row；
- `document_chunks_fts_au`：after update 先删除 old，再插入 new。

创建后执行 FTS5 `rebuild`，再比较 `document_chunks` count 与可 join FTS rowid count。Search 必须 join chunks→sources 并过滤二者 status=ready，因此 stale rows 即使保留索引也不可能命中。Upgrade 在任何 persistent DDL 前用 TEMP FTS5 trigram probe 同时验证英文 substring 与无空格中文 sentinel（正文“这是一个机器学习模型”查询“机器学习”）；缺 FTS5、trigram tokenizer、所需 options 或任一命中能力时抛 `FTS5_TRIGRAM_UNAVAILABLE`，且零 persistent DDL。Public lexical query trim 后必须至少包含三个 Unicode code point；更短 query 返回 422 `SEARCH_QUERY_TOO_SHORT`，不以 `LIKE` 或全表扫描伪装支持。

---

## API 契约

### Artifact 与 index commands

- `POST /api/v2/papers/{paper_id}/artifacts/translation`
- `POST /api/v2/papers/{paper_id}/artifacts/classification`
- `POST /api/v2/papers/{paper_id}/artifacts/metadata`
- `POST /api/v2/papers/{paper_id}/artifacts/summary`

四者body均为 `{"sourceMode":"native","sourceDocumentId":"src_01"}`，成功202返回 `{artifact,job,deduplicated}`。camelCase sourceMode必须与source row.mode相同，否则422 `SOURCE_MODE_MISMATCH`。Provider/model/prompt从immutable backend settings/profile解析并写入identity，不接受client secret/base URL。

`POST /api/v2/papers/{paper_id}/index` body为 `{"sourceMode":"native","sourceDocumentId":"src_01","includeEmbeddings":true}`；sourceMode必须匹配source，true使用当前frozen embedding profile，false只materialize chunks/FTS，均创建job_type=embed并返回202。`GET /api/v2/papers/{paper_id}/index-status?sourceDocumentId=src_01` 返回totalChunks/readyChunks/embeddedChunks/staleChunks/failedEmbeddings/provider/model/version/coverage。

所有 P3 artifact command 复用 P2 domain builder；kind 或 kind-specific options 不同必得不同 artifact/job key。Index command 使用同一 `domain/processing.py` 中唯一的独立 builder：

```text
index_options_hash = sha256(canonical_json({includeEmbeddings, chunkingVersion, embeddingOptions}))
index_job_key = sha256("job:index:v1\0" + source_document_id + "\0" + source_content_sha256 + "\0" + chunking_version + "\0" + embedding_provider_or_none + "\0" + embedding_model_or_none + "\0" + embedding_version_or_none + "\0" + index_options_hash)
```

`includeEmbeddings=false` 的 provider/model/version 固定为 `none` 且零 CredentialStore/provider construction；true 时从冻结 Embedding ProviderProfile 取得非秘密 identity，并只通过 P1 `CredentialStore.get(embedding)` 取得 secret。相同 identity 的 terminal job 不隐式复活，显式 retry 继续使用 P2 lineage。

### Chunk search

`POST /api/v2/search/chunks` body：

~~~json
{
  "query": "robust multimodal evaluation",
  "mode": "hybrid",
  "paperIds": ["paper-1"],
  "limit": 20
}
~~~

`paperIds` 可省略；limit 1–50。Response：

~~~json
{
  "items": [{
    "paperId": "paper-1",
    "sourceDocumentId": "src_01",
    "chunkId": "chunk_01",
    "sequence": 3,
    "headingPath": ["Methods", "Evaluation"],
    "pageStart": 5,
    "pageEnd": 6,
    "excerpt": "Evaluation protocol",
    "score": 0.0325,
    "lexicalScore": 4.2,
    "semanticScore": 0.81
  }],
  "coverage": {
    "readyChunks": 42,
    "embeddedChunks": 40,
    "staleChunks": 0,
    "failedEmbeddings": 2
  }
}
~~~

lexical mode 的 semanticScore 为 NULL；semantic mode 的 lexicalScore 为 NULL。空结果仍 200。Semantic/hybrid 未配置 query embedding provider 返回 409 `EMBEDDING_PROFILE_UNAVAILABLE`，不自动退成 lexical；客户端要 lexical 必须显式请求 lexical。

---

## Typed 错误码

| Code | HTTP/状态 | 语义 |
|---|---|---|
| `SOURCE_NOT_READY` | 409 | source 不是 ready |
| `SOURCE_STALE` | 409 | source 或当前 PDF identity 已 stale |
| `SOURCE_CHUNKS_NOT_READY` | 409 | read-only build/search 所需 chunks 不完整 |
| `CHUNK_COVERAGE_INVALID` | job failed | offset/sequence/join/hash 不能覆盖全文 |
| `CHUNK_ATOMIC_BLOCK_TOO_LARGE` | job failed | fenced atomic block超过8192 tokens |
| `MARKDOWN_STRUCTURE_INVALID` | job failed | translation未能保留fence/table/math/escaped delimiter |
| `CHUNKING_VERSION_MISMATCH` | 409 | 请求/read rows 与 active chunking version 不同 |
| `CONTEXT_BUDGET_INVALID` | 422 | budget 非法或小于单个最小 unit |
| `CONTEXT_COVERAGE_INVALID` | job failed | plan 遗漏/重复 chunk |
| `ARTIFACT_KIND_UNSUPPORTED` | 422 | P3 command kind 不在四种消费者中 |
| `ARTIFACT_OUTPUT_INVALID` | job failed | structured output schema/正文非法 |
| `TRANSLATION_CHECKPOINT_CONFLICT` | job failed | checkpoint identity 与 source/provider/version 不同 |
| `FTS5_TRIGRAM_UNAVAILABLE` | migration/startup 503 | SQLite 无所需 FTS5 trigram/CJK substring capability |
| `SEARCH_QUERY_TOO_SHORT` | 422 | lexical query 少于三个 Unicode code point |
| `SEARCH_QUERY_INVALID` | 422 | query/mode/filter/limit 非法 |
| `EMBEDDING_PROFILE_UNAVAILABLE` | 409 | semantic profile 未配置 |
| `EMBEDDING_REQUEST_FAILED` | queued retry/job failed | typed local/API embedding failure |
| `EMBEDDING_RESPONSE_INVALID` | job failed | vector count/dimension/value 非法 |
| `EMBEDDING_INDEX_STALE` | 409/coverage | requested profile 只有 stale vectors |
| `INDEX_NOT_READY` | 409 | source 尚无完整 ready chunks |
| `P3_BASE_SCHEMA_MISSING` | migration/startup 503 | P2 head/columns/tables 不完整 |
| `P3_DOWNGRADE_BLOCKED_NONEMPTY` | downgrade blocked | P3 state 存在且未明确允许丢弃 |

错误 envelope 沿用 P1 `{error:{code,message,details}}`；details 不含 document content、translation、prompt、vector bytes、API key、Authorization 或 Provider raw response。

---

## 文件职责

- Create: `backend/migrations/versions/20260807_03_source_consumers_search.py`
- Create: `backend/app/domain/context.py`
- Modify: `backend/app/domain/entities.py`
- Create: `backend/app/application/context_builder.py`
- Create: `backend/app/application/source_freshness.py`
- Create: `backend/app/application/document_artifacts.py`
- Create: `backend/app/application/document_search.py`
- Create: `backend/app/application/ports/embedding_provider.py`
- Modify: `backend/app/application/ports/repositories.py`
- Modify: `backend/app/repositories/models.py`
- Modify: `backend/app/repositories/unit_of_work.py`
- Create: `backend/app/repositories/document_chunks.py`
- Create: `backend/app/repositories/document_search.py`
- Create: `backend/app/repositories/translation_checkpoints.py`
- Create: `backend/app/providers/embeddings/__init__.py`
- Create: `backend/app/providers/embeddings/model2vec.py`
- Create: `backend/app/providers/embeddings/openai_compatible.py`
- Verify: `backend/app/application/ports/credential_store.py` — 使用 P1 `embedding` kind；不得新增 secret seam。
- Modify: `backend/app/providers/generation.py`
- Modify: `backend/app/workers/processing_worker.py`
- Create: `backend/app/api/routes/document_consumers.py`
- Create: `backend/app/api/routes/document_search.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/bootstrap.py`
- Modify: `backend/app/infrastructure/database_backup.py`
- Create: `backend/tests/test_p3_migration.py`
- Create: `backend/tests/test_context_builder.py`
- Create: `backend/tests/test_source_freshness.py`
- Create: `backend/tests/test_document_artifacts.py`
- Create: `backend/tests/test_translation_resume.py`
- Create: `backend/tests/test_fts_search.py`
- Create: `backend/tests/test_chunk_embeddings.py`
- Create: `backend/tests/test_document_search_api.py`
- Modify: `backend/tests/test_database_backup.py`
- Modify: `frontend/src/lib/api/artifactGateway.ts`
- Modify: `frontend/src/lib/api/insightsGateway.ts`
- Create: `frontend/src/lib/api/insightsGateway.test.ts`
- Modify: `frontend/src/features/reader/useArtifactCommands.ts`
- Modify: `frontend/src/features/reader/ArtifactPanel.test.tsx`
- Modify: `docs/DATABASE.md`

---

## Task 0：重新验证 P0.1 baseline 与 P2 fixed-revision 入口

**Files:**

- Verify: `contracts/pre-existing-test-failures-v1.json`
- Verify: `scripts/pre-existing-failure-baseline.mjs`
- Verify: P2 restored-copy and Live revision evidence

- [ ] **运行 P3 入口 exact baseline verifier**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p3EntryBaselineJson = node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
$p3EntryBaselineCode = $LASTEXITCODE
if ($p3EntryBaselineCode -ne 0) { throw "P3 entry baseline verification failed with exit code $p3EntryBaselineCode." }
$p3EntryBaseline = $p3EntryBaselineJson | ConvertFrom-Json
$p3EntryBaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p3EntryBaselineField in $p3EntryBaselineRequiredFields) {
  if (-not ($p3EntryBaseline.PSObject.Properties.Name -contains $p3EntryBaselineField)) { throw "P3 entry baseline verifier omitted required field $p3EntryBaselineField." }
}
if ($p3EntryBaseline.baselineMatched -isnot [bool] -or $p3EntryBaseline.baselineMatched -ne $true) { throw 'P3 entry baseline verifier did not report boolean baselineMatched=true.' }
if ($p3EntryBaseline.observedSuiteExitCode -isnot [int] -and $p3EntryBaseline.observedSuiteExitCode -isnot [long]) { throw 'P3 entry baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p3EntryBaseline.overallGreen -isnot [bool]) { throw 'P3 entry baseline verifier did not report boolean overallGreen.' }
$p3EntryObservedSuiteExitCode = [long]$p3EntryBaseline.observedSuiteExitCode
if (($p3EntryObservedSuiteExitCode -eq 0) -ne $p3EntryBaseline.overallGreen) { throw 'P3 entry baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
~~~

Expected: verifier process exit 0 and `baselineMatched=true`; an accepted raw non-zero remains visible with `overallGreen=false`. Any ID/signature/hash/path drift stops before P3 migration tests. Separately confirm the recorded P2 Live and restore evidence each show a unique `20260807_02` revision; this Task performs no DB command and no migration.

---

## Task 1：建立 final P3 additive migration、FTS5 与 guarded downgrade

**Files:**

- Create: `backend/migrations/versions/20260807_03_source_consumers_search.py`
- Modify: `backend/app/repositories/models.py`
- Create: `backend/tests/test_p3_migration.py`

- [ ] **RED：先写 exact migration tests**

从临时 P2 head 升级，断言 revision/down_revision、P1 五主表/P2 columns 完全保留；document_chunks 只增加本计划 columns/index；embedding/checkpoint tables、FTS virtual table、三个 triggers 精确存在。测试 insert/update/delete external-content 同步、rebuild/backfill、foreign keys/vector/checkpoint constraints。缺 P2 head/任一 base table/column 时断言 `P3_BASE_SCHEMA_MISSING` 且 sqlite_master 前后相同；缺 FTS5/trigram、非法 tokenizer options 或中文/英文 TEMP sentinel probe失败时断言 `FTS5_TRIGRAM_UNAVAILABLE` 且零 persistent DDL。Downgrade 空状态保留完整 P2 schema/hard data；非空默认 guard；显式 x argument 仅在隔离 DB 可 drop P3 objects/columns。

本模块同时定义下游命令引用的 `P3RestoredCopyValidationTests(unittest.TestCase)`，包含精确方法 `test_db_path_is_bound_restore_at_exact_p3_revision` 与 `test_p3_schema_fts_health_and_required_objects_are_read_only`。两项都要求 process `DB_PATH` 和 `MIGRATION_RESTORE_ROOT`；在首次 SQLite open 前解析 containment，拒绝 Live `data/app.db`、非 `restore-validation-*` parent、sibling-prefix、symlink/junction/reparse escape、missing/multiple current 或非 `20260807_03`。validator 只读验证 P1/P2 hard schema、P3 columns/tables/FTS/triggers、FTS integrity/coverage、quick/integrity/FK，并断言 bytes/size/mtime/sidecars 前后不变。12 张 legacy count/hash 相等由演练 PowerShell 的逐 phase snapshots执行。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p3_migration -v
~~~

Expected RED：revision、P3 columns/tables/FTS/triggers/guard 尚不存在。Import、P2 fixture、Live path 或本机 FTS capability 误判必须先修正，不能算目标 RED。

- [ ] **最小实现**

Upgrade 先完成 read-only schema/FTS TEMP probe，再 ADD COLUMN、backfill、index、create auxiliary tables、create FTS/triggers、rebuild、validate counts，任何失败整事务 rollback。不得 CREATE/rebuild P1 五主表。Downgrade 在任一 DROP 前检查 P3 tables 与 P3-added columns；默认阻断非空 state。获准后按 triggers→FTS→checkpoint/embedding tables→indexes→batch drop P3 columns 的顺序执行，并校验 P2 table counts/hash。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p3_migration -v
~~~

Expected GREEN：preflight、exact DDL、FTS sync/rebuild、constraints、P2-preserving downgrade、guard 和 re-upgrade 全绿。

**Exit gate:** `alembic heads` 只能输出 `20260807_03 (head)`；P4–P6 不再创建 revision。

---

## Task 2：固定 chunk/context/search domain 与 Ports

**Files:**

- Create: `backend/app/domain/context.py`
- Modify: `backend/app/domain/entities.py`
- Create: `backend/app/application/ports/embedding_provider.py`
- Modify: `backend/app/application/ports/repositories.py`
- Create: `backend/tests/test_context_builder.py`

- [ ] **RED：先写纯 domain invariant tests**

覆盖ChunkingSpec、DocumentChunk/Set、ContextRequest/Batch/Plan、EmbeddingProfile/Request/Batch、SearchRequest/Hit/Coverage invariants；断言直接复用 P1 七值 ArtifactKind，P3 不新增 enum。扩展 P2 processing-domain golden tests：五种已实现 artifact kind 在相同 source/profile 下 key 两两不同，kind-specific options 改变时 key 改变；index key 对 includeEmbeddings、chunking version、embedding provider/model/version/options 敏感且跨进程稳定。为chunk/embedding/RRF写golden values。拒绝gap/overlap/hash/page/vector/query错误。ProcessingJob继续支持global obsidian_sync的nullable paper/sourceMode，P3 document jobs仍强制二者非空。证明domain import不加载SQLAlchemy/FastAPI/NumPy/provider。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.ContextDomainTests -v
~~~

Expected RED：context domain/embedding Port/repository Port 尚缺失；测试不连接数据库或 Provider。

- [ ] **最小实现**

用标准库 frozen dataclass/String Enum/hash helpers 实现所有 invariants。Vector domain 只接受 immutable finite float tuple；BLOB packing 留给 repository adapter。Repository Ports 明确区分 command/write 与 query/read-only 方法，所有 async 方法返回 domain values，不泄漏 ORM row。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.ContextDomainTests -v
~~~

Expected GREEN：golden identity/RRF、invalid construction 和 import isolation 全部通过。

**Exit gate:** 后续 chunker、artifact、search 和 Provider 共用同一 domain vocabulary，不复制 hash/rank/status 字符串。

---

## Task 3：实现 deterministic coverage chunk materialization

**Files:**

- Create: `backend/app/repositories/document_chunks.py`
- Modify: `backend/app/repositories/unit_of_work.py`
- Create: `backend/app/application/context_builder.py`
- Modify: `backend/tests/test_context_builder.py`

- [ ] **RED：先写 chunk algorithm/repository tests**

用规范化中英Markdown、headings/pages、完整/未闭合fence、pipe tables、inline/display math、escaped dollar/backtick/pipe、1601-token atomic、8193-token拒绝、超长text/emoji fixtures。断言exact boundary formula、offset/content_kind/hash/token/page/heading；plain≤1600、atomic≤8192、join=source，boundary不落入fence/table row/math/escape，跨进程IDs相同。覆盖dedupe/concurrency/rollback/version/stale。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.DeterministicChunkingTests -v
~~~

Expected RED：chunker/repository/materialize_chunks 尚缺失；失败必须指向 coverage/identity/persistence，不是 fixture encoding。

- [ ] **最小实现**

按本计划七步算法生成 memory ChunkSet，先完整 validate coverage，再开启短 UoW 插入；unique conflict 后读取并逐字段比较 winner，不一致报 `CHUNK_COVERAGE_INVALID`。不得逐 chunk commit。FTS triggers 随事务同步。Source markdown 只在 read transaction 复制一次，chunker 不接受 PDF path/bytes。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.DeterministicChunkingTests -v
~~~

Expected GREEN：所有复杂 fixture round-trip、determinism、concurrency、version swap 和 rollback 全绿。

**Exit gate:** 任一 `"".join(chunks) != source.markdown` 都是 hard failure，不能用 trimmed/normalized comparison 放宽。

---

## Task 4：实现 consumer-specific 且可审计的 ContextBuilder

**Files:**

- Modify: `backend/app/application/context_builder.py`
- Modify: `backend/tests/test_context_builder.py`

- [ ] **RED：先写六种 policy 的 selection/coverage tests**

构造至少 40 chunks，后部 Discussion/Conclusion 含唯一 sentinel，末尾 References含不应发送的 poison。测试 translation/embedding覆盖0..39；summary覆盖全部非references/ack chunks；explain按priority headings覆盖前后eligible sections，过长Methods先完整section summaries再final，tail sentinel到达Fake explainer且poison缺席；classification input只含首页/摘要/方法概述/结论、总量≤3200且中间实验/参考文献poison缺席；metadata只含首页/题录、总量≤1600。覆盖heading aliases、中英 heading、无page fallback、budget boundary、selected/excluded reasons、stale/missing/mixed-version/gap fail closed；build前后DB rows/hash相同。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.ContextPlanCoverageTests -v
~~~

Expected RED：build/policies/coverage validator 尚缺失；若 sentinel 因 fixture budget 不足而未进入 expected plan，先修 fixture。

- [ ] **最小实现**

Build 在一个 read-only UoW 中读取 ready source与单一 ready chunk version，关闭连接后纯内存 classify headings/select/pack/validate。Translation/embedding要求all coverage；summary/explain先计算eligible set再要求eligible coverage；classification/metadata按固定category cap构建bounded set并记录每个excluded reason。Budget不能静默删除eligible chunk：explain/summary超限必须生成有child ranges的section/map summary nodes。ContextBuilder提供完整 level/range graph，consumer填summary内容后再次验证coverage。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder.ContextPlanCoverageTests -v
~~~

Expected GREEN：六种policy、priority heading、tail sentinel、references/ack exclusion、section-summary hierarchy、bounded classification/metadata、stale fail-closed和zero-write proof全绿。

**Exit gate:** code review禁止任何consumer使用 `markdown[:N]`、`chunks[:N]` 或只取first batch；classification/metadata的bounded slice只能由ContextBuilder按上述semantic categories产生。

---

## Task 5：实现 explicit source stale cascade

**Files:**

- Create: `backend/app/application/source_freshness.py`
- Modify: `backend/app/repositories/document_chunks.py`
- Modify: `backend/app/repositories/document_search.py`
- Create: `backend/tests/test_source_freshness.py`

- [ ] **RED：先写 freshness/cascade transaction tests**

覆盖 PDF SHA 改变 stale 全部 mode；同 mode 的 provider/model/options/processing version 改变 stale 旧 source；相同 identity dedupe；native 与 OCR 不因 mode 不同互相 stale。每次 cascade 断言 source、dependent ready artifacts、chunks、embeddings、artifact head、queued/running jobs 的精确状态；legacy papers.explainer/translations/metadata projection 保持。注入每个写点失败断言整事务 rollback；并发 activate 通过 CAS 只留一个同-mode active identity。Stale chunk/vector 即使仍在 FTS/vector table，也不得被 search repository 返回。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_freshness -v
~~~

Expected RED：freshness service/cascade repository 尚缺失；测试使用显式 SHA/clock，不改真实 PDF。

- [ ] **最小实现**

`reconcile_pdf` 和 `activate_source` 在短 `BEGIN IMMEDIATE` UoW 中重读 source identities，使用 ready→stale CAS、写 stale_at，按 FK 查询 dependent rows并批量 stale。删除 stale artifact 的 head row但不清 legacy projection；queued job立即 cancelled，running job只写 cancel_requested_at，terminal job不改。所有 search queries强制 join source/chunk/embedding ready predicates。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_freshness -v
~~~

Expected GREEN：五种 identity drift、mode coexistence、atomic rollback、CAS race、job handling 和 search exclusion 全绿。

**Exit gate:** stale 是持久业务状态，不以 delete、cache miss、query-time SHA repair 或 legacy fallback 隐藏。

---

## Task 6：实现全 chunk translation 与断点续跑

**Files:**

- Create: `backend/app/repositories/translation_checkpoints.py`
- Create: `backend/app/application/document_artifacts.py`
- Modify: `backend/app/providers/generation.py`
- Modify: `backend/app/workers/processing_worker.py`
- Create: `backend/tests/test_translation_resume.py`

- [ ] **RED：先写 translation checkpoint/resume tests**

使用25-chunk plan，含fence/display-math verbatim与table/inline-math structured。断言plain/structured calls、verbatim零call；最终sequence assembly、fence、pipe layout、公式、escaped delimiters golden round-trip且tail sentinel存在；placeholder丢失/重复/重排以 `MARKDOWN_STRUCTURE_INVALID` non-retryable失败。再测chunk11 transient resume、crash/cancel/identity/stale/rollback。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_translation_resume -v
~~~

Expected RED：checkpoint repository/P3 translation handler 尚缺失；Fake call ledger 必须能证明成功 chunk 未重复。

- [ ] **最小实现**

Handler 先用 ContextBuilder read-only build translation plan，再为每个 sequence读取/验证 checkpoint；只调用缺失/failed chunk。每个成功 chunk 在独立短事务 CAS succeeded，并 report_progress。外部调用期间不持 DB transaction。全部 succeeded 后重读 source/chunk identities，组装非空 Markdown并使用 P2 artifact publication transaction。Cancel/lease loss 停止下一 chunk，不删除已成功 checkpoint。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_translation_resume -v
~~~

Expected GREEN：full coverage、resume call ledger、crash/cancel、identity conflicts、stale fence 和 atomic publish 全绿。

**Exit gate:** translation 不能调用 `agent/translate.py` 的 PDF extraction或重新 chunk；P3 provider 只翻译 ContextBatch。

---

## Task 7：迁移 explainer、classification、metadata 与 summary 到 ContextPlan

**Files:**

- Modify: `backend/app/application/document_artifacts.py`
- Modify: `backend/app/providers/generation.py`
- Modify: `backend/app/repositories/generated_artifacts.py`
- Create: `backend/tests/test_document_artifacts.py`

- [ ] **RED：先写三个 structured consumer tests**

Explainer Fake验证priority sections、references/ack exclusion、长section summaries层级和后部Conclusion sentinel进入最终Markdown。Classification Fake断言调用input只含首页/摘要/方法概述/结论且无中间无关section；metadata Fake只收到首页题录；summary Fake的map/reduce覆盖全部eligible body并排除references/ack poison。Provider只收到ContextBatch/Paper metadata而非PDF/legacy fulltext。验证 strict JSON schema、unknown/missing/wrong-type/empty response fail closed。Classification只投影type/topic/task/models/datasets/tags/relevance；metadata只CAS allowlist且不以空值覆盖可信identifier；summary只投影tldr/contribution；explainer投影papers.explainer。Artifact/head/projection/job原子，旧ready artifact immutable。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_artifacts -v
~~~

Expected RED：consumer handlers/map-reduce adapters/projection methods 尚缺失；若 Fake 允许未覆盖 tail 仍生成正确答案，测试 fixture 无效，先修 fixture。

- [ ] **最小实现**

为四kind建独立typed prompt/output adapters和版本；共用ContextPlan executor但不共用selection policy/output schema。Explainer与summary的section/map results包含covered ranges，reduce逐层验证union；classification/metadata直接验证bounded plan categories。Structured outputs以canonical JSON保存，explainer保存Markdown。Publish前revalidate source ready/identity；UoW内插artifact、更新head、执行kind-specific projection、complete job。Provider exception按typed retry policy，schema invalid non-retryable。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_artifacts -v
~~~

Expected GREEN：四consumer policy inputs、explainer tail/reference hierarchy、schema negatives、projection allowlists、identity/cache、rollback/immutability全绿。

**Exit gate:** 新 Python path 不得调用 `agent/llm.py` 中含固定 prefix slice 的 classify/metadata/summary entrypoint；legacy mode 可继续保留旧实现。

---

## Task 8：实现 external-content FTS5 lexical search

**Files:**

- Create: `backend/app/repositories/document_search.py`
- Create: `backend/app/application/document_search.py`
- Create: `backend/tests/test_fts_search.py`

- [ ] **RED：先写 FTS consistency/query tests**

覆盖 migration trigger insert/update/delete、rollback不泄漏 index row、FTS rebuild 后 rowid join完整。Query fixtures包含英文 substring/phrase、正文“这是一个机器学习模型”查询“机器学习”的无空格中文命中、punctuation/quotes、heading-only match、相同 BM25 score tie、multi-paper filter、stale source/chunk。单/双 code-point query 明确 422 `SEARCH_QUERY_TOO_SHORT`。断言参数化 MATCH/escaped user syntax不产生 SQL injection/OperationalError；result 带 exact source/chunk/sequence/heading path/page range/safe excerpt；排序 BM25 后以 paper_id/source_id/sequence稳定 tie-break；query 前后 total_changes=0、DB hash不变。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_fts_search -v
~~~

Expected RED：FTS repository/query normalization/ranking 尚缺失；本机无 FTS5 时应由 Task 1 capability test明确失败，不 skip 核心行为。

- [ ] **最小实现**

Lexical repository使用一条参数化 CTE：trigram FTS MATCH产生 rowid/BM25，join document_chunks/document_sources/papers，强制 ready predicates和可选 paper IDs，limit在Pydantic验证后绑定。普通用户 query按 literal trigram policy 编译并拒绝短于三个 Unicode code point；raw FTS grammar 不在 P3 public API。Excerpt按命中位置截取有界文本并转义控制字符，不返回整 chunk。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_fts_search -v
~~~

Expected GREEN：trigger/rebuild/rollback、CJK/English/escaping/filter/stale/rank/provenance 和 zero-write proof 全绿。

**Exit gate:** 正文 lexical search 不再使用 `LIKE '%query%'`；metadata filters 可以继续使用参数化普通 columns。

---

## Task 9：实现 chunk embedding Providers、持久 index 与 resume

**Files:**

- Create: `backend/app/providers/embeddings/__init__.py`
- Create: `backend/app/providers/embeddings/model2vec.py`
- Create: `backend/app/providers/embeddings/openai_compatible.py`
- Modify: `backend/app/application/document_search.py`
- Modify: `backend/app/repositories/document_search.py`
- Modify: `backend/app/workers/processing_worker.py`
- Create: `backend/tests/test_chunk_embeddings.py`

- [ ] **RED：先写 Provider/index/resume tests**

Embedding Port contract tests覆盖 input/output count、provider/model/version、finite dimension、L2 normalization、little-endian float32 BLOB/hash。Model2Vec 用 injected static model，证明 import/startup/query不加载或下载模型，只有 embed worker首次调用才 lazy load且复用。OpenAI-compatible 用 fake transport覆盖 exact `/embeddings` request、batch order、timeout/429/500 typed retry、401/403 non-retry、malformed/empty/mixed-dimension response invalid、secret/raw body redaction；CredentialStore spy 证明只请求 `embedding` kind，env→Keyring→legacy `embedApiKey` priority 已由 P1 contract 提供，profile/DTO/log/error 不含 secret。Index 以 configurable batch size遍历全部 ready chunks；已 ready identity零重复调用；中途失败后 retry只处理 missing/failed；source/chunk stale fence；includeEmbeddings=false 时 CredentialStore、Provider factory、transport 全零调用。并断言 artifact/index enqueue 使用 P2 唯一 key builders，重复 request 返回同 job，kind/profile/options 变化不碰撞。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_chunk_embeddings -v
~~~

Expected RED：Providers/embedding repository/index handler 尚缺失；任何真实下载/DNS/HTTP 都是 test defect。

- [ ] **最小实现**

Adapters只实现已知现有 contracts：model2vec `StaticModel.encode` 和 OpenAI-compatible `POST /embeddings`；不猜额外 response fields。OpenAI-compatible adapter接收已解析的内部 Credential，不读取 settings/environment/Keyring；composition 仅在显式 includeEmbeddings=true 的 worker path 通过 P1 CredentialStore取得 `embedding` credential。Transport/parser把 rate-limit/timeout/server/auth/schema failures归一成 typed failure。Index handler先 materialize/validate chunks，再批量 embed，逐 batch短事务 insert/CAS rows并 report progress；ready row immutable。Provider/model/version变更写新 identity并 stale旧 profile，不覆盖 vector。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_chunk_embeddings -v
~~~

Expected GREEN：两个 Adapter contracts、redaction、BLOB round-trip、all-chunk coverage、resume/no-repeat、profile stale 和 lexical-only zero construction 全绿。

**Exit gate:** test/build artifact 不包含下载的 model cache；production model download/cache policy沿用现有 settings并只在显式 embed job中发生。

---

## Task 10：实现 semantic/hybrid search、Worker dispatch 与 FastAPI routes

**Files:**

- Modify: `backend/app/application/document_search.py`
- Modify: `backend/app/repositories/document_search.py`
- Modify: `backend/app/workers/processing_worker.py`
- Create: `backend/app/api/routes/document_consumers.py`
- Create: `backend/app/api/routes/document_search.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/bootstrap.py`
- Create: `backend/tests/test_document_search_api.py`

- [ ] **RED：先写 semantic/hybrid/query-no-write 与 HTTP tests**

用fixed vectors测试cosine/profile/stale/filter/ties/coverage和 `RRF k=60` golden ranks；Query Provider每request最多一次且document vector calls为零，search零写。HTTP覆盖existing explainer policy、四个P3 artifact commands、index true/false/status和三种search；每个artifact/index command都要求camelCase sourceMode/sourceDocumentId，missing/unknown/snake_case/mode mismatch fail closed。Worker与 app factory 复用 P1 revision gate，但必须显式传 `required_schema_revision="20260807_03"`，注册translate/explain/embed并按artifact.kind分派；测试证明 `20260807_02` 被拒绝、`20260807_03` 被接受，且没有第二套或写死 P1 的 schema gate。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_search_api -v
~~~

Expected RED：semantic/hybrid rank、P3 handlers/routes/bootstrap尚缺失；search fixture不得依赖真实Provider。

- [ ] **最小实现**

Semantic query解析frozen profile，生成一个query vector，read-only repository按exact provider/model/version/dimension读取ready vectors并用NumPy批量cosine；local model不在query path下载，cache缺失返回 `EMBEDDING_PROFILE_UNAVAILABLE`。Hybrid纯内存融合两个已排序hit lists。Search UoW使用read-only connection/transaction且禁止flush/commit。Routes strict validate并await application Interfaces；commands只enqueue，search只query。Worker外部调用期间无写事务，并沿用P2 lease/checkpoint/cancel/retry。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_search_api -v
~~~

Expected GREEN：semantic/RRF golden ranks、coverage/stale/profile、zero-write、all API contracts、worker dispatch和schema gate全绿。

**Exit gate:** semantic/hybrid query遇到不完整index只报告coverage并搜索现有ready vectors；不得enqueue embed、调用 `DocumentSearch.index` 或改变status。

---

## Task 11：接入 React Artifact/Insights Gateways，不改 UI

**Files:**

- Modify: `frontend/src/lib/api/artifactGateway.ts`
- Modify: `frontend/src/lib/api/insightsGateway.ts`
- Create: `frontend/src/lib/api/insightsGateway.test.ts`
- Modify: `frontend/src/features/reader/useArtifactCommands.ts`
- Modify: `frontend/src/features/reader/ArtifactPanel.test.tsx`

- [ ] **RED：先写 strict Gateway/Hook tests**

为 `searchChunks/enqueueIndex/getIndexStatus` 写exact request/strict decoder/error tests，覆盖nullable scores/page、heading array、coverage、unknown enum/field/missing field fail closed。ArtifactGateway在显式注入P3 processing adapter时把translatePaper映射为source ready→translation enqueue→job polling，legacy exported singleton仍走现有NDJSON直到P4切换。Hook stop/unmount/paper switch只detach polling，不cancel server；显式Gateway cancel仍可用。断言现有按钮、ARIA、文案、tab顺序和CSS class不变。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- src/lib/api/insightsGateway.test.ts src/features/reader/ArtifactPanel.test.tsx
~~~

Expected RED：P3 Gateway methods/decoders/translation adapter尚缺失；现有legacy tests必须保持GREEN。

- [ ] **最小实现**

在现有injectable Gateway factory添加P3 adapter参数，默认undefined保持legacy。复用P2 ProcessingGateway job DTO/poller，不复制status decoder。Insights Gateway只返回typed data，不在Gateway/Hook自动enqueue index、fallback search mode或修改component state。Hook owner继续绑定paperId/generation/runId/serverJobId，late response不得覆盖新owner。零JSX/CSS edit。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- src/lib/api/insightsGateway.test.ts src/features/reader/ArtifactPanel.test.tsx
~~~

Expected GREEN：strict search/index DTO、translation polling/detach/owner race和现有DOM behavior全绿；default production singleton行为未提前切换。

**Exit gate:** P3只交付Gateway/Hook seam；P4才负责FastAPI takeover与runtime路由切换。

---

## Task 12：扩展 backup fingerprints、运维文档并演练 P3 rollback

**Files:**

- Modify: `backend/app/infrastructure/database_backup.py`
- Modify: `backend/tests/test_database_backup.py`
- Modify: `docs/DATABASE.md`

- [ ] **RED：先写 P3 backup/documentation tests**

临时P3 DB manifest必须记录documentChunks/chunkEmbeddings/translationCheckpoints critical counts/hash，以及FTS logical row coverage/`integrity-check`；不直接hash FTS shadow-table physical layout。篡改chunk/vector/checkpoint或破坏FTS trigger/index后verify必须分类失败。Documentation contract断言final head、FTS capability、explicit index command、query-no-reembed、source stale cascade、backup→restore→upgrade/downgrade drill、guard和runtime rollback值/stop order全部出现。`backend/tests/test_p3_migration.py` 必须明确创建命令使用的 `P3OperationalDocumentationTests(unittest.TestCase)`，其唯一 public method 名为 `test_p3_runbook_contains_fixed_migration_search_and_rollback_contract`；该 method 只读 `docs/DATABASE.md`，不打开 DB，并逐项断言固定 revisions、12-table map-presence/equality guards、FTS diagnostics、downgrade/re-upgrade commands 与 stop conditions。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_p3_search_fingerprints backend.tests.test_database_backup.DatabaseBackupTests.test_verify_detects_p3_search_tampering backend.tests.test_p3_migration.P3OperationalDocumentationTests -v
~~~

Expected RED：P3 fingerprints/FTS verification/runbook尚缺失；只使用临时DB。

- [ ] **最小实现**

扩展ordered logical fingerprint registry；vector hash只处理stored bytes SHA/identity，不把vector写进日志/JSON。FTS verify运行documented integrity command并比较ready row coverage。`docs/DATABASE.md` 写清writer/worker stop、P0 backup verification、restored-copy drill、final head、FTS诊断、embedding显式重建、runtime legacy值、guarded downgrade与snapshot data-loss boundary。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_p3_search_fingerprints backend.tests.test_database_backup.DatabaseBackupTests.test_verify_detects_p3_search_tampering backend.tests.test_p3_migration.P3OperationalDocumentationTests -v
~~~

Expected GREEN：logical fingerprints、FTS corruption detection和完整operational contract全绿。

**Exit gate:** 当前用户已授权在 verified backup、restore-check、副本升级/降级、writer drain 与内容 hash 全绿后执行路线图所需的 Live additive migration；不得把连续执行授权解释为跳过这些前置门禁。

---

## 恢复副本 migration、guarded downgrade 与 re-upgrade 演练

获得运行Live backup的明确授权后，用CLI JSON返回值传递精确路径，不手写恢复路径：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
function Assert-LegacyFingerprintMap {
  param([Parameter(Mandatory = $true)]$Fingerprint, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Tables)
  foreach ($table in $Tables) {
    if (-not ($Fingerprint.tableCounts.PSObject.Properties.Name -contains $table)) { throw "$Label count map is missing legacy table $table." }
    if (-not ($Fingerprint.tableSha256.PSObject.Properties.Name -contains $table)) { throw "$Label hash map is missing legacy table $table." }
  }
}
function Assert-LegacyFingerprintEqual {
  param([Parameter(Mandatory = $true)]$Expected, [Parameter(Mandatory = $true)]$Actual, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Tables)
  Assert-LegacyFingerprintMap $Expected 'baseline' $Tables
  Assert-LegacyFingerprintMap $Actual $Label $Tables
  foreach ($table in $Tables) {
    if ($Expected.tableCounts.$table -ne $Actual.tableCounts.$table) { throw "$Label changed legacy count for $table." }
    if ($Expected.tableSha256.$table -ne $Actual.tableSha256.$table) { throw "$Label changed legacy hash for $table." }
  }
}
function Assert-StableContentFingerprintMap {
  param([Parameter(Mandatory = $true)]$Fingerprint, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Keys)
  foreach ($key in $Keys) {
    if (-not ($Fingerprint.contentCounts.PSObject.Properties.Name -contains $key)) { throw "$Label content count map is missing stable key $key." }
    if (-not ($Fingerprint.contentSha256.PSObject.Properties.Name -contains $key)) { throw "$Label content hash map is missing stable key $key." }
  }
}
function Assert-StableContentFingerprintEqual {
  param([Parameter(Mandatory = $true)]$Expected, [Parameter(Mandatory = $true)]$Actual, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Keys)
  Assert-StableContentFingerprintMap $Expected 'baseline' $Keys
  Assert-StableContentFingerprintMap $Actual $Label $Keys
  foreach ($key in $Keys) {
    if ($Expected.contentCounts.$key -ne $Actual.contentCounts.$key) { throw "$Label changed stable content count for $key." }
    if ($Expected.contentSha256.$key -ne $Actual.contentSha256.$key) { throw "$Label changed stable content hash for $key." }
  }
}
$p3LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
$p3StableContentKeys = @('paperIds','explainers','translations','notes','paperVectors','documentSources','generatedArtifacts','processingJobs')
$p3Create = Invoke-CheckedNative 'P3 backup create' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create `
  --database data/app.db `
  --output-directory data/backups `
  --label pre-p3-source-consumers } | ConvertFrom-Json
if (-not $p3Create.ok) { throw 'P3 backup create did not return ok=true.' }

$p3Verify = Invoke-CheckedNative 'P3 backup verify' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify `
  --backup $p3Create.backupPath `
  --manifest $p3Create.manifestPath } | ConvertFrom-Json
if (-not $p3Verify.ok) { throw 'P3 backup verify did not return ok=true.' }
if ($p3Verify.logicalSha256 -ne $p3Create.logicalSha256) {
  throw 'P3 create/verify logical SHA-256 mismatch.'
}

$p3Restore = Invoke-CheckedNative 'P3 restore-check' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check `
  --backup $p3Create.backupPath `
  --manifest $p3Create.manifestPath `
  --output-directory data/backups/restore-checks } | ConvertFrom-Json
if (-not $p3Restore.ok) { throw 'P3 restore-check did not return ok=true.' }
if ($p3Restore.logicalSha256 -ne $p3Verify.logicalSha256) {
  throw 'P3 verify/restore logical SHA-256 mismatch.'
}

$p3DrillDb = (Resolve-Path -LiteralPath $p3Restore.restoredPath).Path
$p3LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
if ($p3DrillDb -eq $p3LiveDb) { throw 'P3 drill resolved to Live database.' }
$p3RestoreRoot = (Resolve-Path -LiteralPath 'data/backups/restore-checks').Path
$p3RestorePrefix = $p3RestoreRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$p3ValidationDir = Split-Path -Parent $p3DrillDb
if (-not $p3DrillDb.StartsWith($p3RestorePrefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Split-Path -Leaf $p3ValidationDir).StartsWith('restore-validation-', [StringComparison]::Ordinal)) {
  throw 'P3 drill database is outside restore-check containment.'
}

$p3PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p3HadDbPath = $null -ne $p3PreviousDbPath
$p3PreviousRestoreRoot = [Environment]::GetEnvironmentVariable('MIGRATION_RESTORE_ROOT', 'Process')
$p3HadRestoreRoot = $null -ne $p3PreviousRestoreRoot
$env:DB_PATH = $p3DrillDb
$env:MIGRATION_RESTORE_ROOT = $p3RestoreRoot
try {
  $p3Before = Invoke-CheckedNative 'P3 pre-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3DrillDb } | ConvertFrom-Json
  if (-not $p3Before.ok -or $p3Before.alembicVersion -ne '20260807_02') { throw 'P3 drill must start at exact revision 20260807_02.' }
  Assert-LegacyFingerprintMap $p3Before 'P3 pre-upgrade' $p3LegacyTables
  Assert-StableContentFingerprintMap $p3Before 'P3 pre-upgrade' $p3StableContentKeys

  Invoke-CheckedNative 'P3 restored-copy upgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03 }
  Invoke-CheckedNative 'P3 restored-copy validation' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p3_migration.P3RestoredCopyValidationTests -v }
  $p3AfterUpgrade = Invoke-CheckedNative 'P3 post-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3DrillDb } | ConvertFrom-Json
  if (-not $p3AfterUpgrade.ok -or $p3AfterUpgrade.alembicVersion -ne '20260807_03') { throw 'P3 post-upgrade fingerprint is not at 20260807_03.' }
  Assert-LegacyFingerprintEqual $p3Before $p3AfterUpgrade 'P3 upgrade' $p3LegacyTables
  Assert-StableContentFingerprintEqual $p3Before $p3AfterUpgrade 'P3 upgrade' $p3StableContentKeys

  $p3DowngradeOutput = & .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_02 2>&1
  $p3DowngradeExit = $LASTEXITCODE
  if ($p3DowngradeExit -ne 0) {
    $p3DowngradeText = $p3DowngradeOutput -join [Environment]::NewLine
    if ($p3DowngradeText -notmatch 'P3_DOWNGRADE_BLOCKED_NONEMPTY') {
      throw 'P3 downgrade failed for an unexpected reason.'
    }
    Invoke-CheckedNative 'explicit isolated P3 downgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p3_data_loss=true downgrade 20260807_02 }
  }

  Invoke-CheckedNative 'P2 validation after P3 downgrade' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration.P2RestoredCopyValidationTests -v }
  $p3AfterDowngrade = Invoke-CheckedNative 'P3 post-downgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3DrillDb } | ConvertFrom-Json
  if (-not $p3AfterDowngrade.ok -or $p3AfterDowngrade.alembicVersion -ne '20260807_02') { throw 'P3 downgrade did not return to 20260807_02.' }
  Assert-LegacyFingerprintEqual $p3Before $p3AfterDowngrade 'P3 downgrade' $p3LegacyTables
  Assert-StableContentFingerprintEqual $p3Before $p3AfterDowngrade 'P3 downgrade' $p3StableContentKeys

  Invoke-CheckedNative 'P3 restored-copy re-upgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03 }
  Invoke-CheckedNative 'P3 restored-copy re-upgrade validation' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p3_migration.P3RestoredCopyValidationTests -v }
  $p3AfterReupgrade = Invoke-CheckedNative 'P3 post-re-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3DrillDb } | ConvertFrom-Json
  if (-not $p3AfterReupgrade.ok -or $p3AfterReupgrade.alembicVersion -ne '20260807_03') { throw 'P3 re-upgrade did not return to 20260807_03.' }
  Assert-LegacyFingerprintEqual $p3Before $p3AfterReupgrade 'P3 re-upgrade' $p3LegacyTables
  Assert-StableContentFingerprintEqual $p3Before $p3AfterReupgrade 'P3 re-upgrade' $p3StableContentKeys
} finally {
  if ($p3HadDbPath) { $env:DB_PATH = $p3PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
  if ($p3HadRestoreRoot) { $env:MIGRATION_RESTORE_ROOT = $p3PreviousRestoreRoot } else { Remove-Item Env:MIGRATION_RESTORE_ROOT -ErrorAction SilentlyContinue }
}
~~~

Expected：create/verify/restore logical hashes一致；P2→P3 upgrade、P3 validators、guarded/explicit isolated downgrade、P2 validators、re-upgrade全绿；冻结的 12 张 legacy 表 `papers|progress|paper_reviews|notes|favorites|translations|paper_vectors|cite_edges|ingest_jobs|job_candidates|job_schedules|schema_migrations` 的 table count/hash，以及 P2 stable content keys `paperIds|explainers|translations|notes|paperVectors|documentSources|generatedArtifacts|processingJobs` 的 content count/hash，每一步都存在且一致。P3 预期新增或变化的 `documentChunks|chunkEmbeddings|translationCheckpoints` 不进入相等比较。绝不对Live使用 `allow_p3_data_loss=true`。

---

## Gate-authorized Live additive upgrade

恢复副本演练、P3 全量测试、fresh backup、writer drain 和规格/质量审查全部有最新绿灯证据后，才把 Live 从 P2 head 显式推进到 P3 final head。命令固定并验证 Live 路径；迁移前后比较 legacy 表与 P2 stable content keys 的 count/hash，并运行 P3 validators：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p3LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p3LiveBefore = .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3LiveDb | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $p3LiveBefore.ok -or $p3LiveBefore.alembicVersion -ne '20260807_02') { throw 'Live database is not at the P3 base revision 20260807_02.' }
$p3LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
$p3StableContentKeys = @('paperIds','explainers','translations','notes','paperVectors','documentSources','generatedArtifacts','processingJobs')
foreach ($p3Table in $p3LegacyTables) {
  if (-not ($p3LiveBefore.tableCounts.PSObject.Properties.Name -contains $p3Table)) { throw "Live P3 pre-upgrade count map is missing legacy table $p3Table." }
  if (-not ($p3LiveBefore.tableSha256.PSObject.Properties.Name -contains $p3Table)) { throw "Live P3 pre-upgrade hash map is missing legacy table $p3Table." }
}
foreach ($p3Key in $p3StableContentKeys) {
  if (-not ($p3LiveBefore.contentCounts.PSObject.Properties.Name -contains $p3Key)) { throw "Live P3 pre-upgrade content count map is missing stable key $p3Key." }
  if (-not ($p3LiveBefore.contentSha256.PSObject.Properties.Name -contains $p3Key)) { throw "Live P3 pre-upgrade content hash map is missing stable key $p3Key." }
}
$p3PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p3HadDbPath = $null -ne $p3PreviousDbPath
$env:DB_PATH = $p3LiveDb
try {
  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_03
  if ($LASTEXITCODE -ne 0) { throw 'Live P3 additive upgrade failed.' }
  $p3CurrentRaw = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current)
  $p3CurrentExit = $LASTEXITCODE
  if ($p3CurrentExit -ne 0) { throw 'Live Alembic current inspection failed.' }
  $p3Current = @($p3CurrentRaw | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
  if ($p3Current.Count -ne 1 -or [string]$p3Current[0] -notmatch '^20260807_03\s+\(head\)$') { throw 'Live Alembic current is not uniquely 20260807_03 (head).' }
  $p3LiveAfter = .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p3LiveDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p3LiveAfter.ok -or $p3LiveAfter.alembicVersion -ne '20260807_03') { throw 'Live fingerprint did not reach 20260807_03.' }
  foreach ($p3Table in $p3LegacyTables) {
    if (-not ($p3LiveAfter.tableCounts.PSObject.Properties.Name -contains $p3Table)) { throw "Live P3 post-upgrade count map is missing legacy table $p3Table." }
    if (-not ($p3LiveAfter.tableSha256.PSObject.Properties.Name -contains $p3Table)) { throw "Live P3 post-upgrade hash map is missing legacy table $p3Table." }
    if ($p3LiveBefore.tableCounts.$p3Table -ne $p3LiveAfter.tableCounts.$p3Table) { throw "Live P3 changed legacy count for $p3Table." }
    if ($p3LiveBefore.tableSha256.$p3Table -ne $p3LiveAfter.tableSha256.$p3Table) { throw "Live P3 changed legacy hash for $p3Table." }
  }
  foreach ($p3Key in $p3StableContentKeys) {
    if (-not ($p3LiveAfter.contentCounts.PSObject.Properties.Name -contains $p3Key)) { throw "Live P3 post-upgrade content count map is missing stable key $p3Key." }
    if (-not ($p3LiveAfter.contentSha256.PSObject.Properties.Name -contains $p3Key)) { throw "Live P3 post-upgrade content hash map is missing stable key $p3Key." }
    if ($p3LiveBefore.contentCounts.$p3Key -ne $p3LiveAfter.contentCounts.$p3Key) { throw "Live P3 changed stable content count for $p3Key." }
    if ($p3LiveBefore.contentSha256.$p3Key -ne $p3LiveAfter.contentSha256.$p3Key) { throw "Live P3 changed stable content hash for $p3Key." }
  }
  foreach ($p3RequiredTable in @('document_sources','generated_artifacts','processing_jobs','document_chunks','obsidian_exports','paper_artifact_heads','processing_job_events','ocr_page_checkpoints','document_chunk_embeddings','artifact_translation_checkpoints')) {
    if (-not ($p3LiveAfter.tableCounts.PSObject.Properties.Name -contains $p3RequiredTable)) { throw "Live P3 schema inventory is missing $p3RequiredTable." }
  }
  if ($p3LiveAfter.quickCheck -ne 'ok' -or $p3LiveAfter.integrityCheck -ne 'ok' -or $p3LiveAfter.foreignKeyViolations -ne 0) { throw 'Live P3 read-only health validation failed.' }
} finally {
  if ($p3HadDbPath) { $env:DB_PATH = $p3PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected：Live 从唯一 head `20260807_02` 前进到唯一 final head `20260807_03`；只读 inspect 的 schema inventory/健康检查、冻结 12 张 legacy 表的 table count/hash 与 P2 stable content keys `paperIds|explainers|translations|notes|paperVectors|documentSources|generatedArtifacts|processingJobs` 的 content count/hash 全部存在且一致，任一缺键或 hash 漂移都阻止阶段出口。P3 预期新增或变化的 `documentChunks|chunkEmbeddings|translationCheckpoints` 不进入相等比较。`P3RestoredCopyValidationTests` 只在 `$p3DrillDb` 运行，绝不以 Live `DB_PATH` 运行。P4–P6 不新增 revision；运行时回滚保留 P3 schema，前进到 P4 前不得对 Live 使用 destructive downgrade。

---

## Runtime rollback

先停止新command enqueue，再让Worker完成或显式取消running translate/explain/embed jobs，停止Worker和FastAPI writer。设置并重启：

~~~text
API_BACKEND_MODE=legacy
DOCUMENT_PIPELINE_MODE=legacy
GENERATION_PIPELINE_MODE=legacy
ARTIFACT_READ_MODE=legacy
ARTIFACT_WRITE_MODE=legacy
OCR_ENABLED=0
OBSIDIAN_ENABLED=0
~~~

保留P3 additive columns、FTS、chunks、vectors/checkpoints；legacy Node/Python继续读 `papers`、`translations`、`paper_vectors`。Runtime rollback不运行downgrade、不删除模型cache、不清stale rows。只有恢复副本destructive downgrade与exact P0 snapshot restore均验证、且确认没有唯一P3 data时才安排停机schema downgrade；否则保留schema或从snapshot离线恢复并明确snapshot后数据丢失。

---

## 阶段门禁

- [ ] P0 verified snapshot路径/hash和P1/P2恢复副本证据已记录。
- [ ] P2 head `20260807_02`、queue/API/events/retry、strict native/OCR、Fake OCR slice全绿。
- [ ] P3 upgrade只add columns/aux objects，final single head为 `20260807_03`。
- [ ] writer drain 后 Live 显式 upgrade 到 `20260807_03`；`alembic current`、read-only inspect/schema inventory 与 legacy count/hash 门禁全绿；restored-copy test 从未指向 Live。
- [ ] deterministic chunks对所有fixtures满足byte-for-byte join、stable identity、text 1600 cap和atomic fence规则。
- [ ] ContextBuilder translation/embedding全覆盖；summary/explain eligible覆盖；classification/metadata bounded selection全绿。
- [ ] P2 explainer已迁到heading priority/section summaries，tail eligible sentinel可达且references/ack排除。
- [ ] translation resume不重复成功chunk，最终artifact/head/legacy/job原子。
- [ ] classification/metadata/summary只消费对应ContextPlan且structured projection allowlist全绿。
- [ ] PDF/provider/model/options/processing version stale cascade与mode coexistence全绿。
- [ ] FTS external-content triggers/rebuild/integrity、heading/page provenance和stale filters全绿。
- [ ] embeddings exact profile、resume、vector validation/redaction全绿。
- [ ] lexical/semantic/hybrid query均read-only；缺/stale embeddings不触发document re-embed。
- [ ] FastAPI command/search contracts与Worker canonical job dispatch全绿。
- [ ] React只改Gateway/Hook seam，`public/`、JSX/CSS/文案/布局零diff。
- [ ] backup fingerprint/FTS corruption detection、restored-copy downgrade/re-upgrade和runtime rollback runbook全绿。
- [ ] 规格审查后再做connection/lock/TOCTOU/path/secret/error/test-effectiveness质量审查；修复后重复targeted/full suites。

---

## 最终验证

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-CheckedNative {
  param([Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][scriptblock]$Command)
  $output = & $Command
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
  $output
}
$env:PYTHONDONTWRITEBYTECODE = '1'
Invoke-CheckedNative 'P3 migration tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p3_migration -v }
Invoke-CheckedNative 'context builder tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_context_builder -v }
Invoke-CheckedNative 'source freshness tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_freshness -v }
Invoke-CheckedNative 'translation resume tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_translation_resume -v }
Invoke-CheckedNative 'document artifact tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_artifacts -v }
Invoke-CheckedNative 'FTS search tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_fts_search -v }
Invoke-CheckedNative 'chunk embedding tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_chunk_embeddings -v }
Invoke-CheckedNative 'document search API tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_document_search_api -v }
Invoke-CheckedNative 'database backup tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup -v }
Invoke-CheckedNative 'backend full suite' { .\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v }
Invoke-CheckedNative 'legacy Python full suite' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v }
Invoke-CheckedNative 'MCP characterization' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v }
Invoke-CheckedNative 'root Node tests' { npm.cmd test }
Invoke-CheckedNative 'P3 frontend targeted tests' { npm.cmd run test:run --prefix frontend -- src/lib/api/insightsGateway.test.ts src/features/reader/ArtifactPanel.test.tsx }
$p3ExitBaselineJson = Invoke-CheckedNative 'P3 exit full frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$p3ExitBaseline = $p3ExitBaselineJson | ConvertFrom-Json
$p3ExitBaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p3ExitBaselineField in $p3ExitBaselineRequiredFields) {
  if (-not ($p3ExitBaseline.PSObject.Properties.Name -contains $p3ExitBaselineField)) { throw "P3 exit baseline verifier omitted required field $p3ExitBaselineField." }
}
if ($p3ExitBaseline.baselineMatched -isnot [bool] -or $p3ExitBaseline.baselineMatched -ne $true) { throw 'P3 exit baseline verifier did not report boolean baselineMatched=true.' }
if ($null -eq $p3ExitBaseline.observedSuiteExitCode -or ($p3ExitBaseline.observedSuiteExitCode -isnot [int] -and $p3ExitBaseline.observedSuiteExitCode -isnot [long])) { throw 'P3 exit baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p3ExitBaseline.overallGreen -isnot [bool]) { throw 'P3 exit baseline verifier did not report boolean overallGreen.' }
$p3ObservedSuiteExitCode = [long]$p3ExitBaseline.observedSuiteExitCode
if (($p3ObservedSuiteExitCode -eq 0) -ne $p3ExitBaseline.overallGreen) { throw 'P3 exit baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
Invoke-CheckedNative 'frontend lint' { npm.cmd run lint --prefix frontend }
Invoke-CheckedNative 'frontend typecheck' { npm.cmd run typecheck --prefix frontend }
Invoke-CheckedNative 'frontend build' { npm.cmd run build --prefix frontend }
Invoke-CheckedNative 'frontend e2e' { npm.cmd run e2e --prefix frontend }
$p3HeadLines = @(Invoke-CheckedNative 'P3 Alembic heads' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini heads } | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
if ($p3HeadLines.Count -ne 1 -or $p3HeadLines[0] -notmatch '^20260807_03\s+\(head\)$') { throw 'P3 migration graph must expose exactly one 20260807_03 (head).' }
Invoke-CheckedNative 'P3 git diff check' { git diff --check }
~~~

Expected：

- 所有targeted/backend/legacy/Node与targeted React tests 0 failures、0 errors、0 unexpected skips；full frontend suite 只通过 P0.1 exact verifier 执行。
- P0.1 verifier process exit 0、`baselineMatched=true`，并报告整数 `observedSuiteExitCode`；raw 0 必须对应 `overallGreen=true`，exact-match reviewed raw non-zero 必须保留该非零值并对应 `overallGreen=false`。
- lint/typecheck/build/E2E成功；test Fake/secret/model cache不进入production bundle。
- Alembic输出恰好一行 `20260807_03 (head)`。
- `git diff --check` 无error；`git diff -- public frontend/src/features/reader/ArtifactPanel.tsx frontend/src/features/reader/*.css` 为空。
- 实施报告记录exact test counts、FTS5 capability/integrity、context selection coverage、embedding coverage/stale counts、migration hash和任何未执行项。全部 P3 门禁为绿时连续进入 P4；失败时停止推进并保持 runtime rollback。

---

## 自审清单

- [ ] 没有Git add/commit/push/branch步骤。
- [ ] 12个任务各有RED、同一targeted command确认RED、最小实现、同一command GREEN。
- [ ] explainer/translation/classification/metadata/summary/search/embedding都只消费ready SourceDocument/chunks。
- [ ] translation/embedding覆盖全部chunks；summary/explain覆盖全部eligible sections；classification/metadata只发送明确bounded categories。
- [ ] 没有PDF reopen、legacy abstract fallback、prefix truncation或eligible tail omission。
- [ ] document_chunks由P1创建，P3只additive扩展；revision/down_revision和final head准确。
- [ ] FTS5是external-content并有insert/delete/update triggers、rebuild、integrity和stale join filter。
- [ ] chunk embedding有exact provider/model/version identity、finite float32 validation与explicit stale。
- [ ] query path不materialize chunks、不re-embed documents、不repair stale、不写DB。
- [ ] translation checkpoint可恢复且成功chunk不重复。
- [ ] migration/downgrade/runtime rollback/backup fingerprint均有具体命令、guard与停止条件。
- [ ] 所有章节、命令、测试矩阵与实现边界均完整具体，不含截断或延后实现标记。
