from __future__ import annotations

import json
import ntpath

from sqlalchemy import text as sa_text
from typing import Any

import anyio
from fastapi import APIRouter, Request
from starlette.responses import Response, StreamingResponse

from backend.app.domain import MissingPaperError
from backend.app.api.middleware.ndjson import ndjson_response


_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def create_legacy_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    async def get_settings(request: Request) -> Response:
        try:
            view = await _settings(request).view()
        except Exception as error:
            return _settings_error(error)
        return _json_response(view)

    @router.post("/api/settings")
    async def update_settings(request: Request) -> Response:
        body = await _body(request)
        try:
            await _settings(request).update(body)
        except Exception as error:
            return _settings_error(error)
        return _json_response({"ok": True})

    @router.post("/api/test-llm")
    async def test_llm(request: Request) -> Response:
        try:
            result = await _settings(request).test_llm()
        except Exception as error:
            return _settings_error(error)
        return _json_response(result)

    @router.post("/api/ingest")
    async def ingest(request: Request) -> Response:
        body = await _body(request)
        if not body.get("query") or not _valid_sources(body.get("sources")):
            return _json_response(
                {"ok": False, "error": "缺少检索方向或数据源"}, 400
            )
        try:
            result = await _search_coordinator(request).ingest(body)
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(result)

    @router.post("/api/expand")
    async def expand(request: Request) -> Response:
        body = await _body(request)
        try:
            result = await _search_coordinator(request).expand(body)
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(result)

    @router.post("/api/translate-text")
    async def translate_text(request: Request) -> Response:
        body = await _body(request)
        selected = str(body.get("text") or "").strip()
        if not selected:
            return _json_response({"ok": False, "error": "缺少文本"}, 400)
        if len(selected) > 6000:
            return _json_response(
                {"ok": False, "error": "选区过长，请缩短后再试"}, 413
            )
        try:
            result = await _search_coordinator(request).translate_text(selected)
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(result)

    @router.post("/api/ocr-md")
    async def ocr_md(request: Request) -> Response:
        """PDF → Markdown（OCR）：调用 agent ocr-md（方案 A 官方提示词）。
        进度 OCRPG::i/n → NDJSON progress 事件，Markdown → 终态事件 markdown 字段。"""
        body = await _body(request)
        paper_id = _safe_base(body.get("id"))
        if not paper_id:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        try:
            row = await _services(request).library_queries.get_paper(paper_id)
        except Exception:
            row = None
        stored = row.get("pdf_path") if isinstance(row, dict) else None
        try:
            file = _pdf_files(request).resolve_for_id(paper_id, stored_path=stored)
        except Exception as error:
            return _safe_json_error(error)
        if file is None:
            return _json_response(
                {"ok": False, "error": "无本地 PDF，请先下载 PDF 入库后再执行 OCR"}, 409
            )
        return ndjson_response(
            _agent_events(
                request,
                "ocr-md",
                ["--id", paper_id],
                terminal_fields={"markdown": ""},
                stdout_text_field="markdown",
            )
        )

    @router.get("/api/ocr-md")
    async def get_ocr_md(request: Request) -> Response:
        """读已保存的 PDF→Markdown(OCR) 结果；无记录返回空文本（与 /api/explainer 一致）。"""
        paper_id = _safe_base(request.query_params.get("id"))
        if not paper_id:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        try:
            async with request.app.state.session_factory() as session:
                result = await session.execute(
                    sa_text("SELECT content FROM ocr_markdown WHERE paper_id = :pid"),
                    {"pid": paper_id},
                )
                row = result.first()
        except Exception:
            # 表不存在（从未执行过 OCR）→ 视同无记录
            return _markdown_response(None)
        return _markdown_response(row[0] if row else None)

    @router.get("/api/ocr-md-batch")
    async def ocr_batch_status(request: Request) -> Response:
        """批量 OCR 统计：总数 / 已有 OCR / 有 PDF 待转换 / 缺 PDF。"""
        try:
            async with request.app.state.session_factory() as session:
                rows = (
                    await session.execute(sa_text("SELECT id, pdf_path FROM papers"))
                ).all()
                try:
                    have_rows = (
                        await session.execute(
                            sa_text(
                                "SELECT paper_id FROM ocr_markdown "
                                "WHERE TRIM(content) != ''"
                            )
                        )
                    ).all()
                except Exception:
                    have_rows = []  # 表不存在（从未执行过 OCR）
            have = {str(row[0]) for row in have_rows}
            resolver = _pdf_files(request)
            total = len(rows)
            has_ocr = len(have)
            with_pdf = 0
            pending = 0
            no_pdf = 0
            for row in rows:
                paper_id = str(row[0])
                stored = row[1] if isinstance(row[1], str) and row[1].strip() else None
                resolved = resolver.resolve_for_id(paper_id, stored_path=stored)
                if resolved is not None:
                    with_pdf += 1
                    if paper_id not in have:
                        pending += 1
                else:
                    no_pdf += 1
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(
            {
                "ok": True,
                "total": total,
                "hasOcr": has_ocr,
                "withPdf": with_pdf,
                "pending": pending,
                "noPdf": no_pdf,
            }
        )

    @router.post("/api/ocr-md-batch")
    async def ocr_batch(request: Request) -> Response:
        """批量 PDF→Markdown(OCR)：只补「有 PDF 且无 OCR 落库」的论文；NDJSON 流式进度，
        汇总 JSON 包在终态事件 summary 字段（与 /api/explain-batch 同契约）。"""
        body = await _body(request)
        return ndjson_response(
            _agent_events(
                request,
                "ocr-md-batch",
                _optional_args(body, "limit"),
                terminal_fields={
                    "summary": {"total": 0, "done": 0, "failed": [], "skipped_no_pdf": []}
                },
                stdout_object_field="summary",
            )
        )

    @router.get("/api/reviews")
    async def list_reviews(request: Request) -> Response:
        try:
            snapshot = await _review_scheduler(request).list_snapshot()
        except Exception as error:
            return _safe_json_error(error)
        return _json_response({"ok": True, **snapshot})

    @router.post("/api/reviews/start")
    async def start_review(request: Request) -> Response:
        body = await _body(request)
        paper_id = _safe_base(body.get("id"))
        if not paper_id:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        try:
            plan = await _review_scheduler(request).start(paper_id)
        except Exception as error:
            return _safe_json_error(error)
        if plan is None:
            return _json_response({"ok": False, "error": "论文不存在"}, 404)
        return _json_response({"ok": True, "plan": plan})

    @router.post("/api/reviews/complete")
    async def complete_review(request: Request) -> Response:
        body = await _body(request)
        paper_id = _safe_base(body.get("id"))
        if not paper_id:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        try:
            plan = await _review_scheduler(request).complete(paper_id)
            if plan is None:
                return _json_response(
                    {"ok": False, "error": "尚未加入复习计划"}, 404
                )
            reviews = await _review_scheduler(request).list_snapshot()
        except Exception as error:
            return _safe_json_error(error)
        return _json_response({"ok": True, "plan": plan, "reviews": reviews})

    @router.get("/api/note")
    async def get_note(request: Request) -> Response:
        try:
            content = await _artifact_store(request).read_note(
                _safe_base(request.query_params.get("id"))
            )
        except Exception as error:
            return _safe_text_error(error)
        return _markdown_response(content)

    @router.post("/api/note")
    async def set_note(request: Request) -> Response:
        body = await _body(request)
        try:
            await _artifact_store(request).write_note(
                _safe_base(body.get("id")), body.get("content")
            )
        except Exception as error:
            return _safe_json_error(error)
        return _json_response({"ok": True})

    @router.get("/api/explainer")
    async def get_explainer(request: Request) -> Response:
        try:
            content = await _artifact_store(request).read_content(
                _safe_base(request.query_params.get("id")), "explainer"
            )
        except Exception as error:
            return _safe_text_error(error)
        return _markdown_response(content)

    @router.get("/api/translation")
    async def get_translation(request: Request) -> Response:
        try:
            content = await _artifact_store(request).read_content(
                _safe_base(request.query_params.get("id")), "translation"
            )
        except Exception as error:
            return _safe_text_error(error)
        return _markdown_response(content)

    @router.get("/api/title-translations")
    async def title_translation_status(request: Request) -> Response:
        try:
            status = await _artifact_store(request).title_translation_status()
        except Exception as error:
            return _safe_json_error(error)
        return _json_response({"ok": True, **status})

    @router.get("/api/explain-batch")
    async def explain_batch_status(request: Request) -> Response:
        try:
            status = await _artifact_store(request).explain_batch_status()
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(status)

    @router.get("/api/scan-pdfs")
    async def scan_pdfs(request: Request) -> Response:
        directory = str(request.query_params.get("dir") or "").strip()
        if not directory:
            return _json_response({"ok": False, "error": "缺少文件夹路径"}, 400)
        try:
            coordinator = _optional_service(request, "search_coordinator")
            result = (
                coordinator.scan_pdfs(_pdf_files(request), directory)
                if coordinator is not None
                else _pdf_files(request).scan(directory)
            )
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(result)

    @router.get("/api/pdf/status")
    async def pdf_status(request: Request) -> Response:
        identifier = str(request.query_params.get("id") or "")
        row = None
        try:
            row = await _services(request).library_queries.get_paper(identifier)
        except Exception:
            row = None
        file = _pdf_files(request).resolve_for_id(
            identifier,
            stored_path=row.get("pdf_path") if isinstance(row, dict) else None,
        )
        can_download = bool(
            row
            and (
                row.get("pdf_url")
                or row.get("arxiv_id")
                or "arxiv" in str(row.get("url") or "").lower()
                or bool(__import__("re").match(r"^\d{4}\.\d{4,5}", identifier))
            )
        )
        return _json_response(
            {
                "ok": True,
                "id": identifier,
                "hasPdf": file is not None,
                "size": file.size if file is not None else 0,
                "path": str(file.path) if file is not None else "",
                "canDownload": can_download,
            }
        )

    @router.get("/api/citegraph")
    async def citation_graph(request: Request) -> Response:
        try:
            result = await _search_coordinator(request).citation_graph()
        except Exception as error:
            return _safe_json_error(error)
        return _json_response(result)

    async def _stored_pdf_path(request: Request, identifier: str) -> str | None:
        try:
            row = await _services(request).library_queries.get_paper(identifier)
        except Exception:
            row = None
        return row.get("pdf_path") if isinstance(row, dict) else None

    async def _serve_pdf_bytes(request: Request) -> Response:
        identifier = str(request.query_params.get("id") or "")
        opened = _pdf_files(request).open_for_id(
            identifier, stored_path=await _stored_pdf_path(request, identifier)
        )
        if opened is None:
            return Response("not found", status_code=404, media_type="text/plain")
        return _opened_pdf_response(
            request,
            opened,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/pdfbytes", operation_id="legacy_pdfbytes_get")
    async def pdf_bytes_get(request: Request) -> Response:
        return await _serve_pdf_bytes(request)

    @router.head("/pdfbytes", operation_id="legacy_pdfbytes_head")
    async def pdf_bytes_head(request: Request) -> Response:
        return await _serve_pdf_bytes(request)

    @router.api_route(
        "/pdfbytes",
        methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"],
        include_in_schema=False,
    )
    async def pdf_bytes_unguarded_method(request: Request) -> Response:
        return await _serve_pdf_bytes(request)

    async def _serve_paper_pdf(request: Request, paper_path: str) -> Response:
        identifier = paper_path
        if identifier.lower().endswith(".pdf"):
            identifier = identifier[:-4]
        opened = _pdf_files(request).open_for_id(
            identifier, stored_path=await _stored_pdf_path(request, identifier)
        )
        if opened is None:
            return Response("PDF not found", status_code=404, media_type="text/plain")
        return _opened_pdf_response(request, opened, media_type="application/pdf")

    @router.get("/papers/{paper_path:path}", operation_id="legacy_papers_get")
    async def paper_pdf_get(request: Request, paper_path: str) -> Response:
        return await _serve_paper_pdf(request, paper_path)

    @router.head("/papers/{paper_path:path}", operation_id="legacy_papers_head")
    async def paper_pdf_head(request: Request, paper_path: str) -> Response:
        return await _serve_paper_pdf(request, paper_path)

    @router.api_route(
        "/papers/{paper_path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"],
        include_in_schema=False,
    )
    async def paper_pdf_unguarded_method(
        request: Request,
        paper_path: str,
    ) -> Response:
        return await _serve_paper_pdf(request, paper_path)

    @router.post("/api/title-translations")
    async def translate_titles(request: Request) -> Response:
        body = await _body(request)
        return ndjson_response(
            _agent_events(
                request,
                "title-translations",
                _optional_args(body, "limit"),
                terminal_fields={"summary": {}},
            )
        )

    @router.post("/api/search")
    async def search_legacy(request: Request) -> Response:
        body = await _body(request)
        if not str(body.get("query") or "").strip() or not _valid_sources(body.get("sources")):
            return _json_response({"ok": False, "error": "缺少搜索方向或数据源"}, 400)
        args = ["--query", str(body["query"]), "--sources", ",".join(_valid_sources(body["sources"]))]
        # 与旧 Node 对齐：years/max/min-relevance/expand/only-a/queries 必须全部透传，
        # 否则前端生成/编辑的检索词与高级选项会被静默丢弃。
        years = body.get("years")
        args.extend(("--years", str(years) if years not in (None, "") else "2024-2026"))
        try:
            max_candidates = min(int(body.get("max") or 10), 60)
        except (TypeError, ValueError):
            max_candidates = 10
        args.extend(("--max", str(max_candidates)))
        if body.get("minRelevance") is not None:
            args.extend(("--min-relevance", str(body["minRelevance"])))
        if body.get("expand"):
            args.append("--expand")
        if body.get("onlyA"):
            args.append("--only-a")
        queries = body.get("queries")
        if isinstance(queries, list) and [item for item in queries if str(item or "").strip()]:
            args.extend(
                ("--queries", json.dumps([str(item) for item in queries], ensure_ascii=False))
            )
        return ndjson_response(
            _agent_events(
                request,
                "search",
                args,
                terminal_fields={"candidates": []},
                # agent search 的 stdout 是纯 JSON 数组，需包装进 candidates 字段。
                stdout_array_field="candidates",
            )
        )

    @router.post("/api/ingest-selected")
    async def ingest_selected(request: Request) -> Response:
        body = await _body(request)
        candidates = body.get("candidates")
        if not isinstance(candidates, list):
            candidates = []

        async def events():
            service = _optional_service(request, "legacy_ingest")
            stream = getattr(service, "ingest_selected_events", None)
            if not callable(stream):
                yield {
                    "type": "done",
                    "ok": False,
                    "added": 0,
                    "error": "provider unavailable",
                }
                return
            try:
                async for event in stream(
                    [item for item in candidates if isinstance(item, dict)],
                    deep=bool(body.get("deep")),
                    download_pdf=body.get("downloadPdf") is not False,
                ):
                    yield event
            except Exception as error:
                yield {
                    "type": "done",
                    "ok": False,
                    "added": 0,
                    "error": _safe_error(error),
                }

        return ndjson_response(events())

    @router.post("/api/verify-venue")
    async def verify_venue(request: Request) -> Response:
        body = await _body(request)
        sources = _valid_sources(body.get("sources"), allowed={"dblp", "semanticscholar", "openalex"})
        return ndjson_response(
            _agent_events(
                request,
                "verify-venue",
                ["--sources", ",".join(sources or ("dblp", "semanticscholar"))],
                terminal_fields={"verifications": []},
                stdin=json.dumps(body.get("candidates") if isinstance(body.get("candidates"), list) else [], ensure_ascii=False),
            )
        )

    @router.post("/api/explain")
    async def explain_legacy(request: Request) -> Response:
        body = await _body(request)
        identifier = _safe_base(body.get("id"))
        if not identifier:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        args = ["--id", identifier]
        if body.get("deep"):
            args.append("--deep")
        return ndjson_response(
            _durable_artifact_events(
                request,
                identifier,
                "explainer",
                profile="deep" if body.get("deep") else "standard",
                agent_command="explain",
                agent_args=args,
            )
        )

    @router.post("/api/explain-batch")
    async def explain_batch(request: Request) -> Response:
        body = await _body(request)
        return ndjson_response(
            _agent_events(
                request,
                "explain-batch",
                _optional_args(body, "limit"),
                terminal_fields={
                    "summary": {"total": 0, "done": 0, "failed": [], "skipped_no_pdf": []}
                },
                stdout_object_field="summary",
            )
        )

    @router.post("/api/translate")
    async def translate_legacy(request: Request) -> Response:
        body = await _body(request)
        identifier = _safe_base(body.get("id"))
        if not identifier:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        return ndjson_response(
            _durable_artifact_events(
                request,
                identifier,
                "translation",
                profile="standard",
                agent_command="translate",
                agent_args=["--id", identifier],
            )
        )

    @router.post("/api/recommend")
    async def recommend_legacy(request: Request) -> Response:
        body = await _body(request)
        identifier = _safe_base(body.get("id"))
        if not identifier:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        return ndjson_response(
            _agent_events(
                request,
                "recommend",
                ["--id", identifier, *(_optional_args(body, "limit"))],
                terminal_fields={"candidates": []},
            )
        )

    @router.post("/api/embed")
    async def embed_legacy(request: Request) -> Response:
        body = await _body(request)
        scope = "all" if body.get("scope") == "all" else "missing"
        return ndjson_response(_durable_embedding_events(request, scope))

    @router.post("/api/semsearch")
    async def semantic_search_legacy(request: Request) -> Response:
        body = await _body(request)
        query = str(body.get("query") or "")[:500]
        if not query.strip():
            return _json_response({"ok": False, "error": "缺少查询"}, 400)
        return ndjson_response(
            _agent_events(
                request,
                "semsearch",
                ["--query", query, *(_optional_args(body, "k"))],
                terminal_fields={"results": []},
            )
        )

    @router.post("/api/import-pdfs")
    async def import_pdfs(request: Request) -> Response:
        body = await _body(request)
        paths = body.get("paths") if isinstance(body.get("paths"), list) else []
        if not [value for value in paths if isinstance(value, str) and value.strip()]:
            return _json_response({"ok": False, "error": "未选择 PDF"}, 400)
        return ndjson_response(
            _agent_events(
                request,
                "import-pdfs",
                ((), ("--no-enrich",))[body.get("enrich") is False],
                terminal_fields={"total": 0, "added": 0, "dup": 0, "failed": 0},
                stdin=json.dumps(paths, ensure_ascii=False),
            )
        )

    @router.post("/api/download-pdfs")
    async def download_pdfs(request: Request) -> Response:
        body = await _body(request)
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        args = _optional_args(body, "limit")
        return ndjson_response(
            _agent_events(
                request,
                "download-pdfs",
                args,
                terminal_fields={"ok": True, "added": 0, "failed": 0},
                stdin=json.dumps([_safe_base(value) for value in ids], ensure_ascii=False),
            )
        )

    @router.post("/api/norm-venues")
    async def normalize_venues(request: Request) -> Response:
        return ndjson_response(
            _agent_events(
                request,
                "norm-venues",
                (),
                terminal_fields={"changed": 0, "mapping": {}},
            )
        )

    @router.post("/api/cite-build")
    async def build_citations(request: Request) -> Response:
        return ndjson_response(
            _agent_events(
                request,
                "citegraph",
                (),
                terminal_fields={"edges": 0, "nodes": 0},
            )
        )

    # Legacy collector jobs intentionally use the historical ingest_jobs
    # state machine.  P2 processing_jobs remain behind the typed /api/v2 API.
    @router.post("/api/jobs")
    async def create_legacy_job(request: Request) -> Response:
        body = await _body(request)
        try:
            identifier = await _legacy_ingest(request).create_job(body)
        except Exception as error:
            return _legacy_job_error(error)
        return _json_response({"ok": True, "id": identifier})

    @router.get("/api/jobs")
    async def list_legacy_jobs(request: Request) -> Response:
        try:
            rows = await _legacy_ingest(request).list_jobs()
        except Exception as error:
            return _legacy_job_error(error)
        return _json_response(rows)

    @router.get("/api/jobs/detail")
    async def legacy_job_detail(request: Request) -> Response:
        identifier = _parse_int(request.query_params.get("id"))
        if identifier is None:
            return _json_response({"ok": False, "error": "任务不存在"}, 404)
        try:
            result = await _legacy_ingest(request).get_job_detail(identifier)
        except Exception as error:
            return _legacy_job_error(error)
        return _json_response(result)

    @router.post("/api/jobs/delete")
    async def delete_legacy_job(request: Request) -> Response:
        body = await _body(request)
        identifier = _parse_int(body.get("id"))
        if identifier is not None:
            try:
                await _legacy_ingest(request).delete_job(identifier)
            except Exception as error:
                return _legacy_job_error(error)
        return _json_response({"ok": True})

    @router.post("/api/jobs/confirm")
    async def confirm_legacy_job(request: Request) -> Response:
        body = await _body(request)
        identifier = _parse_int(body.get("jobId"))
        candidates = body.get("candidates")
        if identifier is None or not isinstance(candidates, list) or not candidates:
            return _json_response({"ok": False, "error": "缺少任务或候选"}, 400)

        async def events():
            try:
                service = _legacy_ingest(request)
                normalized = [
                    item for item in candidates if isinstance(item, dict)
                ]
                stream = getattr(service, "confirm_events", None)
                if callable(stream):
                    async for event in stream(
                        identifier,
                        normalized,
                        deep=bool(body.get("deep")),
                        download_pdf=body.get("downloadPdf") is not False,
                    ):
                        yield event
                    return
                result = await service.confirm(
                    identifier,
                    normalized,
                    deep=bool(body.get("deep")),
                    download_pdf=body.get("downloadPdf") is not False,
                )
            except Exception as error:
                result = {"type": "done", "ok": False, "error": _safe_error(error)}
            yield result

        return ndjson_response(events())

    @router.get("/api/schedules")
    async def list_legacy_schedules(request: Request) -> Response:
        try:
            rows = await _legacy_ingest(request).list_schedules()
        except Exception as error:
            return _legacy_job_error(error)
        return _json_response(rows)

    @router.post("/api/schedules")
    async def create_legacy_schedule(request: Request) -> Response:
        body = await _body(request)
        try:
            identifier = await _legacy_ingest(request).create_schedule(body)
        except Exception as error:
            return _legacy_job_error(error)
        return _json_response({"ok": True, "id": identifier})

    @router.post("/api/schedules/toggle")
    async def toggle_legacy_schedule(request: Request) -> Response:
        body = await _body(request)
        identifier = _parse_int(body.get("id"))
        if identifier is not None:
            try:
                await _legacy_ingest(request).toggle_schedule(
                    identifier, bool(body.get("enabled"))
                )
            except Exception as error:
                return _legacy_job_error(error)
        return _json_response({"ok": True})

    @router.post("/api/schedules/delete")
    async def delete_legacy_schedule(request: Request) -> Response:
        body = await _body(request)
        identifier = _parse_int(body.get("id"))
        if identifier is not None:
            try:
                await _legacy_ingest(request).delete_schedule(identifier)
            except Exception as error:
                return _legacy_job_error(error)
        return _json_response({"ok": True})

    @router.get("/api/papers")
    async def list_papers(request: Request) -> Response:
        rows = await _services(request).library_queries.list_papers()
        return _json_response(rows)

    @router.api_route(
        "/api/papers",
        methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"],
        include_in_schema=False,
    )
    async def list_papers_unguarded_method(request: Request) -> Response:
        return await list_papers(request)

    @router.get("/api/paper/get")
    async def get_paper(request: Request) -> Response:
        paper_id = _safe_base(request.query_params.get("id"))
        row = await _services(request).library_queries.get_paper(paper_id)
        return _json_response(row)

    @router.post("/api/paper/add")
    async def add_paper(request: Request) -> Response:
        body = await _body(request)
        if not str(body.get("title") or "").strip():
            return _json_response({"ok": False, "error": "标题不能为空"}, 400)
        try:
            identifier = await _services(request).paper_library.add(body)
        except Exception as error:
            return _json_response({"ok": False, "error": _safe_error(error)}, 500)
        return _json_response({"ok": True, "id": identifier})

    @router.post("/api/paper/update")
    async def update_paper(request: Request) -> Response:
        body = await _body(request)
        paper_id = _safe_base(body.get("id"))
        if not paper_id:
            return _json_response({"ok": False, "error": "缺少 id"}, 400)
        fields = {name: value for name, value in body.items() if name != "id"}
        try:
            changes = await _services(request).paper_library.update(paper_id, fields)
        except Exception as error:
            return _json_response({"ok": False, "error": _safe_error(error)}, 500)
        return _json_response({"ok": True, "changes": changes})

    @router.post("/api/progress")
    async def set_progress(request: Request) -> Response:
        body = await _body(request)
        try:
            await _services(request).paper_library.set_status(
                _safe_base(body.get("id")),
                body.get("status"),
            )
        except MissingPaperError:
            return _foreign_key_error()
        except Exception as error:
            return _json_response({"ok": False, "error": _safe_error(error)}, 500)
        return _json_response({"ok": True})

    @router.post("/api/favorite")
    async def set_favorite(request: Request) -> Response:
        body = await _body(request)
        try:
            await _services(request).paper_library.set_favorite(
                _safe_base(body.get("id")),
                bool(body.get("favorite")),
            )
        except MissingPaperError:
            return _foreign_key_error()
        except Exception as error:
            return _json_response({"ok": False, "error": _safe_error(error)}, 500)
        return _json_response({"ok": True})

    @router.post("/api/delete")
    async def delete_paper(request: Request) -> Response:
        body = await _body(request)
        await _services(request).paper_library.delete(_safe_base(body.get("id")))
        return _json_response({"ok": True})

    @router.api_route(
        "/api/{api_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"],
        include_in_schema=False,
    )
    async def unknown_api(api_path: str) -> Response:
        del api_path
        return Response(
            "API not found",
            status_code=404,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    return router


def _services(request: Request) -> Any:
    return request.app.state.container.legacy


def _settings(request: Request) -> Any:
    legacy = _services(request)
    service = getattr(legacy, "settings", None)
    if service is None:
        service = getattr(legacy, "settings_service", None)
    if service is None:
        service = getattr(request.app.state.container, "settings_service", None)
    if service is None:
        raise RuntimeError("settings service is not configured")
    return service


def _review_scheduler(request: Request) -> Any:
    legacy = _services(request)
    service = getattr(legacy, "review_scheduler", None)
    if service is None:
        service = getattr(legacy, "reviews", None)
    if service is None:
        raise RuntimeError("review scheduler is not configured")
    return service


def _artifact_store(request: Request) -> Any:
    legacy = _services(request)
    service = getattr(legacy, "artifact_store", None)
    if service is None:
        service = getattr(legacy, "artifacts", None)
    if service is None:
        raise RuntimeError("artifact store is not configured")
    return service


def _pdf_files(request: Request) -> Any:
    legacy = _services(request)
    service = getattr(legacy, "pdf_files", None)
    if service is None:
        service = getattr(request.app.state.container, "pdf_files", None)
    if service is None:
        raise RuntimeError("PDF file service is not configured")
    return service


def _legacy_ingest(request: Request) -> Any:
    legacy = _services(request)
    service = getattr(legacy, "legacy_ingest", None)
    if service is None:
        service = getattr(legacy, "ingest", None)
    if service is None:
        raise RuntimeError("legacy ingest service is not configured")
    return service


def _search_coordinator(request: Request) -> Any:
    service = getattr(_services(request), "search_coordinator", None)
    if service is None:
        raise RuntimeError("search coordinator is not configured")
    return service


def _optional_service(request: Request, name: str) -> Any | None:
    try:
        return getattr(_services(request), name, None)
    except Exception:
        return None


def _legacy_agent(request: Request) -> Any | None:
    legacy = _services(request)
    provider = getattr(legacy, "agent", None)
    if provider is not None:
        return provider
    ingest = getattr(legacy, "legacy_ingest", None)
    return getattr(ingest, "provider", None) if ingest is not None else None


def _processing_streams(request: Request) -> Any | None:
    service = _optional_service(request, "processing_streams")
    if service is not None:
        return service
    return getattr(request.app.state.container, "legacy_processing_streams", None)


async def _durable_artifact_events(
    request: Request,
    paper_id: str,
    kind: str,
    *,
    profile: str,
    agent_command: str | None = None,
    agent_args: list[str] | tuple[str, ...] = (),
):
    # 优先走 durable processing 管道；存量 legacy 论文没有 ready source
    # document（SOURCE_IDENTITY_MISSING）时，回退到旧 Node 同款行为：
    # spawn agent 子进程（explain/translate），stdout 纯文本包进 markdown 字段。
    service = _processing_streams(request)
    stream = getattr(service, "artifact_events", None)
    durable_error: str | None = None
    durable_terminal: dict[str, object] | None = None
    if callable(stream):
        try:
            async for event in stream(paper_id, kind, profile=profile):
                # 流内部会把异常转成失败终态事件：成功直接透传返回；
                # 失败（如 SOURCE_IDENTITY_MISSING）则不转发，转走 agent 回退。
                if isinstance(event, dict) and event.get("type") == "result":
                    durable_terminal = event
                    if event.get("ok"):
                        yield event
                        return
                    continue
                yield event
        except Exception as error:
            durable_terminal = {"ok": False, "error": _safe_error(error)}
        if durable_terminal is not None:
            durable_error = str(durable_terminal.get("error") or "") or "processing failed"
    else:
        durable_error = "processing service unavailable"
    if agent_command is None:
        yield {
            "type": "result",
            "ok": False,
            "markdown": "",
            "error": durable_error,
        }
        return
    async for event in _agent_events(
        request,
        agent_command,
        list(agent_args),
        terminal_fields={"markdown": ""},
        stdout_text_field="markdown",
    ):
        # agent 通道完全不可用时，返回 durable 管道的原始失败原因。
        if (
            isinstance(event, dict)
            and event.get("type") == "result"
            and not event.get("ok")
            and event.get("error") == "provider unavailable"
        ):
            yield {
                "type": "result",
                "ok": False,
                "markdown": "",
                "error": durable_error or "legacy agent failed",
            }
            return
        yield event


async def _durable_embedding_events(request: Request, scope: str):
    # 优先走 durable processing 管道；它对存量 legacy 论文常因缺少 ready
    # source document 而失败（如 SOURCE_IDENTITY_MISSING）——此时回退到旧 Node
    # 同款行为：spawn `python -m agent embed`，写入 paper_vectors，即
    # /api/semsearch 与 MCP semantic_search 实际读取的语义索引。
    service = _processing_streams(request)
    stream = getattr(service, "embedding_events", None)
    durable_terminal: dict[str, object] | None = None
    if callable(stream):
        try:
            async for event in stream(scope):
                if isinstance(event, dict) and event.get("type") == "result":
                    durable_terminal = event
                    if event.get("ok"):
                        yield event
                        return
                    continue
                yield event
        except Exception as error:
            durable_terminal = {"ok": False, "error": _safe_error(error)}
    else:
        durable_terminal = {"ok": False, "error": "processing service unavailable"}
    async for event in _agent_events(
        request,
        "embed",
        ["--scope", scope],
        terminal_fields={"indexed": 0, "total": 0},
    ):
        # agent 通道完全不可用时，返回 durable 管道的原始失败原因。
        if (
            isinstance(event, dict)
            and event.get("type") == "result"
            and not event.get("ok")
            and event.get("error") == "provider unavailable"
            and durable_terminal is not None
        ):
            yield {
                "type": "result",
                "ok": False,
                "indexed": 0,
                "total": 0,
                "error": str(durable_terminal.get("error") or "embedding failed"),
            }
            return
        yield event


async def _agent_events(
    request: Request,
    command: str,
    args: list[str] | tuple[str, ...],
    *,
    terminal_type: str = "result",
    terminal_fields: dict[str, object] | None = None,
    stdin: str | bytes | None = None,
    stdout_array_field: str | None = None,
    stdout_text_field: str | None = None,
    stdout_object_field: str | None = None,
):
    provider = _legacy_agent(request)
    fields = dict(terminal_fields or {})
    if provider is None:
        yield {"type": terminal_type, "ok": False, **fields, "error": "provider unavailable"}
        return
    stream = getattr(provider, "stream_events", None)
    if callable(stream):
        extra: dict[str, object] = {}
        if stdout_array_field is not None:
            extra["stdout_array_field"] = stdout_array_field
        if stdout_text_field is not None:
            extra["stdout_text_field"] = stdout_text_field
        if stdout_object_field is not None:
            extra["stdout_object_field"] = stdout_object_field
        try:
            result = stream(
                command,
                args,
                terminal_type=terminal_type,
                terminal_fields=fields,
                stdin=stdin,
                **extra,
            )
        except TypeError:
            result = stream(
                command,
                args,
                terminal_type=terminal_type,
                terminal_fields=fields,
                stdin=stdin,
            )
        async for event in result:
            yield event
        return
    runner = getattr(provider, "run", None)
    if not callable(runner):
        yield {"type": terminal_type, "ok": False, **fields, "error": "provider unavailable"}
        return
    try:
        result = runner(command, args, stdin=stdin)
        if hasattr(result, "__await__"):
            result = await result
        for line in str(getattr(result, "stderr", "")).splitlines():
            if line.strip():
                yield {"type": "progress", "line": line}
        decoded: dict[str, object] = {}
        raw_stdout = str(getattr(result, "stdout", "") or "")
        try:
            parsed = json.loads(raw_stdout) if raw_stdout.strip() else {}
            if isinstance(parsed, dict):
                if stdout_object_field is not None:
                    decoded[stdout_object_field] = parsed
                else:
                    decoded.update(parsed)
            elif isinstance(parsed, list) and stdout_array_field is not None:
                decoded[stdout_array_field] = parsed
            elif stdout_text_field is not None and raw_stdout.strip():
                decoded[stdout_text_field] = raw_stdout
        except (TypeError, ValueError):
            if stdout_text_field is not None and raw_stdout.strip():
                decoded[stdout_text_field] = raw_stdout
        for key, value in fields.items():
            decoded.setdefault(key, value)
        decoded.setdefault("ok", int(getattr(result, "returncode", 1)) == 0)
        decoded.setdefault("error", "" if decoded["ok"] else "legacy agent failed")
        yield {"type": terminal_type, **decoded}
    except Exception:
        yield {"type": terminal_type, "ok": False, **fields, "error": "legacy agent failed"}


def _optional_args(body: dict[str, object], field: str) -> list[str]:
    value = body.get(field)
    if value is None or value == "":
        return []
    return [f"--{field.replace('_', '-')}", str(value)]


def _valid_sources(value: object, *, allowed: set[str] | None = None) -> tuple[str, ...]:
    accepted = allowed or {"semanticscholar", "arxiv", "openalex", "dblp"}
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if str(item) in accepted))


async def _body(request: Request) -> dict[str, object]:
    value = await request.json()
    return value if isinstance(value, dict) else {}


def _safe_base(value: object) -> str:
    return ntpath.basename(str(value or ""))


def _parse_int(value: object) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(str(value), 10)
    except (TypeError, ValueError):
        return None


def _json_response(value: object, status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        status_code=status_code,
        headers={"Content-Type": _JSON_CONTENT_TYPE},
    )


def _opened_pdf_response(
    request: Request,
    opened: object,
    *,
    media_type: str,
    headers: dict[str, str] | None = None,
) -> Response:
    stream = opened.stream
    response_headers = {**(headers or {}), "Content-Length": str(int(opened.size))}
    if request.method == "HEAD":
        stream.close()
        return Response(b"", media_type=media_type, headers=response_headers)

    async def body():
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(stream.read, 64 * 1024)
                if not chunk:
                    return
                yield chunk
        finally:
            await anyio.to_thread.run_sync(stream.close)

    return StreamingResponse(body(), media_type=media_type, headers=response_headers)


def _markdown_response(value: object, status_code: int = 200) -> Response:
    return Response(
        content="" if value is None else str(value),
        status_code=status_code,
        headers={"Content-Type": "text/markdown; charset=utf-8"},
    )


def _foreign_key_error() -> Response:
    return Response(
        content="FOREIGN KEY constraint failed",
        status_code=500,
        media_type="text/plain",
    )


def _safe_error(error: Exception) -> str:
    if isinstance(error, ValueError) and str(error) == "标题不能为空":
        return "标题不能为空"
    return getattr(error, "public_message", None) or "请求处理失败"


def _settings_error(error: Exception) -> Response:
    status = getattr(error, "http_status", 500)
    if not isinstance(status, int) or status < 400 or status > 599:
        status = 500
    code = getattr(error, "code", "SETTINGS_ERROR")
    message = getattr(error, "public_message", None) or "Settings could not be processed."
    return _json_response(
        {"ok": False, "error": {"code": str(code), "message": str(message)}},
        status,
    )


def _safe_json_error(error: Exception) -> Response:
    status = getattr(error, "http_status", 500)
    if not isinstance(status, int) or status < 400 or status > 599:
        status = 500
    return _json_response(
        {"ok": False, "error": _safe_error(error)},
        status,
    )


def _safe_text_error(error: Exception) -> Response:
    status = getattr(error, "http_status", 500)
    if not isinstance(status, int) or status < 400 or status > 599:
        status = 500
    return Response(
        content=_safe_error(error),
        status_code=status,
        headers={"Content-Type": "text/markdown; charset=utf-8"},
    )


def _legacy_job_error(error: Exception) -> Response:
    status = getattr(error, "http_status", 500)
    if not isinstance(status, int) or status < 400 or status > 599:
        status = 500
    if status == 400:
        message = getattr(error, "public_message", None) or "缺少搜索方向或数据源"
    elif status == 404:
        message = getattr(error, "public_message", None) or "任务不存在"
    else:
        message = "请求处理失败"
    return _json_response({"ok": False, "error": message}, status)


__all__ = ["create_legacy_router"]
