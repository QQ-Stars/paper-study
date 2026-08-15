# P2 SQLite Processing Queue、显式 OCR Source 与 Explainer 纵向切片实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development for every behavior and verification-before-completion before any completion claim. Execute every checkbox in order. This plan deliberately contains no Git commit, push, branch, or staging step.

**Goal:** 在不改变现有页面结构和交互控件的前提下，交付一个重启可恢复、SQLite 持久化、可观测且幂等的 ProcessingQueue；用显式 `sourceMode=native|ocr` 建立 `document_sources`，并完成 `ready SourceDocument -> generated_artifacts(kind=explainer) -> papers.explainer` 的最小纵向切片。

**Architecture:** `ProcessingQueue` 是唯一任务状态深模块，负责 enqueue、lease、heartbeat、retry、cancel 和 terminal settle；`SourceDocumentProcessor` 只按调用者明确选择的 source mode 调用一个 extractor；`OcrProvider` 是 OCR seam，测试使用 `FakeOcrProvider`，生产 registry 在缺少经核验供应商合同时同步拒绝请求。`ArtifactGenerator` 只接收 ready `SourceDocument`，不再自行打开 PDF。P1 已创建五张主表，P2 repositories 只增加 queue/cache/lease 能力并在事务内同步 `processing_jobs`、`document_sources`、`generated_artifacts` 和 legacy `papers.explainer`。FastAPI route 只做 DTO 校验和 Interface 调用，React Gateway/Hook 只适配新 job 协议，不新增或重排 UI。

**Tech Stack:** Python 3、FastAPI/Pydantic（P1 composition root）、SQLAlchemy 2 async/aiosqlite、Alembic revision `20260807_02`、SQLite WAL、Python `unittest.IsolatedAsyncioTestCase`、React 19、TypeScript、TanStack Query、Vitest。

**Depends on:** P0 verified backup/restore-check；P1 `backend/alembic.ini`、`backend/migrations/env.py`、`backend/migrations/versions/20260807_01_domain_data_foundation.py`、`backend/app/bootstrap.py`、统一 `{error:{code,message,details}}` 错误 DTO、`/api/v2` router 和 test app factory。P1 未满足出口门禁时不得开始 P2 migration 或 HTTP route。完整 frontend suite 若为已审核 non-zero，必须在 P2 入口与出口由 P0.1 exact verifier 重新证明 IDs/signatures/related hashes 未变、相关路径未触碰，并继续报告 raw non-zero 与 `overallGreen=false`。

**Workspace constraints:** 不修改 `public/`；不改任何 JSX、CSS、按钮文本或布局；不读写真实 `data/app.db` 作为测试夹具；不自动运行 live migration；本主计划不直接创建真实 DeepSeek OCR Adapter，条件实现只按 `docs/superpowers/plans/2026-08-08-p2-deepseek-ocr-adapter-conditional.md` 执行；不把 OCR 设计成 native 失败后的回退；不在请求线程执行 OCR、讲解或重试循环。

---

## 不可协商的 source mode 语义

| 请求 | 唯一允许调用 | 明确禁止 | 失败结果 |
|---|---|---|---|
| `sourceMode="native"` | P1 `NativeExtractor.extract()` | `OcrProvider`、provider registry、任何外部网络 | source/job 进入 failed；错误码 `NATIVE_EXTRACTION_FAILED` 或 `NATIVE_TEXT_EMPTY` |
| `sourceMode="ocr"` | OCR_ENABLED=1且请求显式指定contract-verified provider的 `OcrProvider.extract_batch()` | `NativePdfExtractor`、PyMuPDF文本回退、其他OCR provider | disabled时同步拒绝；运行失败按typed contract分类；绝不退回native |

`OCR_ENABLED` 沿用P0 immutable startup setting，默认0。sourceMode=ocr且值为0时先返回409 `OCR_DISABLED`：registry/provider/transport构造、PDF upload、source/job/checkpoint写入全为零。只有显式设置1并重启后才继续provider/model/options验证；Fake纵向测试必须在test bootstrap显式启用。Native携带ocrProvider/model返回422；enabled OCR缺provider/model返回对应422。Production registry不暴露fake。

仓库目前只有普通 LLM chat-completions 配置，没有可据此推断的 DeepSeek OCR 协议。因此主线正式交付状态是：`ocrProvider="deepseek"` 在创建 source/job 前同步返回 HTTP 503 `OCR_PROVIDER_CONTRACT_UNVERIFIED`，数据库零写入，transport 零构造/调用。完整资料 gate 与逐行为 TDD 位于 `docs/superpowers/plans/2026-08-08-p2-deepseek-ocr-adapter-conditional.md`：资料未齐时默认不执行且 P2 Fake slice 可完成；若用户在项目完成前提供完整官方资料，该条件计划变为真实 Adapter 完成的必执行 gate。

---

## 公共 Interface

### ProcessingQueue

```python
class ProcessingQueue(Protocol):
    async def enqueue(self, spec: JobSpec, *, now: datetime) -> EnqueueResult:
        raise NotImplementedError

    async def get(self, job_id: str) -> ProcessingJob | None:
        raise NotImplementedError

    async def cancel(self, job_id: str, *, now: datetime) -> ProcessingJob:
        raise NotImplementedError

    async def retry(self, job_id: str, *, now: datetime) -> EnqueueResult:
        raise NotImplementedError

    async def list(self, query: JobListQuery) -> Page[ProcessingJob]:
        raise NotImplementedError

    async def list_events(self, job_id: str, query: JobEventListQuery) -> Page[ProcessingJobEvent]:
        raise NotImplementedError

    async def claim_next(self, *, worker_id: str, now: datetime, lease_seconds: int) -> JobLease | None:
        raise NotImplementedError

    async def report_progress(self, lease: JobLease, progress: JobProgress, *, now: datetime) -> None:
        raise NotImplementedError

    async def complete(self, lease: JobLease, result: JobResult, *, now: datetime) -> ProcessingJob:
        raise NotImplementedError

    async def fail(self, lease: JobLease, failure: JobFailure, *, now: datetime) -> ProcessingJob:
        raise NotImplementedError
```

`JobSpec` 必须先由唯一的 `encode_job_spec_v1()` 编码成 canonical UTF-8 JSON，才允许进入 repository。`progress_json` 只保存可丢弃的安全进度，绝不作为 worker 重建请求的来源。持久化 envelope 固定为：

```json
{
  "arguments": {},
  "jobType": "source_materialize",
  "paperId": "paper-1",
  "schemaVersion": 1,
  "sourceMode": "native",
  "target": {"artifactId": null, "sourceDocumentId": "src_01"}
}
```

顶层 key 必须恰为上面六项，按 Unicode code point 排序并用 compact separators 编码；`arguments` 与 `target` 的允许 key 由每个 `jobType` 的 Pydantic discriminated model 冻结，unknown/missing/wrong type、重复 JSON key、非 canonical bytes、未知 `schemaVersion` 或超过 4 MiB 都以 `JOB_SPEC_INVALID` fail closed。P2 的 `source_materialize|ocr|explain` arguments 分别保存执行所需的 processing version、OCR provider/model/options/page batch policy，或 generator profile/provider/model/prompt version；不得保存 API Key、Authorization/Cookie、credential material、PDF/Markdown/prompt bytes、provider raw request/response、lease token或任意 header。P3–P5 只能向同一个 versioned union 增加明确 variant，不能另建 payload 列或借用 `progress_json`。

该union另有一个migration-only `LegacyImportedJobSpecV1`：`arguments`恰为`{"legacyImported":true}`，保留row原有jobType/paperId/sourceMode/nullable target binding，但永远不可由application enqueue、claim dispatch或explicit retry创建。它只让P1 nonempty rows可无损backfill/inspect；worker遇到它以`JOB_SPEC_UNRECOVERABLE`终止而不猜参。下文“idempotency绑定spec SHA”适用于全部P2+新建/重试job；保留原idempotency key的P1 legacy-import row是只读迁移例外。

Repository Interface 也固定 raw/canonical 边界；同一个 SQLAlchemy adapter 同时实现它与上面的 `ProcessingQueue`，不建立第二套 queue：

```python
@dataclass(frozen=True)
class StoredJobSpec:
    value: JobSpecV1
    raw_json: str
    sha256: str


class ProcessingJobRepository(Protocol):
    async def insert_with_spec(
        self,
        job: NewProcessingJob,
        *,
        spec_json: str,
        spec_sha256: str,
    ) -> EnqueueResult:
        raise NotImplementedError

    async def load_spec(self, job_id: str) -> StoredJobSpec:
        raise NotImplementedError

    async def copy_spec_for_retry(
        self,
        parent_job_id: str,
        descendant: NewProcessingJob,
    ) -> EnqueueResult:
        raise NotImplementedError
```

`insert_with_spec` 重新严格 decode、重新 canonical encode，并要求传入 bytes/hash 完全一致；`load_spec` 也执行相同检查。`claim_next` 只能在成功 strict decode 后 transition，并把 immutable `StoredJobSpec` 放入 `JobLease`；无效存储 row 保持原样且返回 typed storage-corruption failure，不允许 worker 猜参数。automatic retry/orphan recovery 复用同一 row，必须保持 `spec_json` bytes 不变；explicit retry 只能由 `copy_spec_for_retry` 在事务内把 parent `spec_json` 逐字节复制给 descendant，再重新验证相同 SHA。任何路径都不得从 `progress_json`、当前 Settings 或 target row 拼回丢失参数。

Public `ProcessingJob.status` 只允许 `queued|running|succeeded|failed|cancelled`。重试等待仍是 `queued`，由 `available_at` 控制；running orphan 由 `lease_expires_at` 判定。不得增加 public `blocked`、`retry_wait`、`superseded` 或 `orphaned` 状态。

repository 内部可以把 `claim_next/report_progress/complete` 映射为 claim/heartbeat/succeed helper，但 application、worker 和 test doubles 只依赖上面这组公开名称。`retry` 是用户显式动作：原 terminal job 保持不可变，创建带 `retry_of_job_id/retry_sequence` 的新 queued job并把仍有效的 target 原子置回 queued；同一 terminal parent 同时最多一个 queued/running descendant，竞争 retry 返回同一 descendant。自动 backoff 不调用 `retry`，仍在当前 job 内使用 `queued + available_at`。

### OcrProvider

```python
@dataclass(frozen=True)
class OcrRequest:
    source_id: str
    paper_id: str
    pdf_bytes: bytes
    pdf_sha256: str
    media_type: str
    model: str
    options: Mapping[str, JsonValue]
    page_numbers: Sequence[int]
    total_pages: int


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    markdown: str
    content_sha256: str
    provider_page_id: str | None


@dataclass(frozen=True)
class OcrResult:
    pages: Sequence[OcrPageResult]
    provider: str
    model: str
    processing_version: str
    provider_request_id: str | None


class OcrProvider(Protocol):
    provider_id: str

    async def extract_batch(self, request: OcrRequest) -> OcrResult:
        raise NotImplementedError
```

`FakeOcrProvider` 接收按page number固定的结果或typed failure schedule，记录每个batch request/call。它只由dependency override注入测试app/worker，不得进入production registry。OCR options canonical defaults为 `pageBatchSize=1`、`maxConcurrency=1`；pageBatchSize允许1–16，maxConcurrency允许1–4。Worker只调用 `extract_batch`，每个成功page先写 `ocr_page_checkpoints`；重试只调missing/failed pages，按page_number组装最终markdown。Cancel停止提交新batch，已in-flight结果只有lease仍有效时才checkpoint。

Provider Adapter负责把HTTP `Retry-After` delta-seconds或HTTP-date归一成 `retry_after_seconds: int | None`；Worker从不解析header，只接受归一秒数。缺失/非法值为None，负值按0，超过900秒clamp为900。`OCR_RATE_LIMITED` 的下一次available_at是 `now + max(exponential_backoff_seconds, retry_after_seconds_or_zero)`，最终delay上限900秒。

### SourceDocumentProcessor 与 ArtifactGenerator

```python
class SourceDocumentProcessor:
    async def process(self, lease: JobLease, source_id: str) -> SourceDocument:
        raise NotImplementedError


class ArtifactGenerator:
    async def generate_explainer(self, lease: JobLease, source_id: str) -> GeneratedArtifact:
        raise NotImplementedError
```

`SourceDocumentProcessor.process()` 首先读取持久化 source row 的 `mode`，然后使用完全分离的分支。native 分支的对象图中不注入 OCR registry；OCR 分支的对象图中不注入 native extractor。`ArtifactGenerator` 查询 `document_sources.status='ready'` 且校验当前 PDF SHA，传给 LLM 的正文只能来自 `document_sources.markdown`。

---

## API 契约

### `POST /api/v2/papers/{paper_id}/sources`

Native body：

```json
{"sourceMode":"native"}
```

OCR body：

```json
{"sourceMode":"ocr","ocrProvider":"fake","ocrModel":"fake-ocr-v1","options":{"pageBatchSize":1,"maxConcurrency":1}}
```

该OCR 202示例只适用于test app通过dependency override注入Fake；production registry绝不注册 `fake`。Production中任何显式命名 `deepseek` 且携带任意nonblank client model的请求都在建row前返回503 `OCR_PROVIDER_CONTRACT_UNVERIFIED`；本计划不提供虚构DeepSeek model示例。

成功为 HTTP 202：

```json
{
  "source": {"id":"src_01","paperId":"paper-1","mode":"native","status":"queued"},
  "job": {"id":"job_01","paperId":"paper-1","jobType":"source_materialize","sourceMode":"native","status":"queued"},
  "deduplicated": false
}
```

同一幂等键重复 POST 返回相同 source/job，仍为 202，`deduplicated=true`。

### `POST /api/v2/papers/{paper_id}/artifacts/explainer`

Body：

```json
{"sourceMode":"native","sourceDocumentId":"src_01","profile":"deep"}
```

profile只允许 `standard|deep`，省略为standard，并解析成不同prompt_version/cache identity以保留现有deep workflow。仅ready、同paper、未stale且source.mode与camelCase sourceMode相同的source可入队；mode mismatch为422。成功202返回artifact/job/deduplicated；not ready/stale为409。

### `/api/v2/jobs`

- `GET /api/v2/papers/{paper_id}/sources?limit=50&cursor=cursor_01` 返回该 paper 的 sources，按 `created_at DESC,id DESC`，shape 为 `{items,nextCursor}`。
- `GET /api/v2/papers/{paper_id}/artifacts?kind=explainer&limit=50&cursor=cursor_01` 返回该 paper 的 artifacts；kind 可省略，shape 为 `{items,nextCursor}`。
- `GET /api/v2/jobs?paperId=paper-1&status=queued&jobType=source_materialize&limit=50&cursor=cursor_01` 返回安全 job summaries；filters 可省略，稳定排序为 `created_at DESC,id DESC`。
- `GET /api/v2/jobs/{job_id}` 返回 `id,paperId,jobType,sourceMode,status,progress,attempt,maxAttempts,error,createdAt,startedAt,finishedAt,cancelledAt`；永不返回内部 `spec_json`、spec SHA、settings snapshot 或 target binding。
- `GET /api/v2/jobs/{job_id}/events?afterSequence=0&limit=100` 返回 `{items,nextAfterSequence}`；event 只含 sequence/type/progress/error/createdAt，不含 lease token、正文或 raw provider payload。
- `POST /api/v2/jobs/{job_id}/cancel` 原子取消 queued job；running job 设置 cancel request，由 worker 在 checkpoint settle cancelled。
- `POST /api/v2/jobs/{job_id}/retry` 只接受 failed/cancelled job；原 job 不变，原子创建或返回一个 active retry descendant，成功为 202 `{job,retriedFromJobId,deduplicated}`。succeeded/running/queued、stale target 或 non-retryable error 返回 409 `JOB_NOT_RETRYABLE`。
- 错误正文不含 API Key、Authorization header、PDF 正文或供应商原始 response body。

---

## SQLite Schema 契约

Migration revision 固定：

```python
revision = "20260807_02"
down_revision = "20260807_01"
```

P1 revision `20260807_01` 已经创建 `document_sources`、`generated_artifacts`、`processing_jobs`、`document_chunks`、`obsidian_exports` 以及下列 hard fields/checks。P2 migration 首先断言五表存在；缺一张就以 `P2_BASE_SCHEMA_MISSING` 失败且零 DDL。P2 不重复 CREATE、不重命名、不删除这五张表，也不修改 P1 canonical job_type CHECK。

### `document_sources`

必须保留以下列：

```text
id TEXT PRIMARY KEY
paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE
mode TEXT NOT NULL CHECK(mode IN ('native','ocr'))
status TEXT NOT NULL CHECK(status IN ('queued','running','ready','failed','stale','cancelled'))
provider TEXT NOT NULL
model TEXT NOT NULL
pdf_sha256 TEXT NOT NULL CHECK(length(pdf_sha256)=64)
options_hash TEXT NOT NULL CHECK(length(options_hash)=64)
content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256)=64)
markdown TEXT
page_count INTEGER CHECK(page_count IS NULL OR page_count >= 0)
processing_version TEXT NOT NULL
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

P2 通过 ADD COLUMN 新增 `source_key TEXT`、`ready_at TEXT`、`stale_at TEXT`，再对 backfill 后的 non-null `source_key` 建 partial unique index 支持幂等。P1 约束要求所有 source 的 provider/model 非空：native 固定 `provider='local'`、`model='pymupdf4llm-pymupdf'`，OCR 使用请求中已核验的真实 provider/model；ready 时 markdown 非空、content_sha256 非空、error_code 为空；failed 时 error_code 非空。

### `generated_artifacts`

必须保留以下列：

```text
id TEXT PRIMARY KEY
paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE
kind TEXT NOT NULL
source_document_id TEXT NOT NULL REFERENCES document_sources(id) ON DELETE CASCADE
status TEXT NOT NULL
content TEXT
content_sha256 TEXT
generator_provider TEXT NOT NULL
generator_model TEXT NOT NULL
prompt_version TEXT NOT NULL
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

P2 status 使用 P1 的 `queued|running|ready|failed|stale|cancelled`，P2 use case 只创建 kind `explainer`。通过 ADD COLUMN 新增 `artifact_key TEXT`、`ready_at TEXT`、`stale_at TEXT`，再对 backfill 后的 non-null `artifact_key` 建 partial unique index。新建下列 `paper_artifact_heads` 辅助表保存当前 projection，不能替代 `generated_artifacts`：

```text
paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE
kind TEXT NOT NULL CHECK(length(trim(kind)) > 0)
artifact_id TEXT NOT NULL REFERENCES generated_artifacts(id) ON DELETE CASCADE
updated_at TEXT NOT NULL
PRIMARY KEY(paper_id,kind)
```

另建 `ix_paper_artifact_heads_artifact` 覆盖 `artifact_id`。Repository 的 head compare-and-set 必须在同一事务证明目标 artifact 为 ready、属于相同 paper/kind，且其 SourceDocument 仍 ready；错误 paper/kind、dangling/stale artifact 或旧 CAS token 均零写入。删除 Paper 或当前 artifact 只级联删除对应数据库 head，不触发外部文件操作。

### `processing_jobs`

必须保留以下列：

```text
id TEXT PRIMARY KEY
paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE
job_type TEXT NOT NULL
source_mode TEXT CHECK(source_mode IS NULL OR source_mode IN ('native','ocr'))
status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled'))
progress_json TEXT NOT NULL
attempt INTEGER NOT NULL
max_attempts INTEGER NOT NULL
idempotency_key TEXT NOT NULL UNIQUE
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
started_at TEXT
finished_at TEXT
cancelled_at TEXT
```

P2 通过 ADD COLUMN 新增 `source_document_id`、`artifact_id`、`spec_json TEXT NOT NULL`、`available_at TEXT`、`lease_owner`、`lease_token`、`lease_expires_at`、`heartbeat_at`、`cancel_requested_at`、`result_json TEXT`、`updated_at TEXT`、`retry_of_job_id TEXT REFERENCES processing_jobs(id)`、`retry_sequence INTEGER NOT NULL DEFAULT 0`。`spec_json` 最终约束要求 `length(CAST(spec_json AS BLOB)) BETWEEN 2 AND 4194304`、`json_valid(spec_json)=1`、root/arguments/target 均为 object、`schemaVersion` 为 integer 1，且 insert/update guard trigger 要求 envelope 的 `jobType/paperId/sourceMode/target ids` 与 row columns 使用 null-safe equality 一致。Canonical byte equality、exact key set、variant schema 与 secret-key denylist 由 repository strict decoder 二次强制；数据库约束不是 decoder 的替代品。

Upgrade 先以仅供 ALTER 的 canonical legacy envelope default 增加 non-null 列，再逐 row 读取 P1 columns、用 production encoder 写入 `arguments={"legacyImported":true}` 的 v1 envelope，验证每 row 可 strict decode 且其 bytes/hash 稳定后创建 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update` triggers；未来 insert 省略 `spec_json` 会因 envelope/row mismatch 失败。Backfill 不改任何 P1 column、status 或 idempotency key；nonterminal legacy-import row 可读但 worker dispatch 以 `JOB_SPEC_UNRECOVERABLE` fail closed，绝不猜执行参数。Migration 同时以 `created_at` backfill `available_at/updated_at`，application 此后要求二者非空。P1 canonical job_type CHECK 保持 `source_materialize|ocr|explain|translate|embed|obsidian_export|obsidian_sync`；P2 native source 使用 `source_materialize`，OCR source 使用 `ocr`，explainer artifact 使用 `explain`。Document jobs `source_materialize|ocr|explain|translate|embed` 要求paper_id/source_mode非空；`obsidian_export` 要求paper_id但source_mode可空；global `obsidian_sync` 允许paper_id/source_mode均为空。Domain、list DTO与API serializer必须保留这些nullable语义，不能把global job伪造成某篇native job。

索引：`(status,available_at,created_at)` claim 索引、`lease_expires_at` orphan 索引、`paper_id,created_at` 查询索引、source/artifact/retry FK 索引；partial unique index 保证每个 parent 同时至多一个 queued/running retry descendant。

### `processing_job_events`

P2 的第二张辅助表是 append-only job event log：

```text
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT NOT NULL REFERENCES processing_jobs(id) ON DELETE CASCADE
sequence INTEGER NOT NULL CHECK(sequence > 0)
event_type TEXT NOT NULL CHECK(event_type IN (
  'enqueued','claimed','progress','retry_scheduled','cancel_requested',
  'cancelled','succeeded','failed','lease_recovered'
))
progress_json TEXT NOT NULL
error_code TEXT
created_at TEXT NOT NULL
UNIQUE(job_id,sequence)
```

索引为 `(job_id,sequence)`。event 与对应 job transition 必须同事务写入；GET events 只读取已提交 rows。事件记录经过 size limit 和 secret/正文 redaction，`progress_json` 只允许 stage、current、total、message code 等安全值。

### `ocr_page_checkpoints`

该辅助表为 verified page-capable OCR provider 保留可恢复边界；P2 Fake 可以用多页结果验证它，未核验 DeepSeek 路径仍然零写入：

```text
source_document_id TEXT NOT NULL REFERENCES document_sources(id) ON DELETE CASCADE
page_number INTEGER NOT NULL CHECK(page_number > 0)
status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed'))
markdown TEXT
content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256)=64)
provider_page_id TEXT
attempt INTEGER NOT NULL CHECK(attempt >= 0)
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
PRIMARY KEY(source_document_id,page_number)
```

索引为 `(source_document_id,status,page_number)`。成功 checkpoint 必须同时有 non-empty markdown/content SHA；失败必须有 typed error_code；provider_page_id 与 error_message 经过 secret/raw-body redaction。whole-document provider 不伪造分页，可保持该表为空并只使用 source/job checkpoint。

### 状态与事务

- enqueue：先构造并严格验证 canonical `spec_json`/`spec_sha256`，idempotency key 必须绑定该 SHA；同事务插入 source/artifact、job/spec 和 `enqueued` event。由 idempotency unique constraint 配合 `ON CONFLICT(idempotency_key) DO NOTHING` 选出 winner，随后读取 winner row并重新验证其 spec/hash。
- claim：`BEGIN IMMEDIATE`，先 strict decode due row 的 `spec_json`，再把过期 running job还原 queued并清lease，然后claim一个 due queued job；attempt在claim成功时加一。decode失败零transition、零target write。
- retryable failure：attempt 小于 max_attempts 时 job 回 queued，`available_at` 使用确定性退避，target row 回 queued；达到上限时 job/target failed。
- heartbeat/settle：必须匹配 `id + status='running' + lease_token`；旧 lease 返回内部 `JOB_LEASE_LOST` 且零业务写入。
- explicit retry：保持 parent terminal row/event 不变；先 strict decode parent，在一个事务内逐字节复制 parent `spec_json` 到新 descendant、验证相同 spec SHA，再创建或读取 active descendant、重置仍有效 target，并写 descendant `enqueued` 与 parent `retry_scheduled` event。
- source success：source ready；不会自动创建 explainer job。
- explainer success：同事务把 artifact ready、旧 head artifact stale、head 指向新 artifact、写 `papers.explainer`、job succeeded。
- 取消：job 与对应 queued/running target 同步 cancelled。

---

## Cache Key 契约

所有 hash 使用 UTF-8、字段名排序、无多余空白的 canonical JSON；Job spec 不得包含时间戳、API Key、Authorization 或其他 credential/content bytes。P5 明确定义的非秘密 Vault settings snapshot 可以包含执行所需的 normalized absolute Vault path，但该内部 spec 永不进入 API DTO、event 或日志。

```text
spec_sha256 = sha256(UTF8(spec_json))
options_hash = sha256(canonical_json(options))
source_key = sha256("source:v1\0" + paper_id + "\0" + mode + "\0" + provider + "\0" + model + "\0" + pdf_sha256 + "\0" + options_hash + "\0" + processing_version)
source_job_key = sha256("job:source:v2\0" + source_key + "\0" + spec_sha256)
artifact_options_hash = sha256(canonical_json(kind_specific_options))
artifact_key = sha256("artifact:v1\0" + kind + "\0" + source_document_id + "\0" + source_content_sha256 + "\0" + generator_provider + "\0" + generator_model + "\0" + prompt_version + "\0" + artifact_options_hash)
artifact_job_key = sha256("job:artifact:v2\0" + artifact_key + "\0" + spec_sha256)
```

`artifact_key` 是 P2/P3 唯一的 GeneratedArtifact identity builder：P2 explainer 的 `kind_specific_options` 固定包含 `profile=standard|deep`，P3 translation/classification/metadata/summary 必须调用同一 builder，并把目标语言、ContextBuilder/prompt/output schema 等会改变结果的冻结选项纳入 options hash。禁止为某个 kind 在 route、worker 或 repository 内另写散落 hash。P3 的 index job 使用同一模块中独立的 `index_job_key` builder，不能复用 artifact key。

普通source/artifact POST遇到同key的succeeded/failed/cancelled job都返回原row，不隐式重跑；要创建新target identity必须真实改变processing_version、prompt_version、model或options。唯一例外是用户显式调用 `POST /api/v2/jobs/{job_id}/retry`：它保留target cache identity与terminal parent，按retry lineage创建新queued descendant，不修改或伪造source/artifact key。

---

## Typed 错误码

| Code | HTTP/状态 | 语义 |
|---|---|---|
| `PAPER_NOT_FOUND` | 404 | paper 不存在 |
| `PDF_NOT_FOUND` | 409 | paper 无可解析本地 PDF |
| `SOURCE_MODE_INVALID` | 422 | mode 不是 native/ocr |
| `SOURCE_MODE_MISMATCH` | 422 | artifact/index body mode 与 source row mode 不同 |
| `SOURCE_MODE_OPTIONS_INVALID` | 422 | native 携带 OCR 字段或 options 非对象 |
| `OCR_PROVIDER_REQUIRED` | 422 | OCR 未显式指定 provider |
| `OCR_MODEL_REQUIRED` | 422 | OCR 未显式指定 model |
| `OCR_DISABLED` | 409 | OCR_ENABLED默认0；零registry/provider/transport/row |
| `OCR_PROVIDER_NOT_REGISTERED` | 422 | provider id 未知 |
| `OCR_PROVIDER_CONTRACT_UNVERIFIED` | 503 | provider 已知但无已核验 contract；零 job、零 transport |
| `SOURCE_NOT_FOUND` | 404 | source id 不存在或不属于 paper |
| `SOURCE_NOT_READY` | 409 | source 未 ready |
| `SOURCE_STALE` | 409 | source 已 stale |
| `NATIVE_EXTRACTION_FAILED` | job failed | native extractor 抛错 |
| `NATIVE_TEXT_EMPTY` | job failed | native 返回空白正文 |
| `PDF_ENCRYPTED` | job failed | PDF加密且无法在无密码模式读取page batches |
| `OCR_RATE_LIMITED` | queued retry/job failed | verified provider已归一429与bounded Retry-After |
| `OCR_TIMEOUT` | queued retry/job failed | verified provider timeout |
| `OCR_SERVER_ERROR` | queued retry/job failed | verified provider 5xx |
| `OCR_REQUEST_FAILED` | job failed/queued retry | verified provider 的其他typed transport failure |
| `OCR_RESPONSE_INVALID` | job failed | OCR 结果为空、页数非法或 schema 不符 |
| `EXPLAINER_GENERATION_FAILED` | job failed/queued retry | generator typed failure |
| `EXPLAINER_EMPTY` | job failed | generator 返回空正文 |
| `ARTIFACT_PUBLICATION_CONFLICT` | job failed | source 在发布前变 stale 或 head CAS 失败 |
| `JOB_NOT_FOUND` | 404 | job 不存在 |
| `JOB_NOT_CANCELLABLE` | 409 | terminal job 不能取消 |
| `JOB_NOT_RETRYABLE` | 409 | job 状态、目标或错误类别不允许重试 |
| `JOB_LEASE_LOST` | internal | stale worker 失去所有写权限 |
| `JOB_SPEC_INVALID` | internal/409 | 持久 spec 非 canonical、版本/variant/row binding 无效或含禁用敏感字段；零 claim/复制/dispatch |
| `JOB_SPEC_UNRECOVERABLE` | job failed | P1 legacy-import nonterminal row 缺少可证明执行参数；不从 progress/Settings 猜测 |
| `SCHEMA_REVISION_MISMATCH` | 503 | DB 不在 `20260807_02` |

---

## 文件职责

- Modify: `requirements.txt` — 保留 P1 依赖并把 Alembic/SQLAlchemy 固定为 P1 lock 使用的版本；不加入 OCR SDK。
- Create: `backend/migrations/versions/20260807_02_processing_queue_ocr.py` — 验证 P1 五主表、ADD COLUMN queue/cache/lease 字段、新建三张 P2 辅助表与索引、guarded downgrade。
- Create: `backend/app/domain/processing.py` — immutable DTO、状态转换、hash 与 typed failure。
- Create: `backend/app/application/ports/ocr_provider.py` — OcrProvider Interface。
- Create: `backend/app/application/ports/processing_queue.py` — ProcessingQueue Interface。
- Modify: `backend/app/application/source_documents.py` — 扩展P1 pipeline为source enqueue/processing use cases与mode隔离。
- Modify: `backend/app/application/generated_artifacts.py` — 扩展P1 pipeline为explainer enqueue/generate/publish use cases。
- Modify: `backend/app/repositories/models.py` — 增加 P2 ORM row model，不复制 P1 已有 Paper model。
- Modify: `backend/app/repositories/sqlalchemy.py` — 沿用 P1 engine/session policy 和 SQLite pragma。
- Modify: `backend/app/repositories/unit_of_work.py` — 为 source/artifact/job 原子写入提供单一事务边界。
- Create: `backend/app/repositories/processing_jobs.py` — ProcessingQueue/ProcessingJobRepository 的 SQLite/SQLAlchemy 实现，唯一持有 `spec_json` strict decode/copy boundary。
- Create: `backend/app/repositories/document_sources.py` — SourceDocument 查询、幂等插入与状态 CAS。
- Create: `backend/app/repositories/generated_artifacts.py` — artifact/head/legacy projection 原子发布。
- Modify: `backend/app/providers/native.py` — 复用 P1 `NativeExtractor`，明确移除所有 OCR fallback。
- Create: `backend/app/providers/ocr/fake.py` — deterministic Fake Adapter。
- Create: `backend/app/providers/ocr/registry.py` — verified provider registry 与 DeepSeek contract gate。
- Create: `backend/app/providers/ocr/retry_after.py` — provider-neutral Retry-After normalization与900秒上限。
- Modify: `backend/app/providers/generation.py` — 增加只接收 SourceDocument markdown 的 explainer 调用入口。
- Create: `backend/app/workers/processing_worker.py` — claim/heartbeat/dispatch/settle loop。
- Create: `backend/app/cli/processing_worker.py` — `--once`/`--forever` CLI Adapter。
- Create: `backend/app/api/routes/document_processing.py` — sources/artifacts/jobs routes。
- Modify: `backend/app/bootstrap.py` — 组合真实 SQLite queue、native extractor、provider registry、worker handlers。
- Modify: `backend/app/infrastructure/database_backup.py` — 关键内容计数/hash 增加 sources/artifacts/jobs。
- Create: `backend/tests/test_p2_migration.py`
- Create: `backend/tests/test_processing_queue.py`
- Create: `backend/tests/test_ocr_provider_gate.py`
- Create: `backend/tests/test_source_document_pipeline.py`
- Create: `backend/tests/test_ocr_explainer_slice.py`
- Create: `backend/tests/test_processing_jobs_api.py`
- Create: `backend/tests/test_processing_worker.py`
- Create: `frontend/src/lib/api/processingGateway.ts`
- Create: `frontend/src/lib/api/processingGateway.test.ts`
- Modify: `frontend/src/lib/api/artifactGateway.ts` — 保持 `explainPaper()` facade，内部使用 source/artifact/job Gateway。
- Modify: `frontend/src/features/reader/useArtifactCommands.ts` — 持久 job owner、detach 与显式 cancel。
- Modify: `frontend/src/features/reader/ArtifactPanel.test.tsx` — 只补行为测试，不改组件。
- Modify: `docs/DATABASE.md` — migration、Worker、backup、downgrade 与 runtime rollback 运维命令。

---

## Task 0：重新验证 P0.1 baseline 与 P1 fixed-revision 入口

**Files:**

- Verify: `contracts/pre-existing-test-failures-v1.json`
- Verify: `scripts/pre-existing-failure-baseline.mjs`
- Verify: P1 restored-copy and Live revision evidence

- [ ] **运行 P2 入口 exact baseline verifier**

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p2EntryBaselineJson = node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json
$p2EntryBaselineCode = $LASTEXITCODE
if ($p2EntryBaselineCode -ne 0) { throw "P2 entry baseline verification failed with exit code $p2EntryBaselineCode." }
$p2EntryBaseline = $p2EntryBaselineJson | ConvertFrom-Json
$p2EntryBaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p2EntryBaselineField in $p2EntryBaselineRequiredFields) {
  if (-not ($p2EntryBaseline.PSObject.Properties.Name -contains $p2EntryBaselineField)) { throw "P2 entry baseline verifier omitted required field $p2EntryBaselineField." }
}
if ($p2EntryBaseline.baselineMatched -isnot [bool] -or $p2EntryBaseline.baselineMatched -ne $true) { throw 'P2 entry baseline verifier did not report boolean baselineMatched=true.' }
if ($p2EntryBaseline.observedSuiteExitCode -isnot [int] -and $p2EntryBaseline.observedSuiteExitCode -isnot [long]) { throw 'P2 entry baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p2EntryBaseline.overallGreen -isnot [bool]) { throw 'P2 entry baseline verifier did not report boolean overallGreen.' }
$p2EntryObservedSuiteExitCode = [long]$p2EntryBaseline.observedSuiteExitCode
if (($p2EntryObservedSuiteExitCode -eq 0) -ne $p2EntryBaseline.overallGreen) { throw 'P2 entry baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
~~~

Expected: verifier process exit 0 and `baselineMatched=true`; an accepted raw non-zero remains visible with `overallGreen=false`. Any ID/signature/hash/path drift stops before P2 migration tests. Separately confirm the recorded P1 Live and restore evidence each show a unique `20260807_01` revision; this Task performs no DB command and no migration.

---

## Task 1：建立 P2 additive migration 与 guarded downgrade

**Files:**

- Create: `backend/migrations/versions/20260807_02_processing_queue_ocr.py`
- Modify: `backend/app/repositories/models.py`
- Create: `backend/tests/test_p2_migration.py`

- [ ] **RED 1：只写 spec_json upgrade/backfill/guard 行为测试**

先新增 `P2MigrationTests.test_upgrade_backfills_versioned_canonical_job_specs_and_installs_guards`。Fixture 在 `20260807_01` 插入七种 job_type、nullable paper/source mode 与 queued/terminal sentinel；upgrade 后逐 row 断言 `spec_json` 是 schemaVersion=1 canonical bytes、envelope columns null-safe 对齐、原 P1 columns/count/hash 不变，并断言 missing/noncanonical/wrong-version/unknown-key/secret-key/row-mismatch/超过 4 MiB 的 raw insert/update 被 database guard 或 repository validator 拒绝。测试同时证明 `progress_json` 改变不改变 spec bytes，且 legacy nonterminal row 不可被 worker 猜测执行。

- [ ] **运行 spec migration targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration.P2MigrationTests.test_upgrade_backfills_versioned_canonical_job_specs_and_installs_guards -v
~~~

Expected RED：失败精确指向 `spec_json` column/backfill/guard/strict validator 尚不存在；若 fixture、P1 revision 或测试 DB containment 错误，先修复装配并重跑同一完整命令，不得接受 import error 为 RED。

- [ ] **最小实现 spec_json migration seam**

按 schema 契约增加 non-null `spec_json`，用 production encoder 逐 row canonical backfill并验证，再安装两个固定 guard triggers；SQLAlchemy model 显式标记 non-null。Migration 不改 P1 job columns/status/idempotency；application insert 不允许依赖 ALTER 临时 default。

- [ ] **运行相同 spec migration command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration.P2MigrationTests.test_upgrade_backfills_versioned_canonical_job_specs_and_installs_guards -v
~~~

Expected GREEN：该单一 test reports `OK`，七种 row 全部 canonical/backfilled、negative matrix 全部 fail closed、P1 core bytes/hash 不变。

- [ ] **RED 2：写其余 migration contract tests**

在临时数据库上从 P1 head `20260807_01` 升级，先断言五张 P1 主表及 hard columns 原样存在，再断言 P2 只增加 source/artifact cache 列、job spec/lease/result/retry 列、两个 spec guard triggers、`paper_artifact_heads`、`processing_job_events`、`ocr_page_checkpoints` 和索引；断言 `alembic_version` 只有 `20260807_02`，canonical job_type CHECK 未改变。写缺任一 P1 主表即 `P2_BASE_SCHEMA_MISSING` 且零 DDL 的负例。再写 cache unique、event sequence、checkpoint invariant，以及 head `(paper_id,kind)` 主键、两个 FK/delete action、artifact index、nonblank kind/UTC timestamp 约束 tests；重复 head、dangling artifact、错误 paper/kind relation 和删除级联必须有精确断言。最后写 downgrade tests：P2 operational rows/columns 为空时可降至 `20260807_01` 并保留五主表/hard data；任一 nonempty `spec_json`/operational state 默认抛出 `P2_DOWNGRADE_BLOCKED_NONEMPTY` 且 schema/data 不变；只有隔离副本显式提供 Alembic `-x allow_p2_data_loss=true` 才能删除三张辅助表、spec triggers 和 P2 additive columns。

本模块同时定义下游命令引用的 `P2RestoredCopyValidationTests(unittest.TestCase)`，包含精确方法 `test_db_path_is_bound_restore_at_exact_p2_revision` 与 `test_p2_schema_health_and_required_objects_are_read_only`。两项都要求 process `DB_PATH` 和 `MIGRATION_RESTORE_ROOT`；在首次 SQLite open 前解析 containment，拒绝 Live `data/app.db`、非 `restore-validation-*` parent、sibling-prefix、symlink/junction/reparse escape、missing/multiple current 或非 `20260807_02`。validator 只读验证 P1 hard tables、P2 columns/auxiliary objects、quick/integrity/FK，并断言 bytes/size/mtime/sidecars 前后不变。12 张 legacy count/hash 相等由演练 PowerShell 的逐 phase snapshots 执行，不能由 validator 用常量假造。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration -v
~~~

Expected RED：测试因 revision 文件、P2 additive columns/auxiliary tables 或 guarded downgrade 尚不存在而失败。若失败来自 P1 migration 未升级、测试数据库误指向 `data/app.db`、import error 或 fixture 写错，先修复测试装配并重新执行本步骤上方完整列出的 migration 定向测试命令，直到失败明确指向 P2 migration 行为。

- [ ] **最小实现**

实现 revision `20260807_02`，`down_revision = "20260807_01"`。Upgrade 先用 inspector 一次性验证五张 P1 主表和关键 hard columns，验证失败时尚未执行任何 DDL。验证通过后仅使用 ADD COLUMN 增加本计划 P2 字段，确定性 backfill key/spec/available_at/updated_at，验证全表 spec decode/count/hash，创建 spec guards 与 partial unique/claim/lease/query indexes，再创建三张 P2 辅助表。不得 CREATE、DROP、rename 或 batch-rebuild 五张 P1 主表。所有时间保存 UTC ISO-8601 text；连接沿用 P1 的 foreign-key/WAL policy。

Downgrade 在任何 DROP/rebuild 前读取三张辅助表 count，并检查五主表中会被移除的 P2 column 是否承载 cache/spec/lease/result/retry 值；任何 processing_jobs row 都有 spec，因此 nonempty job table 必须默认 block。检测到 P2 operational state 且 x argument 不等于字符串 `true` 时抛出带固定 code 的 RuntimeError；不得删 row、trigger、column或部分 DDL。获准后先删除 spec guard triggers、三张辅助表和 P2 indexes，再用 SQLite batch operation 只移除 P2-added columns，完整复制并验证 P1 hard data/count/hash。测试只对临时数据库和恢复副本使用破坏性参数，绝不对 Live 使用。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration -v
~~~

Expected GREEN：base-schema preflight、additive upgrade、约束/索引、P1-preserving downgrade、非空 guard 和显式隔离 downgrade 全部通过；测试退出后临时目录可删除且 `data/app.db` 的 size/mtime 不变。

**Exit gate:** migration test 必须同时证明单 head、additive upgrade 和 no-partial-drop；只证明“表能创建”不算完成。

---

## Task 2：固定 processing domain、状态机、cache key 与 Ports

**Files:**

- Create: `backend/app/domain/processing.py`
- Create: `backend/app/application/ports/processing_queue.py`
- Create: `backend/app/application/ports/ocr_provider.py`
- Modify: `backend/tests/test_processing_queue.py`

- [ ] **RED 1：只写 versioned JobSpec codec 与 sensitive-value rejection test**

新增 `ProcessingDomainTests.test_job_spec_v1_is_canonical_strict_content_safe_and_hash_stable`：逐 variant round-trip P2 三类与为 P3–P5 保留的注册 seam，断言 exact top-level keys、sorted compact Unicode bytes、golden SHA、4 MiB bound；duplicate/unknown/missing/wrong-type/noncanonical/unknown-version 与 credential/header/PDF/Markdown/prompt/raw-response key/value fixture 全部拒绝。断言 `progress_json` 不参与 decode，且 API-safe ProcessingJob DTO 不含 raw spec。

- [ ] **运行 JobSpec codec command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingDomainTests.test_job_spec_v1_is_canonical_strict_content_safe_and_hash_stable -v
~~~

Expected RED：失败仅因 v1 union/encoder/decoder/denylist 尚不存在；测试本身不得导入 repository、FastAPI 或 provider。

- [ ] **最小实现 JobSpec codec**

实现唯一 `encode_job_spec_v1/decode_job_spec_v1/hash_job_spec`；decoder 使用 duplicate-key rejecting JSON loader、strict discriminated model、canonical re-encode byte equality与递归敏感 key/value policy。不得为某类 worker 增加旁路 parser。

- [ ] **运行相同 JobSpec codec command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingDomainTests.test_job_spec_v1_is_canonical_strict_content_safe_and_hash_stable -v
~~~

Expected GREEN：单一 test `OK`，golden bytes/SHA 稳定且全部敏感/非 canonical payload fail closed。

- [ ] **RED 2：写其余纯 domain contract tests**

为immutable ProcessingJob/SourceDocument/GeneratedArtifact/JobSpec/Lease/Progress/Failure/Result写construction/state tests。覆盖public job status五值、source/artifact六值、attempt/timestamp/JSON invariants。Document job要求paperId/sourceMode；obsidian_export只要求paperId；global obsidian_sync允许二者null，DTO序列化也保持null。为 source、绑定 `spec_sha256` 的 source job、七种 kind-aware artifact identity、绑定 `spec_sha256` 的 artifact job与 P3 index job builder写跨进程 golden SHA；断言只改变 spec、kind、kind-specific options、source content SHA 或 provider profile 就产生不同 key，native identity固定local/pymupdf4llm-pymupdf。为queued→running→terminal/retry/cancel及非法转换写tests。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingDomainTests -v
~~~

Expected RED：失败只因 domain types、hash helpers、state transition validator 或 Ports 尚未实现。测试不得导入 FastAPI、真实数据库或任何 Provider SDK。

- [ ] **最小实现**

在 `domain/processing.py` 中实现冻结 dataclass/Enum、canonical JSON、绑定 spec SHA 的 SHA-256 key builders、typed `ProcessingFailure` 和纯状态转换函数。拒绝 NaN/Infinity、非字符串 mapping key、naive datetime、空 id 和负 attempt。Port 文件只定义本计划公开方法与数据类型，不导入 concrete repository、FastAPI、OpenAI client 或 provider。`ProcessingJob.status` 不得出现等待/孤儿内部状态；backoff 只由 `available_at` 表示。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingDomainTests -v
~~~

Expected GREEN：所有 golden hash、合法/非法 transition 和 constructor invariant 通过；同一输入跨两次进程执行产生完全相同的 key。

**Exit gate:** application 和 repository 后续只使用这些 domain types；不得再各自定义字符串状态或第二份 hash 公式。

---

## Task 3：实现原子 enqueue 与跨连接幂等

**Files:**

- Modify: `backend/app/repositories/models.py`
- Modify: `backend/app/repositories/sqlalchemy.py`
- Modify: `backend/app/repositories/unit_of_work.py`
- Create: `backend/app/repositories/document_sources.py`
- Create: `backend/app/repositories/generated_artifacts.py`
- Create: `backend/app/repositories/processing_jobs.py`
- Modify: `backend/tests/test_processing_queue.py`

- [ ] **RED 1：只写 canonical spec persistence 与 spec-bound idempotency test**

新增 `ProcessingEnqueueTests.test_enqueue_persists_canonical_spec_and_binds_idempotency_to_spec_hash`。对 native、OCR 与 explainer 各 enqueue 一次，直接读取 row，断言 `spec_json` 与 production encoder bytes 完全一致、SHA进入 v2 idempotency formula、`progress_json`只含初始进度。相同 spec dedupe；只改变一个允许的 semantic argument 时 spec SHA/idempotency key 必须变化；含 secret 或 noncanonical injected spec 必须在任何 source/artifact/job/event write 前失败。API/log/event/result均不得出现 spec 或敏感值。

- [ ] **运行 spec persistence command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingEnqueueTests.test_enqueue_persists_canonical_spec_and_binds_idempotency_to_spec_hash -v
~~~

Expected RED：失败明确指向 repository 尚未写 `spec_json`、idempotency 未绑定 spec SHA 或敏感 payload 未在首写前拒绝；SQLite lock/import error 不算目标 RED。

- [ ] **最小实现 repository spec boundary**

实现 `insert_with_spec/load_spec`：事务开始前 strict encode，insert 时同时写 spec，conflict winner 重新 load/decode并比较 SHA。任何 mismatch 使用 typed error、零 target/event mutation；不得从 `progress_json` fallback。

- [ ] **运行相同 spec persistence command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingEnqueueTests.test_enqueue_persists_canonical_spec_and_binds_idempotency_to_spec_hash -v
~~~

Expected GREEN：单一 test `OK`，三类 job raw spec/hash/idempotency 全等且 secret/noncanonical cases 零写。

- [ ] **RED 2：写其余 enqueue repository tests**

使用迁移到 P2 head 的临时 file-backed SQLite 数据库和两个独立 connection。覆盖：source target、mode 对应的 job 与 `enqueued` event 在同一事务出现；native 创建 `source_materialize` job，OCR 创建 `ocr` job；中途异常时三者都不出现；相同 `source_key/source_job_key` 连续或双连接竞争 enqueue 只产生一个 source 和一个 job，两个调用返回相同 id 且第二个 `deduplicated=true`；同 key 的 queued/running/succeeded/failed/cancelled row 都返回原 row，不隐式复活；不同 processing_version/options/model 产生新 row；artifact enqueue 创建 `explain` job，必须绑定同 paper、ready、非 stale source，且 artifact/job/event 也原子幂等。另用两个 connection 并发 publish 同一 `(paper_id,kind)`，断言只有一个 head CAS winner；wrong-paper/wrong-kind/non-ready artifact 与旧 CAS token 零写入，删除 current artifact 后 head row 按 FK 级联消失。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingEnqueueTests -v
~~~

Expected RED：失败指向 repository/UoW 或原子幂等尚缺失。SQLite `database is locked`、跨线程复用同一 connection 或误用内存数据库均属于 fixture/实现问题，不能当作目标 RED。

- [ ] **最小实现**

以 P1 UnitOfWork 为唯一 commit owner。source enqueue 先生成 canonical spec/spec SHA，再计算稳定 target/job key，在一个事务内执行唯一约束保护的 insert，并在 conflict 后读取 winner及其spec；只有当前事务创建 source 时才创建对应 job。artifact enqueue先在同一事务读取并验证source，再按相同模式处理。不得用“先 SELECT 后无保护 INSERT”，不得捕获所有 IntegrityError 后假装 dedupe；只识别命名唯一约束，其他约束错误原样分类。返回值携带 target、job 和 deduplicated，不携带 raw spec。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingEnqueueTests -v
~~~

Expected GREEN：每个竞争场景数据库只有一对 target/job，失败注入后没有半条业务 row，terminal key 不被隐式重跑。

**Exit gate:** idempotency 的正确性必须来自数据库约束和事务，不得依赖单进程 lock。

---

## Task 4：实现 claim、lease、heartbeat、orphan recovery 与 deterministic retry

**Files:**

- Create: `backend/app/repositories/processing_jobs.py`
- Modify: `backend/app/repositories/document_sources.py`
- Modify: `backend/app/repositories/generated_artifacts.py`
- Modify: `backend/tests/test_processing_queue.py`

- [ ] **RED 1：只写 claim strict-decode-before-transition test**

新增 `ProcessingLeaseTests.test_claim_strictly_decodes_spec_before_any_transition`。分别通过受控 raw SQL 放入 valid canonical、malformed、noncanonical、unknown-version、row-mismatch 与 secret-bearing spec；只有 valid row 可被 claim，lease 带 immutable decoded spec/raw SHA。每个 invalid row 的 status/attempt/target/event/lease bytes 前后全等并返回 `JOB_SPEC_INVALID`，worker/provider调用数为0；不能通过改 `progress_json` 使其可执行。

- [ ] **运行 strict claim command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests.test_claim_strictly_decodes_spec_before_any_transition -v
~~~

Expected RED：失败因 claim 在 decode 前 transition、未返回 stored spec 或接受非法 bytes；fixture 必须显式绕过 application seam且证明 raw row 确已插入。

- [ ] **最小实现 claim spec fencing**

在 `BEGIN IMMEDIATE` 内先选择候选并 `load_spec`，验证通过后才执行 target/job/attempt/lease/event CAS。

- [ ] **运行相同 strict claim command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests.test_claim_strictly_decodes_spec_before_any_transition -v
~~~

Expected GREEN：valid row唯一被claim、全部invalid row零变化和零dispatch。

- [ ] **RED 2：只写 retry/recovery exact-spec-copy test**

新增 `ProcessingLeaseTests.test_explicit_retry_and_orphan_recovery_preserve_exact_spec_bytes`。捕获 parent `spec_json` bytes/SHA；explicit retry descendant 必须逐字节相等，竞争 retry 仍只有一个 descendant；automatic retry与expired orphan recovery保持同一 row bytes不变。篡改 parent 后 retry fail closed且零 descendant/target/event，恢复不得从当前Settings/progress重建。

- [ ] **运行 retry/recovery spec command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests.test_explicit_retry_and_orphan_recovery_preserve_exact_spec_bytes -v
~~~

Expected RED：失败因 retry 重建而非 raw-copy spec、recovery改写spec或篡改parent仍产生descendant。

- [ ] **最小实现 exact copy/recovery**

`copy_spec_for_retry` 在同一短事务 strict decode parent、原样 insert bytes、重读并比较 SHA；automatic retry/orphan recovery SQL 永不更新 `spec_json`。

- [ ] **运行相同 retry/recovery spec command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests.test_explicit_retry_and_orphan_recovery_preserve_exact_spec_bytes -v
~~~

Expected GREEN：各阶段 bytes/SHA 全等且 tamper case 零写。

- [ ] **RED 3：写其余 lease/retry tests**

用可注入clock覆盖：`claim_next` 只选 `status='queued' AND available_at<=now` 并稳定排序；双connection只有一个lease winner；claim原子更新target/job/attempt/lease/event；`report_progress` 仅匹配token；旧token的progress/complete/fail零写入；expired running在同一 `BEGIN IMMEDIATE` 恢复queued/event。Retryable failure按 `base_delay=min(900,5*2**(attempt-1))`；无Retry-After用base，normalized Retry-After用 `min(900,max(base,retry_after_seconds))`。测试429合法seconds/HTTP-date归一值、missing/invalid为None、overlong clamp；timeout/500用base；invalid/empty response non-retryable；达到max_attempts后target/job failed。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests -v
~~~

Expected RED：失败明确显示 claim/lease/retry 行为缺失。测试不得使用 sleep；所有时间推进由 fake clock/显式 now 完成。

- [ ] **最小实现**

在processing job repository内使用短生命周期UoW和 `BEGIN IMMEDIATE`：先 strict decode candidate，恢复expired running时保持spec bytes不变，再用单一UPDATE/CAS claim due row。lease token不可预测且不写日志；attempt仅成功claim时递增。progress/settle WHERE包含id、running、lease token/owner且不更新spec。Backoff严格使用上述bounded exponential公式，无random jitter；Worker只接受Adapter已归一的retry_after_seconds，不解析HTTP header。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue.ProcessingLeaseTests -v
~~~

Expected GREEN：竞争 claim 只有一个 winner，expired lease 可恢复，旧 worker 无写权限，retry 时间与 attempt 完全匹配 golden values。

**Exit gate:** repository 不得持有跨 handler 的长事务；外部 Provider 调用期间数据库 connection 已归还。

---

## Task 5：实现取消、checkpoint 与进程重启恢复

**Files:**

- Modify: `backend/app/repositories/processing_jobs.py`
- Modify: `backend/app/repositories/document_sources.py`
- Modify: `backend/app/repositories/generated_artifacts.py`
- Create: `backend/tests/test_processing_worker.py`

- [ ] **RED：先写 cancellation/restart tests**

覆盖 queued job 取消后 job/target 同事务成为 cancelled 并写 finished_at/cancelled_at/event；running job 取消只写 cancel_requested_at 与 event，worker 下一 checkpoint 用当前 lease settle cancelled；terminal job 再取消返回 `JOB_NOT_CANCELLABLE` 且 row 不变；cancel 与 complete 竞争只能有一个 terminal winner；failed/cancelled job 的显式 retry 保持 parent 不变并创建带 lineage 的 queued descendant，重复/竞争 retry 返回同一 active descendant，succeeded/nonterminal/non-retryable/stale target 拒绝；模拟进程在 claim 后退出，lease 到期后新 worker可恢复并重新claim；模拟旧进程复活，旧token仍无法publish。每个阶段都捕获并比较原/descendant/recovered row 的 `spec_json` bytes/SHA，要求 strict decode且逐字节不变。进度/event JSON必须保留最后已完成阶段，不含spec、source markdown、prompt、secret或Provider raw body。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_worker.ProcessingCancellationTests -v
~~~

Expected RED：失败指向 cancel request、terminal CAS 或 restart recovery 尚缺失；测试进程不访问真实 PDF、网络和 Live DB。

- [ ] **最小实现**

在 queue implementation 以 `report_progress` 实现内部 `checkpoint(lease, progress, now)`，原子返回 `continue|cancelled|lease_lost`。queued cancel立即settle；running cancel仅请求cooperative cancellation，不关闭其他worker连接。`retry`在短事务内strict decode parent、逐字节复制spec、验证target后创建或读取active descendant并追加两侧events。每个handler只从`lease.spec.value`读取输入，并在Provider调用前后和publish前调用checkpoint；不得从progress或当前Settings补参。所有settle使用同一terminal CAS，失败方读取winner并返回明确分类。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_worker.ProcessingCancellationTests -v
~~~

Expected GREEN：queued/running/terminal 三类取消语义、重启回收和 stale worker fencing 全部通过，且没有测试依赖 wall-clock sleep。

**Exit gate:** HTTP disconnect 只允许停止客户端轮询；除非用户显式调用 cancel endpoint，不得把 disconnect 等同于 server job cancellation。

---

## Task 6：建立 OCR Port、Fake 与 DeepSeek 零网络 contract gate

**Files:**

- Create: `backend/app/application/ports/ocr_provider.py`
- Create: `backend/app/providers/ocr/__init__.py`
- Create: `backend/app/providers/ocr/fake.py`
- Create: `backend/app/providers/ocr/registry.py`
- Create: `backend/app/providers/ocr/retry_after.py`
- Create: `backend/tests/test_ocr_provider_gate.py`

- [ ] **RED：先写 provider boundary tests**

先以默认 `OCR_ENABLED=0` 测explicit OCR：409且registry/provider/transport/PDF reader/source/job/checkpoint全零；test app设置1后才运行Fake matrix。Fake extract_batch按page返回/失败并记录calls；Retry-After parser覆盖seconds/date/missing/invalid/negative/overlong。Production无fake；unknown provider；enabled DeepSeek 503零构造/写/网；native options与enabled OCR字段/options invalid 422。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_provider_gate -v
~~~

Expected RED：Fake、registry 和 pre-enqueue gate 尚不存在；RED 必须发生在任何 transport 构造前。若测试真的发起 DNS/HTTP，则立即停止并修复 fixture，不能把网络失败当作正确结果。

- [ ] **最小实现**

实现startup-frozen OCR gate、async Fake与纯Retry-After normalizer。Disabled composition不构造registry；request use case在PDF read/hash、key计算和首个DB write前检查gate。Fake仅test override且需enabled。Enabled production registry把DeepSeek标known-unverified并在provider object前失败；不创建DeepSeek Adapter/SDK/client。Worker只读normalized retry seconds。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_provider_gate -v
~~~

Expected GREEN：async page Fake、Retry-After normalization/bounds、production exclusion、unknown provider和DeepSeek 503零构造/零网络/零数据库写入全部通过。

**Exit gate:** Fake/native P2 slice 可在真实 contract 未满足时完成，但 DeepSeek registry 必须继续 503/零 transport。真实 Adapter 的唯一实施入口是 `2026-08-08-p2-deepseek-ocr-adapter-conditional.md`；只有其官方资料 manifest、逐行为 fixture TDD 与条件出口全部通过才算真实能力完成，P2 不保留可误启用的半成品 Adapter。

---

## Task 7：证明 native/OCR 严格分派且失败绝不跨 mode 回退

**Files:**

- Modify: `backend/app/application/source_documents.py`
- Modify: `backend/app/providers/native.py`
- Modify: `backend/app/bootstrap.py`
- Create: `backend/tests/test_source_document_pipeline.py`

- [ ] **RED：先写隔离对象图与分派 tests**

构造记录调用次数的NativeExtractor、page-capable Fake OCR和panic doubles。强制matrix覆盖single-page text、multi-page scanned、mixed text/image、encrypted、empty、invalid PDF；native每种success/failure的OCR calls为零，scanned empty不回退OCR。OCR只调用指定Fake，native calls为零；pageBatchSize=1/maxConcurrency=1默认顺序；自定义batch/concurrency不超bound；成功pages写checkpoint后不重复；指定page timeout/429/500后job按policy queued，resume只处理failed/missing；empty/duplicate/out-of-order/invalid page result non-retryable；encrypted分类 `PDF_ENCRYPTED`；cancel不提交新batch；PDF SHA drift stale且不处理旧bytes。覆盖provider/model/options只从持久source row读取。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_document_pipeline.SourceModeDispatchTests -v
~~~

Expected RED：失败因 `SourceDocumentProcessor` 或隔离 bootstrap 尚未存在；panic double 不应被调用。任何测试出现 mode fallback 成功都属于规格失败。

- [ ] **最小实现**

建立两个不同constructor/factory：`build_native_source_processor` 只接受native extractor；`build_ocr_source_processor` 只接受OCR registry、non-text PDF page batch reader和checkpoint repository。顶层dispatcher只读取source.mode后选择factory。OCR processor先读取page count并排除已succeeded checkpoints，按pageBatchSize分batch，用bounded async scheduler执行且默认串行；每batch前后checkpoint cancel/lease，成功page立即短事务CAS，最后要求1..page_count完整并按序组装。两路径调用前重算PDF SHA；成功规范换行、拒绝空白、计算content SHA并complete。429 delay取bounded exponential与normalized Retry-After最大值；timeout/500 retry；invalid response non-retry。任何失败不跨mode。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_document_pipeline.SourceModeDispatchTests -v
~~~

Expected GREEN：完整PDF/OCR matrix、page batch/concurrency、429/timeout/500、cancel/resume/no-repeat、native/OCR零交叉调用和PDF drift全绿。

**Exit gate:** code review 搜索 `fallback`、`except` 和 extractor injection，逐个证明不存在捕获一种 mode 错误后调用另一种 mode 的路径。

---

## Task 8：打通 ready SourceDocument 到 explainer 的原子纵向切片

**Files:**

- Modify: `backend/app/application/generated_artifacts.py`
- Modify: `backend/app/providers/generation.py`
- Modify: `backend/app/repositories/generated_artifacts.py`
- Create: `backend/tests/test_ocr_explainer_slice.py`

- [ ] **RED：先写端到端 application/repository tests**

在临时P2 DB用Fake OCR/generator与真实repositories完成OCR source→ready→explainer publish。Artifact command必须同时给camelCase sourceMode/sourceDocumentId并与source.mode匹配，mismatch零artifact/job。断言generator正文等于source.markdown且不打开PDF；artifact provenance完整；publish原子更新artifact/old head/head/legacy/job。注入每个写点rollback，并测not ready/stale/wrong paper/empty/late stale/lease/head conflict。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_explainer_slice -v
~~~

Expected RED：失败明确指向 artifact use case、source-only generation 或 atomic publication 未实现；Fake OCR/LLM 都不得联网。

- [ ] **最小实现**

`enqueue_explainer` 只接受 ready、同 paper、当前 PDF SHA 未 stale 的 source。`ArtifactGenerator.generate_explainer` 在短只读事务复制所需 markdown/hash/version 后关闭连接，再调用 generator；publish 前新事务重读 source 并验证仍 ready/hash 相同，使用 lease 和 head CAS 原子完成五处写入。任何异常不得留下 ready artifact 配 stale head、legacy projection 与 head 不一致或 succeeded job 配未 ready artifact。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_explainer_slice -v
~~~

Expected GREEN：完整 Fake OCR→SourceDocument→explainer 路径与所有 rollback/conflict case 通过；generator PDF-open spy 为零。

**Exit gate:** `papers.explainer` 只是 P2 dual-write compatibility projection；source of truth 是 ready artifact + head，P6 之前不删除 legacy projection。

---

## Task 9：实现单 Worker loop、CLI、优雅停机与 crash recovery

**Files:**

- Create: `backend/app/workers/__init__.py`
- Create: `backend/app/workers/processing_worker.py`
- Create: `backend/app/cli/processing_worker.py`
- Modify: `backend/app/bootstrap.py`
- Modify: `backend/tests/test_processing_worker.py`

- [ ] **RED：先写 worker adapter tests**

覆盖run_once idle；按canonical type分派；reserved handler fail-fast；checkpoint尊重cancel/lease。OCR worker测试page scheduler默认/upper concurrency、成功page不重复、cancel停止新batch；typed429合法/missing/invalid/overlong Retry-After、timeout/500按exact available_at公式，invalid response直接failed。`--once`一次claim；`--forever` injectable waiter；SIGINT/SIGTERM停止新claim并安全settle/lease-recover。日志只有安全IDs/stage/attempt/code。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_worker.ProcessingWorkerLoopTests -v
~~~

Expected RED：失败指向 worker/CLI/bootstrap 未实现；测试通过 injected waiter 和 signal hook，不启动永久真实子进程。

- [ ] **最小实现**

worker 每轮新建短 UoW claim，关闭事务后调用 handler，再通过 lease CAS settle。idle wait 可注入，生产默认 1 秒且响应 stop event。CLI 从 P1 settings/composition root 获取数据库路径，并显式传入冻结的 `required_schema_revision="20260807_02"`；启动时要求 `alembic_version` 恰有这一条 current revision，否则打印结构化 `SCHEMA_REVISION_MISMATCH` 到 stderr 并非零退出。测试还必须证明 P1 的 `20260807_01` 期望值不会被写死到共享 gate，missing/multiple/`20260807_03` 在 P2 CLI 均被拒绝。默认不自动启动 thread；由独立 CLI/process 管理 lifecycle。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_worker.ProcessingWorkerLoopTests -v
~~~

Expected GREEN：once/forever/idle/dispatch/signal/log redaction/crash recovery 通过，测试无 hang、无残留进程和未关闭 SQLite handle。

**Exit gate:** P2 支持持久化单 worker；不声称多 worker 吞吐保证，但并发 claim 的 fencing 仍必须正确。

---

## Task 10：接入 FastAPI source/artifact/job routes

**Files:**

- Create: `backend/app/api/routes/document_processing.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/api/errors.py`
- Modify: `backend/app/bootstrap.py`
- Create: `backend/tests/test_processing_jobs_api.py`

- [ ] **RED：先写 HTTP contract tests**

通过P1 test app factory/dependency overrides测试所有P2 API exact camelCase DTO/status/error/unknown-field rejection；P2 app factory 必须显式传 `required_schema_revision="20260807_02"`，并有旧 P1 head 返回 `SCHEMA_REVISION_MISMATCH`、正确 P2 head 正常启动的成对测试。覆盖native/OCR matrix、DeepSeek零写零网、POST dedupe、artifact body同时要求sourceMode/sourceDocumentId且mode mismatch 422、cursor lists、filtered/global job list（paperId/sourceMode可null）、GET/events、cancel、explicit retry。断言202后handler未在request thread执行；error/log无secret/content/raw body。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_jobs_api -v
~~~

Expected RED：失败因 router/DTO/error mapping 尚缺失；若 P1 test app/import 未完成则不得绕开 composition root 手写第二个 app。

- [ ] **最小实现**

Pydantic request/query models 使用 strict types 与 `extra='forbid'`；route 只解析、调用 use case、序列化 domain DTO。Provider verification 与 canonical JobSpec encode/secret rejection 在 enqueue use case 的首个 DB write 前执行。List cursors 是签名/验证过的稳定 sort tuple，limit 范围 1–100，非法 cursor 返回 422。GET/list/events/cancel/retry 不暴露 lease owner/token、idempotency key、`spec_json`/spec SHA/settings snapshot或内部 result_json。所有 domain failures 通过 P1 mapper 返回 `{error:{code,message,details}}`，details 只含安全 id/field。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_jobs_api -v
~~~

Expected GREEN：所有 request/list/event/cancel/retry matrix 与 exact DTO 通过，DeepSeek case 为 503 且 row/call count 仍零，request latency test 证明没有执行 OCR/generator/automatic retry loop。

**Exit gate:** P2 route 只位于 `/api/v2`；不在本阶段接管 legacy `/api/explain` 或 Node server。

---

## Task 11：建立 React ProcessingGateway 与 Hook 最小兼容接入

**Files:**

- Create: `frontend/src/lib/api/processingGateway.ts`
- Create: `frontend/src/lib/api/processingGateway.test.ts`
- Modify: `frontend/src/lib/api/artifactGateway.ts`
- Modify: `frontend/src/features/reader/useArtifactCommands.ts`
- Modify: `frontend/src/features/reader/ArtifactPanel.test.tsx`

- [ ] **RED：先写 Gateway/Hook behavior tests**

测试Gateway strict DTO/polling/detach/explicit cancel/fail-closed。对injectable artifactGateway factory显式传入P2 ProcessingGateway时，`explainPaper(paperId,deep)` 顺序enqueue native source、等待ready、以sourceMode/sourceDocumentId/profile enqueue explainer并poll terminal；未注入的exported singleton保持legacy NDJSON直到P4 takeover。Hook切paper/unmount/re-run只detach，不cancel server；不改UI。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- src/lib/api/processingGateway.test.ts src/features/reader/ArtifactPanel.test.tsx
~~~

Expected RED：失败因 Gateway、strict decoders 或 persistent job ownership 尚缺失；现有 legacy tests 仍应绿。不要用宽松 `as` assertion 伪造 DTO。

- [ ] **最小实现**

实现injectable client/clock ProcessingGateway。保留explainPaper签名/terminal result；factory只有显式传入processing adapter才走P2，default singleton继续legacy，P4负责runtime切换。deep映射profile，Hook保存server job/owner且late run不覆盖；保留UI。P2不迁移translation facade。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
npm.cmd run test:run --prefix frontend -- src/lib/api/processingGateway.test.ts src/features/reader/ArtifactPanel.test.tsx
~~~

Expected GREEN：Gateway exact requests/decoders/polling/detach/cancel 与现有 panel behavior 全绿；snapshot/DOM 不出现新控件或文案变化。

**Exit gate:** 前端只是 Gateway/Hook seam；没有 P2 UI redesign，且 `public/` 零 diff。

---

## Task 12：扩展 backup fingerprint、运维文档并完成迁移/运行时回滚演练

**Files:**

- Modify: `backend/app/infrastructure/database_backup.py`
- Modify: `backend/tests/test_database_backup.py`
- Extend: `backend/tests/test_p2_migration.py`
- Modify: `docs/DATABASE.md`

- [ ] **RED 1：只写 ProcessingJob spec backup/inventory test**

新增 `DatabaseBackupTests.test_manifest_records_and_verifies_canonical_processing_job_specs`。临时 P2 fixture 插入七种 canonical job spec，断言 full-P2 `processingJobs` projection明确按固定顺序包含 `spec_json`，另有 `processingJobSpecs` count/hash只覆盖 `(id,spec_json)`；两者 count 都等于 processing_jobs 总数。逐字节篡改 spec、改成等价但noncanonical JSON、替换schemaVersion、注入secret key、让spec与row mismatch，各自必须在 verify/schema inventory 中返回 `BACKUP_LOGICAL_MISMATCH|JOB_SPEC_INVALID`，不能只比较row count。测试同时断言 `processing_jobs_spec_guard_insert|processing_jobs_spec_guard_update` inventory name/SQL hash存在且固定。

- [ ] **运行 spec backup/inventory command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_and_verifies_canonical_processing_job_specs -v
~~~

Expected RED：失败因 backup registry/inventory 尚未覆盖 raw spec bytes或两个guard triggers；fixture 必须使用临时数据库，不得创建Live backup。

- [ ] **最小实现 spec fingerprint/inventory**

把 `spec_json` 加入 `processingJobs` 固定 ordered projection，新增 `processingJobSpecs=(id,spec_json)` projection；schema inventory 固定记录两个 spec guard trigger 的normalized SQL SHA，并在capture时strict decode每个spec。不得把spec正文输出到manifest/log，只输出count/hash与错误row id。

- [ ] **运行相同 spec backup/inventory command 并确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_and_verifies_canonical_processing_job_specs -v
~~~

Expected GREEN：单一 test `OK`；spec count/hash/trigger inventory 完整，所有 tamper case fail closed且manifest不泄露payload。

- [ ] **RED 2：写其余 P2 backup/rollback tests**

扩展临时 fixture，断言 manifest 的 critical content counts/hash 包含 full-P2 keys `documentSources`、`generatedArtifacts`、`processingJobs`、`processingJobSpecs`，以及只选择 P1 columns 的 `p1CoreDocumentSources`、`p1CoreGeneratedArtifacts`、`p1CoreProcessingJobs`；table counts 包含 `paper_artifact_heads`、`processing_job_events`、`ocr_page_checkpoints`。fixture 必须在三张 P1 表各插入至少一条非空、可区分的合法 row；core projection count 必须等于对应表总 row count，content SHA 覆盖下方固定 P1 column tuple，不包含任何 P2-added column、lease token 或 error secret。篡改任一 P1-core field 或 P2 business row 后 verify 必须 `BACKUP_LOGICAL_MISMATCH`。

在 `backend/tests/test_p2_migration.py` 明确定义 `P2CoreProjectionMigrationTests(unittest.TestCase)`，其唯一 public method 名为 `test_nonempty_p1_core_fingerprints_survive_p2_upgrade_downgrade_reupgrade`。该 method 只使用临时 restore-contained DB：先迁到 `20260807_01` 并插入三张表的合法非空 sentinel rows，记录三个 core keys，再 upgrade 到 `20260807_02`；先证明无 x argument 的 downgrade 因 backfilled P2 state 以 `P2_DOWNGRADE_BLOCKED_NONEMPTY` 拒绝且 core fingerprint 不变，随后仅对该可丢弃临时副本用 `-x allow_p2_data_loss=true` 降到 `20260807_01`，再 re-upgrade 到 `20260807_02`。initial、upgrade、blocked attempt、explicit downgrade、re-upgrade 每个 phase 都重新 inspect，要求三个 key 在 `contentCounts/contentSha256` 中存在、count/hash 与最初完全相等，并证明 P2-added columns 的 backfill/移除/重建不会进入 core hash。

写文档 command smoke test，确保示例包含 create→verify→restore-check、恢复副本 `20260807_01→20260807_02→20260807_01→20260807_02`、nonempty downgrade guard、runtime legacy values 和 worker stop order。`backend/tests/test_p2_migration.py` 还必须明确创建命令使用的 `P2OperationalDocumentationTests(unittest.TestCase)`，其唯一 public method 名为 `test_p2_runbook_contains_fixed_migration_and_rollback_contract`；该 method 只读 `docs/DATABASE.md`，不打开 DB，并逐项断言上述命令、固定 revisions、12-table map-presence/equality guard、三个 core key 的精确 ordered P1 column tuple、`processingJobSpecs`/spec guard inventory、upgrade/downgrade/re-upgrade/Live equality guard 与 stop conditions。

- [ ] **运行 targeted command 并确认 RED**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_p2_content_fingerprints backend.tests.test_database_backup.DatabaseBackupTests.test_verify_detects_p2_content_tampering backend.tests.test_p2_migration.P2CoreProjectionMigrationTests.test_nonempty_p1_core_fingerprints_survive_p2_upgrade_downgrade_reupgrade backend.tests.test_p2_migration.P2OperationalDocumentationTests -v
~~~

Expected RED：full-P2 fingerprints、三个 P1-core projections、逐 phase nonempty equality 或运维文档契约尚缺失。测试使用临时库；不得为获得 RED 修改 Live 数据或创建 Live backup。

- [ ] **最小实现**

在既有 backup fingerprint registry 保留三类 full-P2 ordered logical projections，并为相同表追加下列不可变的 P1-core specifications；所有 tuple 都使用 `filter_column=None, filter_mode="all"`，所以非空表的每一 row 都计数。`_fingerprint_table` 继续按 primary key 排 row；hasher 先按这里的顺序 frame column name，再用既有 `_encode_sqlite_value` frame 每个值（包括 NULL），不得 trim、JSON 重排或读取未列出的 P2 column。空表也必须记录 count=0 与只含固定 column frames 的稳定 hash。

~~~python
P1_CORE_CONTENT_PROJECTIONS = {
    "document_sources": (
        (
            "p1CoreDocumentSources",
            (
                "id", "paper_id", "mode", "status", "provider", "model",
                "pdf_sha256", "options_hash", "content_sha256", "markdown",
                "page_count", "processing_version", "error_code", "error_message",
                "created_at", "updated_at",
            ),
            None,
            "all",
        ),
    ),
    "generated_artifacts": (
        (
            "p1CoreGeneratedArtifacts",
            (
                "id", "paper_id", "kind", "source_document_id", "status",
                "content", "content_sha256", "generator_provider",
                "generator_model", "prompt_version", "error_code",
                "error_message", "created_at", "updated_at",
            ),
            None,
            "all",
        ),
    ),
    "processing_jobs": (
        (
            "p1CoreProcessingJobs",
            (
                "id", "paper_id", "job_type", "source_mode", "status",
                "progress_json", "attempt", "max_attempts", "idempotency_key",
                "error_code", "error_message", "created_at", "started_at",
                "finished_at", "cancelled_at",
            ),
            None,
            "all",
        ),
    ),
}
~~~

`_build_critical_content_hashers()` 在检查 column subset 之前必须把这些 specs 合并进既有 registry，不能只声明常量：

~~~python
for table_name, core_specs in P1_CORE_CONTENT_PROJECTIONS.items():
    specifications[table_name] = core_specs + specifications.get(table_name, ())
~~~

因此 `20260807_01` preflight 已能产出三个 core keys；升级后同一 key 与 full-P2 key 同时存在。缺少任一 P1 column 时不得静默把 core key 当作可选：P1/P2 migration validators 必须先以 schema contract 失败，operational map-presence guard 随后也会停止。

`p1CoreDocumentSources` 明确排除 `source_key|ready_at|stale_at`；`p1CoreGeneratedArtifacts` 排除 `artifact_key|ready_at|stale_at`；`p1CoreProcessingJobs` 排除 `source_document_id|artifact_id|spec_json|available_at|lease_owner|lease_token|lease_expires_at|heartbeat_at|cancel_requested_at|result_json|updated_at|retry_of_job_id|retry_sequence`。这些 exclusion 只隔离 P2 additive fields，不能过滤 row，也不能把非空 P1 表当成空表跳过。P1-core跨revision相等不能替代P2阶段内对`processingJobSpecs`的count/hash/strict-decode验证。

`docs/DATABASE.md`必须逐字列出三个core key、上面的ordered column tuples、被排除的P2 columns、`processingJobSpecs`的`(id,spec_json)`projection、两个spec guard triggers，以及restored-copy upgrade/downgrade/re-upgrade和Live before/after的core/spec/inventory停止条件。随后写明：

1. 停止新 enqueue，停止 worker claim，等待/取消 running jobs，停止 API writer。
2. 用 P0 CLI 对精确 Live DB 创建、verify、restore-check 快照并记录返回路径/hash。
3. 只在 restore-check 副本执行 `alembic upgrade 20260807_02`、validators、guarded downgrade、再 upgrade。
4. Live upgrade 只在演练全绿后、所有 writer 停止时显式执行。
5. 运行时回滚设置`API_BACKEND_MODE=legacy`、`DOCUMENT_PIPELINE_MODE=legacy`、`GENERATION_PIPELINE_MODE=legacy`、`ARTIFACT_READ_MODE=legacy`、`ARTIFACT_WRITE_MODE=legacy`、`OCR_ENABLED=0`，停止P2 worker claim后重启；保留P2 additive tables、`spec_json`、events/checkpoints与全部queued/terminal jobs，不从progress重建请求。
6. 任何nonempty processing_jobs因`spec_json`都触发默认downgrade guard。Schema downgrade仅适用于已证明P2数据无唯一价值的隔离副本且显式`allow_p2_data_loss=true`；否则离线恢复精确P0 snapshot，并明确snapshot后数据丢失。Downgrade前后都验证P1 core count/hash，P2 before-state另验证spec count/hash/strict decode与trigger inventory。

- [ ] **同一 command 确认 GREEN**

Run:

~~~powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup.DatabaseBackupTests.test_manifest_records_p2_content_fingerprints backend.tests.test_database_backup.DatabaseBackupTests.test_verify_detects_p2_content_tampering backend.tests.test_p2_migration.P2CoreProjectionMigrationTests.test_nonempty_p1_core_fingerprints_survive_p2_upgrade_downgrade_reupgrade backend.tests.test_p2_migration.P2OperationalDocumentationTests -v
~~~

Expected GREEN：full-P2/spec count/hash、两个guard trigger inventory、三个nonempty P1-core projection逐phase equality、tamper detection与完整rollback runbook assertions全部通过。

**Exit gate:** 在任何 Live migration 前执行下面“隔离迁移演练”。当前用户已明确授权在备份、停写与隔离演练全绿后连续执行本路线图所需的 Live additive migration；该授权不允许跳过任一 guard、downgrade rehearsal 或 writer-drain 检查。

---

## 隔离迁移演练与 guarded downgrade

获得创建P0 snapshot的明确授权后，以下命令用CLI JSON返回值解析restore-check隔离副本；不得手写或猜测路径，也不得把 `$p2DrillDb` 指向Live。

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
function Assert-LegacyFingerprintMap {
  param([Parameter(Mandatory = $true)]$Fingerprint, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Tables)
  foreach ($table in $Tables) {
    if (-not ($Fingerprint.database.tableCounts.PSObject.Properties.Name -contains $table)) { throw "$Label count map is missing legacy table $table." }
    if (-not ($Fingerprint.database.tableSha256.PSObject.Properties.Name -contains $table)) { throw "$Label hash map is missing legacy table $table." }
  }
}
function Assert-LegacyFingerprintEqual {
  param([Parameter(Mandatory = $true)]$Expected, [Parameter(Mandatory = $true)]$Actual, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Tables)
  Assert-LegacyFingerprintMap $Expected 'baseline' $Tables
  Assert-LegacyFingerprintMap $Actual $Label $Tables
  foreach ($table in $Tables) {
    if ($Expected.database.tableCounts.$table -ne $Actual.database.tableCounts.$table) { throw "$Label changed legacy count for $table." }
    if ($Expected.database.tableSha256.$table -ne $Actual.database.tableSha256.$table) { throw "$Label changed legacy hash for $table." }
  }
}
function Assert-StableContentFingerprintMap {
  param([Parameter(Mandatory = $true)]$Fingerprint, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Keys)
  foreach ($key in $Keys) {
    if (-not ($Fingerprint.database.contentCounts.PSObject.Properties.Name -contains $key)) { throw "$Label content count map is missing stable key $key." }
    if (-not ($Fingerprint.database.contentSha256.PSObject.Properties.Name -contains $key)) { throw "$Label content hash map is missing stable key $key." }
  }
}
function Assert-StableContentFingerprintEqual {
  param([Parameter(Mandatory = $true)]$Expected, [Parameter(Mandatory = $true)]$Actual, [Parameter(Mandatory = $true)][string]$Label, [Parameter(Mandatory = $true)][string[]]$Keys)
  Assert-StableContentFingerprintMap $Expected 'baseline' $Keys
  Assert-StableContentFingerprintMap $Actual $Label $Keys
  foreach ($key in $Keys) {
    if ($Expected.database.contentCounts.$key -ne $Actual.database.contentCounts.$key) { throw "$Label changed stable content count for $key." }
    if ($Expected.database.contentSha256.$key -ne $Actual.database.contentSha256.$key) { throw "$Label changed stable content hash for $key." }
  }
}
$p2LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
$p2StableContentKeys = @('paperIds','explainers','translations','notes','paperVectors','p1CoreDocumentSources','p1CoreGeneratedArtifacts','p1CoreProcessingJobs')
$p2Create = Invoke-CheckedNative 'P2 backup create' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup create `
  --database data/app.db `
  --output-directory data/backups `
  --label pre-p2-processing } | ConvertFrom-Json
if (-not $p2Create.ok) { throw 'P2 backup create failed.' }
$p2Verify = Invoke-CheckedNative 'P2 backup verify' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup verify `
  --backup $p2Create.backupPath `
  --manifest $p2Create.manifestPath } | ConvertFrom-Json
if (-not $p2Verify.ok -or $p2Verify.logicalSha256 -ne $p2Create.logicalSha256) {
  throw 'P2 backup verification mismatch.'
}
$p2Restore = Invoke-CheckedNative 'P2 restore-check' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup restore-check `
  --backup $p2Create.backupPath `
  --manifest $p2Create.manifestPath `
  --output-directory data/backups/restore-checks } | ConvertFrom-Json
if (-not $p2Restore.ok -or $p2Restore.logicalSha256 -ne $p2Verify.logicalSha256) {
  throw 'P2 restore-check mismatch.'
}
$p2DrillDb = (Resolve-Path -LiteralPath $p2Restore.restoredPath).Path
$liveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
if ($p2DrillDb -eq $liveDb) { throw 'P2 drill database resolves to Live data/app.db.' }
$p2RestoreRoot = (Resolve-Path -LiteralPath 'data/backups/restore-checks').Path
$p2RestorePrefix = $p2RestoreRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$p2ValidationDir = Split-Path -Parent $p2DrillDb
if (-not $p2DrillDb.StartsWith($p2RestorePrefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Split-Path -Leaf $p2ValidationDir).StartsWith('restore-validation-', [StringComparison]::Ordinal)) {
  throw 'P2 drill database is outside a restore-check directory.'
}

$p2PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p2HadDbPath = $null -ne $p2PreviousDbPath
$p2PreviousRestoreRoot = [Environment]::GetEnvironmentVariable('MIGRATION_RESTORE_ROOT', 'Process')
$p2HadRestoreRoot = $null -ne $p2PreviousRestoreRoot
$env:DB_PATH = $p2DrillDb
$env:MIGRATION_RESTORE_ROOT = $p2RestoreRoot
try {
  $p2Before = Invoke-CheckedNative 'P2 pre-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2DrillDb } | ConvertFrom-Json
  if (-not $p2Before.ok -or $p2Before.database.alembicVersion -ne '20260807_01') { throw 'P2 drill must start at exact revision 20260807_01.' }
  Assert-LegacyFingerprintMap $p2Before 'P2 pre-upgrade' $p2LegacyTables
  Assert-StableContentFingerprintMap $p2Before 'P2 pre-upgrade' $p2StableContentKeys

  Invoke-CheckedNative 'P2 restored-copy upgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_02 }
  Invoke-CheckedNative 'P2 restored-copy validation' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration.P2RestoredCopyValidationTests -v }
  $p2AfterUpgrade = Invoke-CheckedNative 'P2 post-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2DrillDb } | ConvertFrom-Json
  if (-not $p2AfterUpgrade.ok -or $p2AfterUpgrade.database.alembicVersion -ne '20260807_02') { throw 'P2 post-upgrade fingerprint is not at 20260807_02.' }
  Assert-LegacyFingerprintEqual $p2Before $p2AfterUpgrade 'P2 upgrade' $p2LegacyTables
  Assert-StableContentFingerprintEqual $p2Before $p2AfterUpgrade 'P2 upgrade' $p2StableContentKeys

  $p2DownOutput = & .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini downgrade 20260807_01 2>&1
  $p2DownExit = $LASTEXITCODE
  if ($p2DownExit -ne 0) {
    $p2DownText = $p2DownOutput -join [Environment]::NewLine
    if ($p2DownText -notmatch 'P2_DOWNGRADE_BLOCKED_NONEMPTY') {
      throw "P2 downgrade failed unexpectedly with exit code $p2DownExit."
    }
    Invoke-CheckedNative 'explicit isolated P2 downgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini -x allow_p2_data_loss=true downgrade 20260807_01 }
  }
  Invoke-CheckedNative 'P1 validation after P2 downgrade' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p1_migration.P1RestoredCopyValidationTests -v }
  $p2AfterDowngrade = Invoke-CheckedNative 'P2 post-downgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2DrillDb } | ConvertFrom-Json
  if (-not $p2AfterDowngrade.ok -or $p2AfterDowngrade.database.alembicVersion -ne '20260807_01') { throw 'P2 downgrade did not return to 20260807_01.' }
  Assert-LegacyFingerprintEqual $p2Before $p2AfterDowngrade 'P2 downgrade' $p2LegacyTables
  Assert-StableContentFingerprintEqual $p2Before $p2AfterDowngrade 'P2 downgrade' $p2StableContentKeys

  Invoke-CheckedNative 'P2 restored-copy re-upgrade' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_02 }
  Invoke-CheckedNative 'P2 restored-copy re-upgrade validation' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration.P2RestoredCopyValidationTests -v }
  $p2AfterReupgrade = Invoke-CheckedNative 'P2 post-re-upgrade inspect' { .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2DrillDb } | ConvertFrom-Json
  if (-not $p2AfterReupgrade.ok -or $p2AfterReupgrade.database.alembicVersion -ne '20260807_02') { throw 'P2 re-upgrade did not return to 20260807_02.' }
  Assert-LegacyFingerprintEqual $p2Before $p2AfterReupgrade 'P2 re-upgrade' $p2LegacyTables
  Assert-StableContentFingerprintEqual $p2Before $p2AfterReupgrade 'P2 re-upgrade' $p2StableContentKeys
} finally {
  if ($p2HadDbPath) { $env:DB_PATH = $p2PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
  if ($p2HadRestoreRoot) { $env:MIGRATION_RESTORE_ROOT = $p2PreviousRestoreRoot } else { Remove-Item Env:MIGRATION_RESTORE_ROOT -ErrorAction SilentlyContinue }
}
~~~

Expected：restored copy可upgrade→validate→downgrade→validate→upgrade；冻结的12张legacy表`papers|progress|paper_reviews|notes|favorites|translations|paper_vectors|cite_edges|ingest_jobs|job_candidates|job_schedules|schema_migrations`的count/hash，以及P1 stable content keys`paperIds|explainers|translations|notes|paperVectors|p1CoreDocumentSources|p1CoreGeneratedArtifacts|p1CoreProcessingJobs`的content count/hash，每一步都存在且一致。三张P1 domain表允许非空；三个`p1Core*`keys必须逐row保护其固定P1 columns。包含P2-added columns的full-P2 keys`documentSources|generatedArtifacts|processingJobs|processingJobSpecs`不做跨revision相等比较，但每个P2 phase必须存在且`processingJobSpecs` count等于processing_jobs count、所有spec strict-decode，两个spec guard trigger inventory必须精确；这些不能替代或省略对应core key。如果演练副本已经写入P2-only rows，第一次downgrade必须被guard拒绝；只有确认副本可丢弃并显式加`-x allow_p2_data_loss=true`后才能演练destructive downgrade。不得对Live使用该x argument。

---

## Gate-authorized Live additive upgrade

只有上面的恢复副本演练、P2 全量测试、fresh backup、writer drain 和规格/质量审查全部有最新绿灯证据时，才执行已经授权的 Live additive upgrade。命令必须显式固定 Live 路径、验证起始 revision、比较 legacy 表与 P1 stable content keys 的 count/hash，并在 finally 中清理进程环境：

~~~powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$p2LiveDb = (Resolve-Path -LiteralPath 'data/app.db').Path
$p2LiveBefore = .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2LiveDb | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $p2LiveBefore.ok -or $p2LiveBefore.database.alembicVersion -ne '20260807_01') { throw 'Live database is not at the P2 base revision 20260807_01.' }
$p2LegacyTables = @('papers','progress','paper_reviews','notes','favorites','translations','paper_vectors','cite_edges','ingest_jobs','job_candidates','job_schedules','schema_migrations')
$p2StableContentKeys = @('paperIds','explainers','translations','notes','paperVectors','p1CoreDocumentSources','p1CoreGeneratedArtifacts','p1CoreProcessingJobs')
foreach ($p2Table in $p2LegacyTables) {
  if (-not ($p2LiveBefore.database.tableCounts.PSObject.Properties.Name -contains $p2Table)) { throw "Live P2 pre-upgrade count map is missing legacy table $p2Table." }
  if (-not ($p2LiveBefore.database.tableSha256.PSObject.Properties.Name -contains $p2Table)) { throw "Live P2 pre-upgrade hash map is missing legacy table $p2Table." }
}
foreach ($p2Key in $p2StableContentKeys) {
  if (-not ($p2LiveBefore.database.contentCounts.PSObject.Properties.Name -contains $p2Key)) { throw "Live P2 pre-upgrade content count map is missing stable key $p2Key." }
  if (-not ($p2LiveBefore.database.contentSha256.PSObject.Properties.Name -contains $p2Key)) { throw "Live P2 pre-upgrade content hash map is missing stable key $p2Key." }
}
$p2PreviousDbPath = [Environment]::GetEnvironmentVariable('DB_PATH', 'Process')
$p2HadDbPath = $null -ne $p2PreviousDbPath
$env:DB_PATH = $p2LiveDb
try {
  .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini upgrade 20260807_02
  if ($LASTEXITCODE -ne 0) { throw 'Live P2 additive upgrade failed.' }
  $p2Current = @(& .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini current | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
  if ($LASTEXITCODE -ne 0 -or $p2Current.Count -ne 1 -or $p2Current[0] -notmatch '^20260807_02\s+\(head\)$') { throw 'Live Alembic current is not uniquely 20260807_02 (head).' }
  $p2LiveAfter = .\.venv\Scripts\python.exe -B -m backend.app.cli.database_backup inspect --database $p2LiveDb | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $p2LiveAfter.ok -or $p2LiveAfter.database.alembicVersion -ne '20260807_02') { throw 'Live fingerprint did not reach 20260807_02.' }
  foreach ($p2Table in $p2LegacyTables) {
    if (-not ($p2LiveAfter.database.tableCounts.PSObject.Properties.Name -contains $p2Table)) { throw "Live P2 post-upgrade count map is missing legacy table $p2Table." }
    if (-not ($p2LiveAfter.database.tableSha256.PSObject.Properties.Name -contains $p2Table)) { throw "Live P2 post-upgrade hash map is missing legacy table $p2Table." }
    if ($p2LiveBefore.database.tableCounts.$p2Table -ne $p2LiveAfter.database.tableCounts.$p2Table) { throw "Live P2 changed legacy count for $p2Table." }
    if ($p2LiveBefore.database.tableSha256.$p2Table -ne $p2LiveAfter.database.tableSha256.$p2Table) { throw "Live P2 changed legacy hash for $p2Table." }
  }
  foreach ($p2Key in $p2StableContentKeys) {
    if (-not ($p2LiveAfter.database.contentCounts.PSObject.Properties.Name -contains $p2Key)) { throw "Live P2 post-upgrade content count map is missing stable key $p2Key." }
    if (-not ($p2LiveAfter.database.contentSha256.PSObject.Properties.Name -contains $p2Key)) { throw "Live P2 post-upgrade content hash map is missing stable key $p2Key." }
    if ($p2LiveBefore.database.contentCounts.$p2Key -ne $p2LiveAfter.database.contentCounts.$p2Key) { throw "Live P2 changed stable content count for $p2Key." }
    if ($p2LiveBefore.database.contentSha256.$p2Key -ne $p2LiveAfter.database.contentSha256.$p2Key) { throw "Live P2 changed stable content hash for $p2Key." }
  }
  foreach ($p2RequiredTable in @('document_sources','generated_artifacts','processing_jobs','document_chunks','obsidian_exports','paper_artifact_heads','processing_job_events','ocr_page_checkpoints')) {
    if (-not ($p2LiveAfter.database.tableCounts.PSObject.Properties.Name -contains $p2RequiredTable)) { throw "Live P2 schema inventory is missing $p2RequiredTable." }
  }
  if ($p2LiveAfter.database.quickCheck -ne 'ok' -or $p2LiveAfter.database.integrityCheck -ne 'ok' -or $p2LiveAfter.database.foreignKeyViolations -ne 0) { throw 'Live P2 read-only health validation failed.' }
} finally {
  if ($p2HadDbPath) { $env:DB_PATH = $p2PreviousDbPath } else { Remove-Item Env:DB_PATH -ErrorAction SilentlyContinue }
}
~~~

Expected：Live从唯一head`20260807_01`前进到唯一head`20260807_02`；只读inspect的schema inventory/健康检查、冻结12张legacy表的count/hash与P1 stable content keys`paperIds|explainers|translations|notes|paperVectors|p1CoreDocumentSources|p1CoreGeneratedArtifacts|p1CoreProcessingJobs`的content count/hash全部存在且一致，任一缺键或hash漂移都阻止阶段出口。三张P1 domain表允许非空，三个`p1Core*`keys必须保护其固定P1 columns；P2 after-state还必须有`documentSources|generatedArtifacts|processingJobs|processingJobSpecs`，后两者count一致、所有spec可strict decode，两个spec guard trigger SQL hash精确。Full-P2 keys不做跨revision相等比较，但不能替代core protection。`P2RestoredCopyValidationTests`只在`$p2DrillDb`运行，绝不以Live`DB_PATH`运行。运行时回滚保留additive schema；前进到P3前不得对Live执行downgrade或`allow_p2_data_loss=true`。

---

## 阶段门禁

- [ ] P0 fresh backup 已经 create、independent verify、isolated restore-check，路径与 logical SHA 已记录。
- [ ] P1 head `20260807_01`、composition root、unified error DTO、test app factory 和 rollout settings 全绿。
- [ ] P2 migration 在恢复副本完成 upgrade→validate→downgrade→validate→upgrade；没有多 Alembic head。
- [ ] writer drain 后 Live 显式 upgrade 到 `20260807_02`；`alembic current`、read-only inspect/schema inventory 与 legacy count/hash 门禁全绿；restored-copy test 从未指向 Live。
- [ ] ProcessingQueue 的 enqueue/get/list/cancel/retry/claim_next/report_progress/complete/fail、双连接 fencing、orphan recovery 和 crash recovery 全绿。
- [ ] public job status 始终只有 `queued|running|succeeded|failed|cancelled`；等待和 orphan 没有新 public status。
- [ ] native success/failure 的 OCR 调用均为零；OCR success/failure 的 native 调用均为零。
- [ ] 默认OCR_ENABLED=0时explicit OCR为409且registry/provider/transport/PDF upload/row均零；Fake tests显式enable。
- [ ] enabled后 `ocrProvider=deepseek` 同步503，source/job row、transport construction和network call均零。
- [ ] 条件计划已链接并显式记录状态：官方资料未齐则 `BLOCKED_BY_PROVIDER_CONTRACT` 且不影响 Fake slice；用户已提供完整资料则条件计划 Task 0–5 全部成为必执行门禁，未通过不得声称真实 DeepSeek OCR 完成。
- [ ] Fake OCR 仅存在于测试 override，production registry 无 fake。
- [ ] Fake/native强制matrix覆盖single-page text、multi-page scanned、mixed、encrypted、empty、invalid、timeout、429、500、cancel、resume、cache、PDF/config drift；真实DeepSeek contract cases继续fail-closed而非skip。
- [ ] ready SourceDocument→explainer publish 的 source relation、head、legacy projection 和 job transaction 全绿。
- [ ] Worker/HTTP/React 测试无 Live DB、真实 Provider、wall-clock sleep、后台残留进程。
- [ ] `public/`、React JSX/CSS/文案/布局零变化；P2 只改 Gateway/Hook seam。
- [ ] P2 backup count/hash 与 tamper detection 已覆盖，runtime rollback 保留 additive tables。
- [ ] 阶段规格审查和代码质量审查完成；发现的问题修复后重复 targeted 与 full verification。

---

## 最终验证

按顺序运行；任一失败即停止，不把后续绿灯掩盖前面的失败。

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
Invoke-CheckedNative 'P2 migration tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_p2_migration -v }
Invoke-CheckedNative 'processing queue tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_queue -v }
Invoke-CheckedNative 'OCR provider gate tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_provider_gate -v }
Invoke-CheckedNative 'source document pipeline tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_source_document_pipeline -v }
Invoke-CheckedNative 'OCR explainer slice tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_ocr_explainer_slice -v }
Invoke-CheckedNative 'processing jobs API tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_jobs_api -v }
Invoke-CheckedNative 'processing worker tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_processing_worker -v }
Invoke-CheckedNative 'database backup tests' { .\.venv\Scripts\python.exe -B -m unittest backend.tests.test_database_backup -v }
Invoke-CheckedNative 'backend full suite' { .\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p "test_*.py" -v }
Invoke-CheckedNative 'legacy Python full suite' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_*.py" -v }
Invoke-CheckedNative 'MCP characterization' { .\.venv\Scripts\python.exe -B -m unittest discover -s test -p "test_mcp_server.py" -v }
Invoke-CheckedNative 'root Node tests' { npm.cmd test }
Invoke-CheckedNative 'P2 frontend targeted tests' { npm.cmd run test:run --prefix frontend -- src/lib/api/processingGateway.test.ts src/features/reader/ArtifactPanel.test.tsx }
$p2ExitBaselineJson = Invoke-CheckedNative 'P2 exit full frontend baseline verification' { node scripts/pre-existing-failure-baseline.mjs verify --baseline contracts/pre-existing-test-failures-v1.json }
$p2ExitBaseline = $p2ExitBaselineJson | ConvertFrom-Json
$p2ExitBaselineRequiredFields = @('baselineMatched','observedSuiteExitCode','overallGreen')
foreach ($p2ExitBaselineField in $p2ExitBaselineRequiredFields) {
  if (-not ($p2ExitBaseline.PSObject.Properties.Name -contains $p2ExitBaselineField)) { throw "P2 exit baseline verifier omitted required field $p2ExitBaselineField." }
}
if ($p2ExitBaseline.baselineMatched -isnot [bool] -or $p2ExitBaseline.baselineMatched -ne $true) { throw 'P2 exit baseline verifier did not report boolean baselineMatched=true.' }
if ($p2ExitBaseline.observedSuiteExitCode -isnot [int] -and $p2ExitBaseline.observedSuiteExitCode -isnot [long]) { throw 'P2 exit baseline verifier did not report an integer observedSuiteExitCode.' }
if ($p2ExitBaseline.overallGreen -isnot [bool]) { throw 'P2 exit baseline verifier did not report boolean overallGreen.' }
$p2ExitObservedSuiteExitCode = [long]$p2ExitBaseline.observedSuiteExitCode
if (($p2ExitObservedSuiteExitCode -eq 0) -ne $p2ExitBaseline.overallGreen) { throw 'P2 exit baseline verifier reported inconsistent observedSuiteExitCode and overallGreen.' }
Invoke-CheckedNative 'frontend lint' { npm.cmd run lint --prefix frontend }
Invoke-CheckedNative 'frontend typecheck' { npm.cmd run typecheck --prefix frontend }
Invoke-CheckedNative 'frontend build' { npm.cmd run build --prefix frontend }
Invoke-CheckedNative 'frontend e2e' { npm.cmd run e2e --prefix frontend }
$p2Heads = @(Invoke-CheckedNative 'P2 Alembic heads' { .\.venv\Scripts\python.exe -B -m alembic -c backend/alembic.ini heads } | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
if ($p2Heads.Count -ne 1 -or $p2Heads[0] -notmatch '^20260807_02\s+\(head\)$') { throw 'P2 migration graph does not expose exactly one 20260807_02 (head).' }
Invoke-CheckedNative 'git diff check' { git diff --check }
~~~

Expected：

- targeted P2 suites 全部 0 failures/0 errors/0 skips；若环境明确不支持 SQLite FTS 不影响 P2。
- backend 全量、legacy Python、Node 和 targeted React regression 全绿；full frontend suite 必须 exact-match P0.1 v1，raw 0 可标 green，已审核 raw non-zero 只能报告 `overallGreen=false`。
- Frontend lint/typecheck/build/E2E成功，production build无测试fake。
- `git diff --check` 无 whitespace error；`git diff -- public frontend/src/features/reader/ArtifactPanel.tsx frontend/src/features/reader/*.css` 为空。
- 报告精确 test counts、Alembic current/head、恢复副本路径与 hash、DeepSeek 零调用证据及任何未执行项。全部 P2 门禁为绿时连续进入 P3；失败时停止推进并保持 P2 rollback 值可用。

---

## 自审清单

- [ ] 本计划没有 Git add/commit/push/branch 步骤。
- [ ] 每个行为任务都有 RED、同一 targeted command 的 observed RED、最小实现、同一 command GREEN。
- [ ] 没有真实 DeepSeek OCR Adapter、未核验 endpoint、OCR SDK 或 chat-completions 伪 OCR。
- [ ] 没有跨 mode fallback、请求线程长任务、自动 retry loop 或 HTTP disconnect 隐式 cancel。
- [ ] 四组 hard fields、固定 public status、cache key、typed errors、revision/down_revision 均完整。
- [ ] migration/downgrade/runtime rollback 和 backup fingerprint 有可执行命令与停止条件。
- [ ] `spec_json` 是唯一 versioned业务请求来源；enqueue/claim/retry/recovery、idempotency、backup/inventory 与 P5 恢复都验证同一 canonical bytes/hash，`progress_json` 只保存安全进度。
- [ ] 所有章节、命令、测试矩阵与实现边界均完整具体，不含截断或延后实现标记。
