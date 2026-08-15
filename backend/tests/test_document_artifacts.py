from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import unittest

if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and os.environ.get("P3_SQLITE_DLL_DIR")
):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])

import sqlite3


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


async def _seed_claimed_artifact(
    fixture,
    *,
    kind: str,
    artifact_id: str,
    job_id: str,
    provider: str,
    model: str,
    prompt_version: str,
    profile: str = "standard",
):
    from backend.app.domain import GeneratedArtifact
    from backend.app.domain.processing import (
        ExplainJobSpecV1,
        NewProcessingJob,
        build_artifact_job_key,
        build_artifact_key,
        encode_job_spec_v1,
        hash_job_spec,
    )

    artifact = GeneratedArtifact(
        id=artifact_id,
        paper_id="paper-1",
        kind=kind,
        source_document_id=fixture.chunk_set.source_document_id,
        status="queued",
        generator_provider=provider,
        generator_model=model,
        prompt_version=prompt_version,
        created_at=NOW,
        updated_at=NOW,
    )
    spec = ExplainJobSpecV1(
        paper_id="paper-1",
        source_document_id=fixture.chunk_set.source_document_id,
        artifact_id=artifact_id,
        profile=profile,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )
    raw_spec = encode_job_spec_v1(spec)
    artifact_key = build_artifact_key(
        kind=kind,
        source_document_id=fixture.chunk_set.source_document_id,
        source_content_sha256=fixture.chunk_set.source_content_sha256,
        generator_provider=provider,
        generator_model=model,
        prompt_version=prompt_version,
        kind_specific_options={"profile": profile},
    )
    job = NewProcessingJob(
        id=job_id,
        spec=spec,
        idempotency_key=build_artifact_job_key(
            artifact_key,
            hash_job_spec(raw_spec),
        ),
        created_at=NOW,
        max_attempts=3,
    )
    async with fixture.unit_of_work_factory() as work:
        await work.artifacts.enqueue_with_job(
            artifact,
            job,
            spec_json=raw_spec,
            spec_sha256=hash_job_spec(raw_spec),
            kind_specific_options={"profile": profile},
        )
        await work.commit()
    async with fixture.unit_of_work_factory() as work:
        lease = await work.jobs.claim_next(
            worker_id=f"{kind}-worker",
            now=NOW,
            lease_seconds=3600,
        )
        await work.commit()
    if lease is None:
        raise AssertionError("fixture failed to claim its artifact job")
    return lease


class DocumentArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_artifact_enqueue_rejects_mismatched_native_provider_identity(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import StaleSourceError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"%PDF-1.7\nartifact native identity fixture\n"
        source_id = "src_artifact_native_identity"
        async with p3_context_fixture(
            prefix="study-app-p3-artifact-native-identity-",
            source_id=source_id,
            markdown="# Abstract\n\nnative identity sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="b" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "native-identity.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.execute(
                    "UPDATE document_sources SET provider='wrong-provider',"
                    "model='wrong-model' WHERE id=?",
                    (source_id,),
                )
                connection.commit()

            class StructuredProvider:
                provider_id = "native-identity-structured"
                model_id = "native-identity-model"

                async def generate(self, _request):
                    raise AssertionError("enqueue must not call the provider")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=StructuredProvider(),
                clock=lambda: NOW,
            )

            with self.assertRaises(StaleSourceError) as raised:
                await service.enqueue(
                    "paper-1", source_id, "native", "classification", now=NOW
                )
            self.assertEqual("SOURCE_STALE", raised.exception.code)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                self.assertEqual(
                    (0, 0),
                    (
                        connection.execute(
                            "SELECT count(*) FROM generated_artifacts"
                        ).fetchone()[0],
                        connection.execute(
                            "SELECT count(*) FROM processing_jobs"
                        ).fetchone()[0],
                    ),
                )

    async def test_worker_structured_transport_timeout_uses_retry_state_machine(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        async with p3_context_fixture(
            prefix="study-app-p3-structured-timeout-",
            source_id="src_structured_timeout",
            markdown="# Abstract\n\nprovider timeout sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact_id = "artifact_structured_timeout"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="classification",
                artifact_id=artifact_id,
                job_id="job_structured_timeout",
                provider="structured-timeout",
                model="structured-timeout-model",
                prompt_version="classification-v1",
            )

            class TimeoutProvider:
                provider_id = "structured-timeout"
                model_id = "structured-timeout-model"

                def __init__(self) -> None:
                    self.calls = 0

                async def generate(self, _request):
                    self.calls += 1
                    raise TimeoutError("raw provider payload must not escape")

            provider = TimeoutProvider()
            worker_now = NOW + timedelta(hours=1, seconds=1)
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: worker_now,
            )

            async def handle_structured(current_lease):
                await service.run(current_lease, artifact_id)
                return ProcessingHandlerOutcome.settled()

            worker = ProcessingWorker(
                fixture.unit_of_work_factory,
                handlers={"explain": handle_structured},
                worker_id="structured-timeout-worker",
                clock=lambda: worker_now,
                lease_seconds=3600,
            )

            self.assertTrue(await worker.run_once())

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                job = connection.execute(
                    "SELECT status,attempt,error_code,available_at,finished_at "
                    "FROM processing_jobs WHERE id=?",
                    (lease.job.id,),
                ).fetchone()
            self.assertEqual(1, provider.calls)
            self.assertEqual("queued", job[0])
            self.assertEqual(2, job[1])
            self.assertEqual("GENERATION_FAILED", job[2])
            self.assertIsNone(job[4])

    async def test_structured_publication_rejects_chunk_identity_mutation_after_provider_call(
        self,
    ) -> None:
        """Publication must fence the full persisted chunk identity, not just IDs."""

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import PersistenceConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = "# Abstract\n\npublication identity sentinel.\n"
        async with p3_context_fixture(
            prefix="study-app-p3-structured-publication-identity-",
            source_id="src_structured_publication_identity",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact_id = "artifact_structured_publication_identity"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="classification",
                artifact_id=artifact_id,
                job_id="job_structured_publication_identity",
                provider="publication-structured",
                model="publication-structured-model",
                prompt_version="classification-v1",
            )

            class MutatingProvider:
                provider_id = "publication-structured"
                model_id = "publication-structured-model"

                async def generate(self, _request):
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        connection.execute(
                            "UPDATE document_chunks SET chunk_key='tampered-chunk-key' "
                            "WHERE id=?",
                            (fixture.chunk_set.chunks[0].id,),
                        )
                        connection.commit()
                    return json.dumps(
                        {
                            "type": "evaluation",
                            "topic": "publication",
                            "task": "identity",
                            "models": ["Model"],
                            "datasets": ["Dataset"],
                            "tags": ["identity"],
                            "relevance": 0.9,
                        },
                        ensure_ascii=False,
                    )

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=MutatingProvider(),
                clock=lambda: NOW,
            )

            with self.assertRaises(PersistenceConflictError) as caught:
                await service.run(lease, artifact_id)
            self.assertEqual("PERSISTENCE_CONFLICT", caught.exception.code)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                artifact_status = connection.execute(
                    "SELECT status FROM generated_artifacts WHERE id=?", (artifact_id,)
                ).fetchone()
                job_status = connection.execute(
                    "SELECT status FROM processing_jobs WHERE id=?", (lease.job.id,)
                ).fetchone()
            self.assertEqual(("running",), artifact_status)
            self.assertEqual(("running",), job_status)

    async def test_explainer_summarizes_every_priority_section_and_publishes_tail_markdown(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import GeneratedArtifact
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfront explainer sentinel.\n\n"
            "# Related Work\n\nrelated poison sentinel.\n\n"
            "# Methods\n\n"
            + "".join(f"method child sentinel {index}.\n\n" for index in range(10))
            + "# References\n\nreference poison sentinel.\n\n"
            "# Discussion\n\ntail discussion sentinel.\n\n"
            "# Conclusion\n\ntail conclusion sentinel.\n"
        )
        final_markdown = (
            "# Complete Explainer\n\n"
            "front explainer sentinel; all method children; "
            "tail discussion sentinel; tail conclusion sentinel.\n"
        )
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-explainer-",
            source_id="src_explainer_artifact",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=6, hard_cap_tokens=6),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            old_content = "# Immutable old explainer\n"
            old_artifact = GeneratedArtifact(
                id="artifact_explainer_old",
                paper_id="paper-1",
                kind="explainer",
                source_document_id=fixture.chunk_set.source_document_id,
                status="ready",
                generator_provider="old-provider",
                generator_model="old-model",
                prompt_version="old-prompt-v1",
                created_at=NOW,
                updated_at=NOW,
                content=old_content,
                content_sha256=hashlib.sha256(old_content.encode("utf-8")).hexdigest(),
            )
            async with fixture.unit_of_work_factory() as work:
                await work.artifacts.add(old_artifact)
                self.assertTrue(
                    await work.artifacts.publish_head(
                        paper_id="paper-1",
                        kind="explainer",
                        artifact_id=old_artifact.id,
                        expected_artifact_id=None,
                        updated_at=NOW,
                    )
                )
                await work.artifacts.write_legacy_explainer(
                    "paper-1", old_content, NOW
                )
                await work.commit()

            artifact_id = "artifact_explainer"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="explainer",
                artifact_id=artifact_id,
                job_id="job_explainer_p3",
                provider="fake-structured",
                model="fake-structured-model",
                prompt_version="explainer-context-v1",
            )

            class ExplainerProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    if request.stage == "map":
                        content = "".join(chunk.content for chunk in request.batch.chunks)
                        if "poison" in content:
                            raise AssertionError("excluded explainer poison crossed provider boundary")
                        return json.dumps(
                            {
                                "coveredRanges": [list(item) for item in request.batch.covered_ranges],
                                "markdown": f"## Section {request.batch.sequence}\n\n{content.strip()}\n",
                            },
                            ensure_ascii=False,
                        )
                    if request.stage == "reduce":
                        combined = "\n".join(child.content for child in request.inputs)
                        for sentinel in (
                            "front explainer sentinel",
                            "method child sentinel 9",
                            "tail discussion sentinel",
                            "tail conclusion sentinel",
                        ):
                            if sentinel not in combined:
                                raise AssertionError(f"missing eligible sentinel: {sentinel}")
                        if "poison" in combined:
                            raise AssertionError("excluded explainer poison reached reduce")
                        return json.dumps(
                            {
                                "coveredRanges": [
                                    list(item)
                                    for child in request.inputs
                                    for item in child.covered_ranges
                                ],
                                "markdown": final_markdown,
                            },
                            ensure_ascii=False,
                        )
                    raise AssertionError(f"unexpected explainer stage {request.stage}")

            provider = ExplainerProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            artifact = await service.run(lease, artifact_id)

            map_calls = [call for call in provider.calls if call.stage == "map"]
            self.assertGreaterEqual(len(map_calls), 4)
            method_call = next(
                call
                for call in map_calls
                if any("method child sentinel" in chunk.content for chunk in call.batch.chunks)
            )
            self.assertGreater(len(method_call.batch.chunks), 1)
            self.assertEqual(1, len([call for call in provider.calls if call.stage == "reduce"]))
            self.assertEqual(final_markdown, artifact.content)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                old_row = connection.execute(
                    "SELECT status,content,content_sha256 FROM generated_artifacts WHERE id=?",
                    (old_artifact.id,),
                ).fetchone()
                new_row = connection.execute(
                    "SELECT status,content FROM generated_artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                head = connection.execute(
                    "SELECT artifact_id FROM paper_artifact_heads "
                    "WHERE paper_id='paper-1' AND kind='explainer'"
                ).fetchone()
                legacy = connection.execute(
                    "SELECT explainer FROM papers WHERE id='paper-1'"
                ).fetchone()
                job = connection.execute(
                    "SELECT status FROM processing_jobs WHERE id='job_explainer_p3'"
                ).fetchone()
            self.assertEqual(
                (
                    "stale",
                    old_content,
                    hashlib.sha256(old_content.encode("utf-8")).hexdigest(),
                ),
                old_row,
            )
            self.assertEqual(("ready", final_markdown), new_row)
            self.assertEqual((artifact_id,), head)
            self.assertEqual((final_markdown,), legacy)
            self.assertEqual(("succeeded",), job)

    async def test_explainer_recursively_reduces_with_bounded_fan_in_and_full_ranges(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        headings = ("Abstract", "Methods", "Experiments", "Discussion", "Conclusion")
        markdown = "".join(
            f"# {headings[index % len(headings)]}\n\n"
            f"recursive explainer sentinel {index}.\n\n"
            for index in range(24)
        )
        final_markdown = "# Recursive Explainer\n\nrecursive tail sentinel 23.\n"
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-recursive-explainer-",
            source_id="src_recursive_explainer",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=12, hard_cap_tokens=12),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact_id = "artifact_recursive_explainer"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="explainer",
                artifact_id=artifact_id,
                job_id="job_recursive_explainer",
                provider="recursive-structured",
                model="recursive-structured-model",
                prompt_version="explainer-context-v1",
            )
            expected_ranges = tuple(
                (chunk.char_start, chunk.char_end) for chunk in fixture.chunk_set.chunks
            )

            class RecursiveProvider:
                provider_id = "recursive-structured"
                model_id = "recursive-structured-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    if request.stage == "map":
                        return json.dumps(
                            {
                                "coveredRanges": [
                                    list(item) for item in request.batch.covered_ranges
                                ],
                                "markdown": "".join(
                                    chunk.content for chunk in request.batch.chunks
                                ),
                            },
                            ensure_ascii=False,
                        )
                    if request.stage == "reduce":
                        if len(request.inputs) > 8:
                            raise AssertionError("reduce exceeded the frozen child fan-in")
                        ranges = tuple(
                            item
                            for child in request.inputs
                            for item in child.covered_ranges
                        )
                        combined = "\n".join(child.content for child in request.inputs)
                        if ranges == expected_ranges:
                            if "recursive explainer sentinel 23" not in combined:
                                raise AssertionError("tail child was lost before final reduce")
                            output = final_markdown
                        else:
                            output = "## Intermediate\n\n" + combined
                        return json.dumps(
                            {
                                "coveredRanges": [list(item) for item in ranges],
                                "markdown": output,
                            },
                            ensure_ascii=False,
                        )
                    raise AssertionError(f"unexpected stage {request.stage}")

            provider = RecursiveProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            artifact = await service.run(lease, artifact_id)

        reduce_calls = [call for call in provider.calls if call.stage == "reduce"]
        self.assertGreater(len(reduce_calls), 1)
        self.assertTrue(all(len(call.inputs) <= 8 for call in reduce_calls))
        self.assertEqual(expected_ranges, tuple(
            item for child in reduce_calls[-1].inputs for item in child.covered_ranges
        ))
        self.assertEqual(final_markdown, artifact.content)

    async def test_structured_provider_fences_every_call_after_cancel_or_lease_loss(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import JobLeaseLostError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfirst structured fence sentinel.\n\n"
            "# Methods\n\nsecond structured fence sentinel.\n\n"
            "# Conclusion\n\ntail structured fence sentinel.\n"
        )
        for invalidation in ("cancel", "lease_loss"):
            with self.subTest(invalidation=invalidation):
                async with p3_context_fixture(
                    prefix=f"study-app-p3-structured-{invalidation}-",
                    source_id=f"src_structured_{invalidation}",
                    markdown=markdown,
                    spec=ChunkingSpec(target_tokens=6, hard_cap_tokens=6),
                    now=NOW,
                    pdf_sha256="a" * 64,
                    options_hash="b" * 64,
                ) as fixture:
                    artifact_id = f"artifact_structured_{invalidation}"
                    job_id = f"job_structured_{invalidation}"
                    lease = await _seed_claimed_artifact(
                        fixture,
                        kind="summary",
                        artifact_id=artifact_id,
                        job_id=job_id,
                        provider="structured-fence",
                        model="structured-fence-model",
                        prompt_version="summary-v1",
                    )

                    class InvalidatingProvider:
                        provider_id = "structured-fence"
                        model_id = "structured-fence-model"

                        def __init__(self) -> None:
                            self.calls = 0

                        async def generate(self, request):
                            self.calls += 1
                            if self.calls == 1:
                                if invalidation == "cancel":
                                    async with fixture.unit_of_work_factory() as work:
                                        await work.jobs.cancel(job_id, now=NOW)
                                        await work.commit()
                                else:
                                    async with fixture.session_factory() as session:
                                        await session.execute(
                                            text(
                                                "UPDATE processing_jobs "
                                                "SET lease_token='stolen-token' WHERE id=:job_id"
                                            ),
                                            {"job_id": job_id},
                                        )
                                        await session.commit()
                            if request.stage == "map":
                                return json.dumps(
                                    {
                                        "coveredRanges": [
                                            list(item) for item in request.batch.covered_ranges
                                        ],
                                        "summary": "map result",
                                    }
                                )
                            return json.dumps(
                                {
                                    "coveredRanges": [
                                        list(item)
                                        for child in request.inputs
                                        for item in child.covered_ranges
                                    ],
                                    "tldr": "bounded tldr",
                                    "contribution": "bounded contribution",
                                }
                            )

                    provider = InvalidatingProvider()
                    service = DocumentArtifactService(
                        fixture.unit_of_work_factory,
                        context_builder=fixture.builder,
                        structured_provider=provider,
                        clock=lambda: NOW,
                    )

                    with self.assertRaises(JobLeaseLostError):
                        await service.run(lease, artifact_id)
                    self.assertEqual(1, provider.calls)

    async def test_summary_maps_all_eligible_batches_then_reduces_with_covered_ranges(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# Abstract\n\nfront summary sentinel.\n\n"
            "# Methods\n\nmethod summary sentinel.\n\n"
            "# References\n\nreference poison sentinel.\n\n"
            "# Conclusion\n\ntail summary sentinel.\n\n"
            "# Acknowledgements\n\nack poison sentinel.\n"
        )
        final_output = {
            "coveredRanges": [],
            "tldr": "Front, method, and tail summary sentinels are covered.",
            "contribution": "Complete eligible-body map/reduce coverage.",
        }
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-summary-",
            source_id="src_summary_artifact",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=6, hard_cap_tokens=6),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact_id = "artifact_summary"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="summary",
                artifact_id=artifact_id,
                job_id="job_summary",
                provider="fake-structured",
                model="fake-structured-model",
                prompt_version="summary-v1",
            )

            class SummaryProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    if request.stage == "map":
                        self.assert_is_batch_only(request)
                        content = "".join(chunk.content for chunk in request.batch.chunks)
                        if "poison" in content:
                            raise AssertionError("excluded summary poison crossed provider boundary")
                        return json.dumps(
                            {
                                "coveredRanges": [list(item) for item in request.batch.covered_ranges],
                                "summary": content.strip(),
                            },
                            ensure_ascii=False,
                        )
                    if request.stage == "reduce":
                        if request.plan is not None or request.batch is not None:
                            raise AssertionError("reduce must receive only typed child results")
                        ranges = [
                            list(item)
                            for child in request.inputs
                            for item in child.covered_ranges
                        ]
                        combined = "\n".join(child.content for child in request.inputs)
                        if "tail summary sentinel" not in combined or "poison" in combined:
                            raise AssertionError("reduce coverage is incomplete or poisoned")
                        final_output["coveredRanges"] = ranges
                        return json.dumps(final_output, ensure_ascii=False)
                    raise AssertionError(f"unexpected summary stage {request.stage}")

                @staticmethod
                def assert_is_batch_only(request):
                    if request.plan is not None or request.batch is None or request.inputs:
                        raise AssertionError("map must receive exactly one ContextBatch")

            provider = SummaryProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            artifact = await service.run(lease, artifact_id)

            map_calls = [call for call in provider.calls if call.stage == "map"]
            reduce_calls = [call for call in provider.calls if call.stage == "reduce"]
            self.assertGreaterEqual(len(map_calls), 3)
            self.assertEqual(1, len(reduce_calls))
            selected = "".join(
                chunk.content for call in map_calls for chunk in call.batch.chunks
            )
            self.assertIn("front summary sentinel", selected)
            self.assertIn("method summary sentinel", selected)
            self.assertIn("tail summary sentinel", selected)
            self.assertNotIn("poison", selected)
            expected_content = json.dumps(
                {
                    "contribution": final_output["contribution"],
                    "tldr": final_output["tldr"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.assertEqual(expected_content, artifact.content)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                paper = connection.execute(
                    "SELECT tldr,contribution FROM papers WHERE id='paper-1'"
                ).fetchone()
                head = connection.execute(
                    "SELECT artifact_id FROM paper_artifact_heads "
                    "WHERE paper_id='paper-1' AND kind='summary'"
                ).fetchone()
            self.assertEqual(
                (final_output["tldr"], final_output["contribution"]),
                paper,
            )
            self.assertEqual((artifact_id,), head)

    async def test_summary_recursively_reduces_with_bounded_fan_in_and_full_ranges(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = "".join(
            f"# Section {index}\n\nrecursive summary sentinel {index}.\n\n"
            for index in range(20)
        )
        final_tldr = "All recursive summary sentinels are covered."
        final_contribution = "Bounded recursive reduction preserves ordered ranges."
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-recursive-summary-",
            source_id="src_recursive_summary",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact_id = "artifact_recursive_summary"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="summary",
                artifact_id=artifact_id,
                job_id="job_recursive_summary",
                provider="recursive-summary",
                model="recursive-summary-model",
                prompt_version="summary-v1",
            )
            expected_ranges = tuple(
                (chunk.char_start, chunk.char_end) for chunk in fixture.chunk_set.chunks
            )

            class RecursiveSummaryProvider:
                provider_id = "recursive-summary"
                model_id = "recursive-summary-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    if request.stage == "map":
                        return json.dumps(
                            {
                                "coveredRanges": [
                                    list(item) for item in request.batch.covered_ranges
                                ],
                                "summary": "".join(
                                    chunk.content for chunk in request.batch.chunks
                                ),
                            },
                            ensure_ascii=False,
                        )
                    if request.stage == "reduce":
                        if len(request.inputs) > 8:
                            raise AssertionError("summary reduce exceeded child fan-in")
                        ranges = tuple(
                            item
                            for child in request.inputs
                            for item in child.covered_ranges
                        )
                        combined = "\n".join(child.content for child in request.inputs)
                        if ranges == expected_ranges and "recursive summary sentinel 19" not in combined:
                            raise AssertionError("summary tail child was lost")
                        tldr = final_tldr if ranges == expected_ranges else combined
                        contribution = (
                            final_contribution
                            if ranges == expected_ranges
                            else "Intermediate summary node."
                        )
                        return json.dumps(
                            {
                                "coveredRanges": [list(item) for item in ranges],
                                "tldr": tldr,
                                "contribution": contribution,
                            },
                            ensure_ascii=False,
                        )
                    raise AssertionError(f"unexpected stage {request.stage}")

            provider = RecursiveSummaryProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            artifact = await service.run(lease, artifact_id)

        reduce_calls = [call for call in provider.calls if call.stage == "reduce"]
        self.assertGreater(len(reduce_calls), 1)
        self.assertTrue(all(len(call.inputs) <= 8 for call in reduce_calls))
        self.assertEqual(expected_ranges, tuple(
            item for child in reduce_calls[-1].inputs for item in child.covered_ranges
        ))
        self.assertEqual(
            json.dumps(
                {"contribution": final_contribution, "tldr": final_tldr},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            artifact.content,
        )

    async def test_metadata_uses_first_page_and_preserves_trusted_identifiers(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "[page 1]\n# Extracted Paper Title\n\n"
            "Ada Lovelace · Verified Venue · 2026.\n\n"
            "[page 2]\n# Methods\n\nmetadata body poison.\n"
        )
        output = {
            "title": "Extracted Paper Title",
            "titleZh": "抽取论文标题",
            "authors": ["Ada Lovelace"],
            "venue": "Verified Venue",
            "year": "2026",
            "abstract": "A source-owned abstract.",
            "arxivId": None,
            "doi": None,
        }
        expected_content = json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-metadata-",
            source_id="src_metadata_artifact",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET arxiv_id='2401.00001',doi='10.1000/trusted',"
                    "title='Trusted Existing Title',authors='[\"Existing Author\"]' "
                    "WHERE id='paper-1'"
                )
                connection.commit()
            artifact_id = "artifact_metadata"
            lease = await _seed_claimed_artifact(
                fixture,
                kind="metadata",
                artifact_id=artifact_id,
                job_id="job_metadata",
                provider="fake-structured",
                model="fake-structured-model",
                prompt_version="metadata-v1",
            )

            class MetadataProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    return expected_content

            provider = MetadataProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            artifact = await service.run(lease, artifact_id)

            self.assertEqual(expected_content, artifact.content)
            self.assertEqual(1, len(provider.calls))
            request = provider.calls[0]
            self.assertEqual("metadata", request.kind)
            selected = "".join(
                chunk.content
                for batch in request.plan.batches
                for chunk in batch.chunks
            )
            self.assertIn("Extracted Paper Title", selected)
            self.assertNotIn("poison", selected)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                paper = connection.execute(
                    "SELECT title,title_zh,authors,venue,year,abstract,arxiv_id,doi "
                    "FROM papers WHERE id='paper-1'"
                ).fetchone()
                head = connection.execute(
                    "SELECT artifact_id FROM paper_artifact_heads "
                    "WHERE paper_id='paper-1' AND kind='metadata'"
                ).fetchone()
                job = connection.execute(
                    "SELECT status FROM processing_jobs WHERE id='job_metadata'"
                ).fetchone()
            self.assertEqual(
                (
                    output["title"],
                    output["titleZh"],
                    json.dumps(output["authors"], ensure_ascii=False, separators=(",", ":")),
                    output["venue"],
                    output["year"],
                    output["abstract"],
                    "2401.00001",
                    "10.1000/trusted",
                ),
                paper,
            )
            self.assertEqual((artifact_id,), head)
            self.assertEqual(("succeeded",), job)

    async def test_classification_schema_fails_closed_without_partial_publication(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import ArtifactOutputInvalidError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        valid = {
            "type": "评测",
            "topic": "鲁棒性",
            "task": "classification",
            "models": ["M"],
            "datasets": ["D"],
            "tags": ["T"],
            "relevance": 0.8,
        }
        invalid_outputs = {
            "unknown": json.dumps({**valid, "rawResponse": "secret-provider-payload"}),
            "missing": json.dumps({key: value for key, value in valid.items() if key != "topic"}),
            "wrong_type": json.dumps({**valid, "models": "M"}),
            "empty": "   ",
        }
        for case, raw_output in invalid_outputs.items():
            with self.subTest(case=case):
                async with p3_context_fixture(
                    prefix=f"study-app-p3-classification-schema-{case}-",
                    source_id=f"src_classification_schema_{case}",
                    markdown="# Abstract\n\nstrict schema sentinel.\n",
                    spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
                    now=NOW,
                    pdf_sha256="a" * 64,
                    options_hash="b" * 64,
                ) as fixture:
                    artifact_id = f"artifact_classification_schema_{case}"
                    lease = await _seed_claimed_artifact(
                        fixture,
                        kind="classification",
                        artifact_id=artifact_id,
                        job_id=f"job_classification_schema_{case}",
                        provider="fake-structured",
                        model="fake-structured-model",
                        prompt_version="classification-v1",
                    )

                    class InvalidProvider:
                        provider_id = "fake-structured"
                        model_id = "fake-structured-model"

                        async def generate(self, request):
                            return raw_output

                    service = DocumentArtifactService(
                        fixture.unit_of_work_factory,
                        context_builder=fixture.builder,
                        structured_provider=InvalidProvider(),
                        clock=lambda: NOW,
                    )

                    with self.assertRaises(ArtifactOutputInvalidError) as raised:
                        await service.run(lease, artifact_id)

                    self.assertEqual("ARTIFACT_OUTPUT_INVALID", raised.exception.code)
                    self.assertFalse(raised.exception.retryable)
                    self.assertNotIn("secret-provider-payload", str(raised.exception))
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        artifact_row = connection.execute(
                            "SELECT status,content,content_sha256 FROM generated_artifacts WHERE id=?",
                            (artifact_id,),
                        ).fetchone()
                        head_count = connection.execute(
                            "SELECT count(*) FROM paper_artifact_heads"
                        ).fetchone()[0]
                        projection = connection.execute(
                            "SELECT type,topic,task,models,datasets,tags,relevance "
                            "FROM papers WHERE id='paper-1'"
                        ).fetchone()
                        job_row = connection.execute(
                            "SELECT status,result_json,error_code,error_message "
                            "FROM processing_jobs WHERE id=?",
                            (lease.job.id,),
                        ).fetchone()
                    self.assertEqual(("running", None, None), artifact_row)
                    self.assertEqual(0, head_count)
                    self.assertEqual((None, None, None, None, None, None, None), projection)
                    self.assertEqual(("running", None, None, None), job_row)

    async def test_enqueue_uses_formal_queue_for_classification_and_translation_identity(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"%PDF-1.7\np3 artifact enqueue fixture\n"
        source_id = "src_artifact_enqueue"
        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-enqueue-",
            source_id=source_id,
            markdown="# Abstract\n\nenqueue context sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="b" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "paper-1.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            class StructuredProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                async def generate(self, request):
                    raise AssertionError("enqueue must not call the provider")

            class TranslationProvider:
                provider_id = "fake-translation"
                model_id = "fake-translation-model"
                prompt_version = "translation-chunk-v1"

                async def translate(self, request):
                    raise AssertionError("enqueue must not call the provider")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=StructuredProvider(),
                translation_provider=TranslationProvider(),
                clock=lambda: NOW,
            )

            classification = await service.enqueue(
                "paper-1",
                source_id,
                "native",
                "classification",
                now=NOW,
            )
            translation = await service.enqueue(
                "paper-1",
                source_id,
                "native",
                "translation",
                now=NOW,
            )
            classification_again = await service.enqueue(
                "paper-1",
                source_id,
                "native",
                "classification",
                now=NOW,
            )

            self.assertEqual("classification", classification.artifact.kind.value)
            self.assertEqual("explain", classification.job.spec.job_type)
            self.assertEqual("translation", translation.artifact.kind.value)
            self.assertEqual("translate", translation.job.spec.job_type)
            self.assertEqual(source_id, classification.job.spec.source_document_id)
            self.assertEqual(source_id, translation.job.spec.source_document_id)
            self.assertNotEqual(classification.artifact.id, translation.artifact.id)
            self.assertNotEqual(
                classification.job.idempotency_key,
                translation.job.idempotency_key,
            )
            self.assertTrue(classification_again.deduplicated)
            self.assertEqual(
                classification.artifact.id,
                classification_again.artifact.id,
            )
            self.assertEqual(classification.job.id, classification_again.job.id)

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                artifacts = connection.execute(
                    "SELECT kind,status,artifact_key FROM generated_artifacts ORDER BY kind"
                ).fetchall()
                jobs = connection.execute(
                    "SELECT job_type,status,spec_json FROM processing_jobs ORDER BY job_type"
                ).fetchall()
            self.assertEqual(2, len(artifacts))
            self.assertEqual(2, len({row[2] for row in artifacts}))
            self.assertTrue(all(row[1] == "queued" for row in artifacts))
            self.assertEqual(["explain", "translate"], [row[0] for row in jobs])
            self.assertTrue(all(row[1] == "queued" for row in jobs))
            self.assertTrue(
                all(json.loads(row[2])["target"]["sourceDocumentId"] == source_id for row in jobs)
            )

    async def test_enqueue_rechecks_current_paper_pdf_path_inside_atomic_write(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import PersistenceConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        original_pdf = b"%PDF-1.7\noriginal enqueue identity\n"
        replacement_pdf = b"%PDF-1.7\nreplacement path bytes are distinct\n"
        source_id = "src_enqueue_path_fence"
        async with p3_context_fixture(
            prefix="study-app-p3-artifact-enqueue-path-fence-",
            source_id=source_id,
            markdown="# Abstract\n\npath identity fence sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(original_pdf).hexdigest(),
            options_hash="b" * 64,
        ) as fixture:
            original_path = fixture.database_path.parent / "original.pdf"
            replacement_path = fixture.database_path.parent / "replacement.pdf"
            original_path.write_bytes(original_pdf)
            replacement_path.write_bytes(replacement_pdf)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(original_path),),
                )
                connection.commit()

            swapped = False

            class SwappingArtifacts:
                def __init__(self, delegate) -> None:
                    self._delegate = delegate

                async def find_by_artifact_key(self, artifact_key):
                    nonlocal swapped
                    if not swapped:
                        swapped = True
                        with closing(sqlite3.connect(fixture.database_path)) as connection:
                            connection.execute(
                                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                                (str(replacement_path),),
                            )
                            connection.commit()
                    return await self._delegate.find_by_artifact_key(artifact_key)

                def __getattr__(self, name):
                    return getattr(self._delegate, name)

            class SwappingWork:
                def __init__(self) -> None:
                    self._inner = None

                async def __aenter__(self):
                    self._inner = fixture.unit_of_work_factory()
                    inner = await self._inner.__aenter__()
                    self.papers = inner.papers
                    self.sources = inner.sources
                    self.chunks = inner.chunks
                    self.jobs = inner.jobs
                    self.projections = inner.projections
                    self.artifacts = SwappingArtifacts(inner.artifacts)
                    return self

                async def __aexit__(self, exc_type, exc_value, traceback):
                    assert self._inner is not None
                    return await self._inner.__aexit__(exc_type, exc_value, traceback)

                async def commit(self):
                    assert self._inner is not None
                    await self._inner.commit()

            class StructuredProvider:
                provider_id = "enqueue-path-provider"
                model_id = "enqueue-path-model"

                async def generate(self, _request):
                    raise AssertionError("enqueue must not call the provider")

            service = DocumentArtifactService(
                SwappingWork,
                context_builder=fixture.builder,
                structured_provider=StructuredProvider(),
                clock=lambda: NOW,
            )

            with self.assertRaises(PersistenceConflictError) as raised:
                await service.enqueue(
                    "paper-1", source_id, "native", "classification", now=NOW
                )
            self.assertEqual("PERSISTENCE_CONFLICT", raised.exception.code)
            self.assertTrue(swapped)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                self.assertEqual(
                    0,
                    connection.execute("SELECT count(*) FROM generated_artifacts").fetchone()[0],
                )
                self.assertEqual(
                    0,
                    connection.execute("SELECT count(*) FROM processing_jobs").fetchone()[0],
                )

    async def test_concurrent_identical_enqueue_returns_one_artifact_and_job(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"%PDF-1.7\nconcurrent artifact enqueue fixture\n"
        source_id = "src_concurrent_enqueue"
        async with p3_context_fixture(
            prefix="study-app-p3-artifact-enqueue-concurrent-",
            source_id=source_id,
            markdown="# Abstract\n\nconcurrent enqueue sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="b" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "concurrent.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            arrived = 0
            release = asyncio.Event()

            class BlockingArtifacts:
                def __init__(self, delegate) -> None:
                    self._delegate = delegate

                async def find_by_artifact_key(self, artifact_key):
                    nonlocal arrived
                    arrived += 1
                    if arrived == 2:
                        release.set()
                    await release.wait()
                    return await self._delegate.find_by_artifact_key(artifact_key)

                def __getattr__(self, name):
                    return getattr(self._delegate, name)

            class BlockingWork:
                def __init__(self) -> None:
                    self._inner = None

                async def __aenter__(self):
                    self._inner = fixture.unit_of_work_factory()
                    inner = await self._inner.__aenter__()
                    self.papers = inner.papers
                    self.sources = inner.sources
                    self.chunks = inner.chunks
                    self.jobs = inner.jobs
                    self.projections = inner.projections
                    self.artifacts = BlockingArtifacts(inner.artifacts)
                    return self

                async def __aexit__(self, exc_type, exc_value, traceback):
                    assert self._inner is not None
                    return await self._inner.__aexit__(exc_type, exc_value, traceback)

                async def commit(self):
                    assert self._inner is not None
                    await self._inner.commit()

            class StructuredProvider:
                provider_id = "concurrent-provider"
                model_id = "concurrent-model"

                async def generate(self, _request):
                    raise AssertionError("enqueue must not call the provider")

            service = DocumentArtifactService(
                BlockingWork,
                context_builder=fixture.builder,
                structured_provider=StructuredProvider(),
                clock=lambda: NOW,
            )
            first, second = await asyncio.gather(
                service.enqueue("paper-1", source_id, "native", "classification", now=NOW),
                service.enqueue("paper-1", source_id, "native", "classification", now=NOW),
            )

            self.assertEqual(2, arrived)
            self.assertEqual(first.artifact.id, second.artifact.id)
            self.assertEqual(first.job.id, second.job.id)
            self.assertEqual([False, True], sorted((first.deduplicated, second.deduplicated)))
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                self.assertEqual(
                    1,
                    connection.execute("SELECT count(*) FROM generated_artifacts").fetchone()[0],
                )
                self.assertEqual(
                    1,
                    connection.execute("SELECT count(*) FROM processing_jobs").fetchone()[0],
                )

    async def test_explainer_deep_profile_has_distinct_frozen_artifact_and_job_identity(
        self,
    ) -> None:
        """A deep explainer is a distinct ContextPlan consumer, never a standard cache hit."""

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        pdf_bytes = b"%PDF-1.7\ndeep profile identity fixture\n"
        source_id = "src_deep_profile_identity"
        async with p3_context_fixture(
            prefix="study-app-p3-deep-profile-identity-",
            source_id=source_id,
            markdown="# Abstract\n\nprofile identity sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            options_hash="b" * 64,
        ) as fixture:
            pdf_path = fixture.database_path.parent / "paper-1.pdf"
            pdf_path.write_bytes(pdf_bytes)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                    (str(pdf_path),),
                )
                connection.commit()

            class StructuredProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                async def generate(self, _request):
                    raise AssertionError("enqueue must not call the provider")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=StructuredProvider(),
                clock=lambda: NOW,
            )

            standard = await service.enqueue(
                "paper-1", source_id, "native", "explainer", now=NOW
            )
            deep = await service.enqueue(
                "paper-1", source_id, "native", "explainer", profile="deep", now=NOW
            )
            deep_again = await service.enqueue(
                "paper-1", source_id, "native", "explainer", profile="deep", now=NOW
            )

            self.assertNotEqual(standard.artifact.id, deep.artifact.id)
            self.assertNotEqual(standard.job.idempotency_key, deep.job.idempotency_key)
            self.assertEqual("standard", standard.job.spec.profile)
            self.assertEqual("deep", deep.job.spec.profile)
            self.assertNotEqual(standard.artifact.prompt_version, deep.artifact.prompt_version)
            self.assertTrue(deep_again.deduplicated)
            self.assertEqual(deep.artifact.id, deep_again.artifact.id)
            self.assertEqual(deep.job.id, deep_again.job.id)

    async def test_deep_explainer_provider_requests_carry_the_frozen_profile(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        async with p3_context_fixture(
            prefix="study-app-p3-deep-profile-provider-request-",
            source_id="src_deep_profile_provider_request",
            markdown="# Abstract\n\ndeep provider request sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            lease = await _seed_claimed_artifact(
                fixture,
                kind="explainer",
                artifact_id="artifact_deep_profile_provider_request",
                job_id="job_deep_profile_provider_request",
                provider="deep-structured",
                model="deep-structured-model",
                prompt_version="explainer-context-deep-v1",
                profile="deep",
            )

            class RecordingProvider:
                provider_id = "deep-structured"
                model_id = "deep-structured-model"

                def __init__(self) -> None:
                    self.calls = []

                async def generate(self, request):
                    self.calls.append(request)
                    if request.stage == "map":
                        return json.dumps(
                            {
                                "coveredRanges": [
                                    list(item) for item in request.batch.covered_ranges
                                ],
                                "markdown": "## Deep section\n\ndeep provider request sentinel.\n",
                            },
                            ensure_ascii=False,
                        )
                    return json.dumps(
                        {
                            "coveredRanges": [
                                list(item)
                                for child in request.inputs
                                for item in child.covered_ranges
                            ],
                            "markdown": "# Deep explainer\n\ndeep provider request sentinel.\n",
                        },
                        ensure_ascii=False,
                    )

            provider = RecordingProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            await service.run(lease, "artifact_deep_profile_provider_request")

            self.assertTrue(provider.calls)
            self.assertEqual(
                ["deep"] * len(provider.calls),
                [request.profile for request in provider.calls],
            )
            self.assertEqual(
                ["explainer-context-deep-v1"] * len(provider.calls),
                [request.prompt_version for request in provider.calls],
            )

    async def test_structured_worker_rejects_profile_and_prompt_identity_mismatch_before_provider(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import PersistenceConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.tests.support.p3_database import p3_context_fixture

        async with p3_context_fixture(
            prefix="study-app-p3-structured-profile-mismatch-",
            source_id="src_structured_profile_mismatch",
            markdown="# Abstract\n\nprofile mismatch sentinel.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            lease = await _seed_claimed_artifact(
                fixture,
                kind="explainer",
                artifact_id="artifact_structured_profile_mismatch",
                job_id="job_structured_profile_mismatch",
                provider="profile-fence",
                model="profile-fence-model",
                prompt_version="explainer-context-v1",
                profile="deep",
            )

            class FailingProvider:
                provider_id = "profile-fence"
                model_id = "profile-fence-model"

                async def generate(self, _request):
                    raise AssertionError("profile mismatch reached the provider")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=FailingProvider(),
                clock=lambda: NOW,
            )

            with self.assertRaises(PersistenceConflictError) as raised:
                await service.run(lease, "artifact_structured_profile_mismatch")
            self.assertEqual("PERSISTENCE_CONFLICT", raised.exception.code)

    async def test_classification_consumes_only_its_context_plan_and_atomically_publishes_projection(
        self,
    ) -> None:
        """A claimed classification job never crosses the full-document seam."""

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.structured_artifact_provider import (
            StructuredArtifactRequest,
        )
        from backend.app.domain import GeneratedArtifact
        from backend.app.domain.context import ChunkingSpec
        from backend.app.domain.processing import (
            ExplainJobSpecV1,
            NewProcessingJob,
            build_artifact_job_key,
            build_artifact_key,
            encode_job_spec_v1,
            hash_job_spec,
        )
        from backend.tests.support.p3_database import p3_context_fixture

        markdown = (
            "# bounded classification title sentinel\n\n"
            "# Abstract\n\nbounded abstract sentinel.\n\n"
            "# Introduction\n\nlegacy abstract poison and middle poison.\n\n"
            "# Methods Overview\n\nbounded methods sentinel.\n\n"
            "# Experiments\n\nfull markdown poison and PDF poison.\n\n"
            "# Conclusion\n\nbounded tail conclusion sentinel.\n\n"
            "# References\n\nreference poison sentinel.\n"
        )
        provider_id = "fake-structured"
        model_id = "fake-structured-model"
        prompt_version = "classification-v1"
        source_id = "src_classification_artifact"
        artifact_id = "artifact_classification"
        job_id = "job_classification"
        output = {
            "type": "评测",
            "topic": "鲁棒多模态评估",
            "task": "classification",
            "models": ["Model-A"],
            "datasets": ["Dataset-B"],
            "tags": ["robustness"],
            "relevance": 0.91,
        }
        expected_content = json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        async with p3_context_fixture(
            prefix="study-app-p3-document-artifact-classification-",
            source_id=source_id,
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=6, hard_cap_tokens=6),
            now=NOW,
            pdf_sha256="a" * 64,
            options_hash="b" * 64,
        ) as fixture:
            artifact = GeneratedArtifact(
                id=artifact_id,
                paper_id="paper-1",
                kind="classification",
                source_document_id=source_id,
                status="queued",
                generator_provider=provider_id,
                generator_model=model_id,
                prompt_version=prompt_version,
                created_at=NOW,
                updated_at=NOW,
            )
            spec = ExplainJobSpecV1(
                paper_id="paper-1",
                source_document_id=source_id,
                artifact_id=artifact_id,
                profile="standard",
                provider=provider_id,
                model=model_id,
                prompt_version=prompt_version,
            )
            raw_spec = encode_job_spec_v1(spec)
            artifact_key = build_artifact_key(
                kind="classification",
                source_document_id=source_id,
                source_content_sha256=fixture.chunk_set.source_content_sha256,
                generator_provider=provider_id,
                generator_model=model_id,
                prompt_version=prompt_version,
                kind_specific_options={"profile": "standard"},
            )
            job = NewProcessingJob(
                id=job_id,
                spec=spec,
                idempotency_key=build_artifact_job_key(
                    artifact_key,
                    hash_job_spec(raw_spec),
                ),
                created_at=NOW,
                max_attempts=3,
            )
            async with fixture.unit_of_work_factory() as work:
                await work.artifacts.add(artifact)
                await work.jobs.insert_with_spec(
                    job,
                    spec_json=raw_spec,
                    spec_sha256=hash_job_spec(raw_spec),
                )
                await work.commit()
            async with fixture.session_factory() as session:
                await session.execute(
                    __import__("sqlalchemy").text(
                        "UPDATE generated_artifacts SET artifact_key=:artifact_key "
                        "WHERE id=:artifact_id"
                    ),
                    {"artifact_key": artifact_key, "artifact_id": artifact_id},
                )
                await session.commit()
            async with fixture.unit_of_work_factory() as work:
                lease = await work.jobs.claim_next(
                    worker_id="classification-worker",
                    now=NOW,
                    lease_seconds=3600,
                )
                await work.commit()
            self.assertIsNotNone(lease)

            class RecordingProvider:
                provider_id = "fake-structured"
                model_id = "fake-structured-model"

                def __init__(self) -> None:
                    self.calls: list[StructuredArtifactRequest] = []

                async def generate(self, request: StructuredArtifactRequest) -> str:
                    self.calls.append(request)
                    return expected_content

            provider = RecordingProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                structured_provider=provider,
                clock=lambda: NOW,
            )

            published = await service.run(lease, artifact_id)

            self.assertEqual("ready", published.status.value)
            self.assertEqual(expected_content, published.content)
            self.assertEqual(1, len(provider.calls))
            request = provider.calls[0]
            self.assertEqual("classification", request.kind)
            self.assertEqual("classification", request.plan.request_consumer)
            selected = "".join(
                chunk.content
                for batch in request.plan.batches
                for chunk in batch.chunks
            )
            self.assertIn("bounded classification title sentinel", selected)
            self.assertIn("bounded abstract sentinel", selected)
            self.assertIn("bounded methods sentinel", selected)
            self.assertIn("bounded tail conclusion sentinel", selected)
            self.assertNotIn("poison", selected)
            self.assertNotIn(markdown, selected)
            self.assertFalse(hasattr(request, "source_markdown"))
            self.assertFalse(hasattr(request, "pdf"))
            self.assertFalse(hasattr(request, "legacy_abstract"))

            with closing(sqlite3.connect(fixture.database_path)) as connection:
                artifact_row = connection.execute(
                    "SELECT status,content,content_sha256 FROM generated_artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                head = connection.execute(
                    "SELECT artifact_id FROM paper_artifact_heads "
                    "WHERE paper_id='paper-1' AND kind='classification'"
                ).fetchone()
                projection = connection.execute(
                    "SELECT type,topic,task,models,datasets,tags,relevance "
                    "FROM papers WHERE id='paper-1'"
                ).fetchone()
                job_row = connection.execute(
                    "SELECT status,result_json,lease_owner,lease_token,lease_expires_at "
                    "FROM processing_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
            self.assertEqual(
                (
                    "ready",
                    expected_content,
                    hashlib.sha256(expected_content.encode("utf-8")).hexdigest(),
                ),
                artifact_row,
            )
            self.assertEqual((artifact_id,), head)
            self.assertEqual(
                (
                    output["type"],
                    output["topic"],
                    output["task"],
                    json.dumps(output["models"], ensure_ascii=False, separators=(",", ":")),
                    json.dumps(output["datasets"], ensure_ascii=False, separators=(",", ":")),
                    json.dumps(output["tags"], ensure_ascii=False, separators=(",", ":")),
                    output["relevance"],
                ),
                projection,
            )
            self.assertEqual("succeeded", job_row[0])
            self.assertEqual(
                {
                    "artifactId": artifact_id,
                    "contentSha256": hashlib.sha256(
                        expected_content.encode("utf-8")
                    ).hexdigest(),
                },
                json.loads(job_row[1]),
            )
            self.assertEqual((None, None, None), job_row[2:])


if __name__ == "__main__":
    unittest.main()
