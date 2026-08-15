from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import os
import sqlite3
import unittest

if os.name == "nt" and hasattr(os, "add_dll_directory") and os.environ.get("P3_SQLITE_DLL_DIR"):
    _SQLITE_DLL_HANDLE = os.add_dll_directory(os.environ["P3_SQLITE_DLL_DIR"])


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


class TranslationResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_empty_provider_output_fails_checkpoint_and_job_closed(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-empty-output-",
            markdown="Provider must return content.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
        ) as fixture:
            class EmptyProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    return "   "

            provider = EmptyProvider()
            worker_now = NOW + timedelta(hours=1, seconds=1)
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: worker_now,
                ),
                clock=lambda: worker_now,
            )

            async def handle_translation(lease):
                await service.run(lease, fixture.artifact_id)
                return ProcessingHandlerOutcome.settled()

            worker = ProcessingWorker(
                fixture.unit_of_work_factory,
                handlers={"translate": handle_translation},
                worker_id="translation-empty-output-worker",
                clock=lambda: worker_now,
                lease_seconds=3600,
            )

            self.assertTrue(await worker.run_once())

            async with fixture.session_factory() as session:
                checkpoint = (
                    await session.execute(
                        text(
                            "SELECT status,error_code,translated_markdown "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id AND sequence=0"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).one()
                job = (
                    await session.execute(
                        text(
                            "SELECT status,attempt,error_code,finished_at "
                            "FROM processing_jobs WHERE id=:job_id"
                        ),
                        {"job_id": fixture.job_id},
                    )
                ).one()
            self.assertEqual([0], [request.sequence for request in provider.calls])
            self.assertEqual(("failed", "ARTIFACT_OUTPUT_INVALID", None), checkpoint)
            self.assertEqual("failed", job.status)
            self.assertEqual(2, job.attempt)
            self.assertEqual("ARTIFACT_OUTPUT_INVALID", job.error_code)
            self.assertIsNotNone(job.finished_at)

    async def test_worker_transient_failure_requeues_then_resumes_only_failed_chunk(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import TranslationProviderRequestError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.app.workers.processing_worker import (
            ProcessingHandlerOutcome,
            ProcessingWorker,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        markdown = "First chunk.\nSecond chunk.\n"
        async with p3_translation_fixture(
            prefix="study-app-p3-translation-transient-resume-",
            markdown=markdown,
            spec=ChunkingSpec(target_tokens=2, hard_cap_tokens=2),
            now=NOW,
        ) as fixture:
            class Provider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []
                    self.fail_once = True

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    if request.sequence == 1 and self.fail_once:
                        self.fail_once = False
                        raise TranslationProviderRequestError(retryable=True)
                    return f"translated-{request.sequence}\n"

            provider = Provider()
            worker_now = [NOW + timedelta(hours=1, seconds=1)]
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: worker_now[0],
                ),
                clock=lambda: worker_now[0],
            )

            published = []

            async def handle_translation(lease):
                published.append(await service.run(lease, fixture.artifact_id))
                return ProcessingHandlerOutcome.settled()

            worker = ProcessingWorker(
                fixture.unit_of_work_factory,
                handlers={"translate": handle_translation},
                worker_id="translation-resume-worker",
                clock=lambda: worker_now[0],
                lease_seconds=3600,
            )

            self.assertTrue(await worker.run_once())
            async with fixture.session_factory() as session:
                checkpoint_rows = (
                    await session.execute(
                        text(
                            "SELECT sequence,status,error_code,translated_markdown "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id ORDER BY sequence"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).all()
                job_row = (
                    await session.execute(
                        text(
                            "SELECT status,attempt,error_code FROM processing_jobs "
                            "WHERE id=:job_id"
                        ),
                        {"job_id": fixture.job_id},
                    )
                ).one()
            self.assertEqual(
                [
                    (0, "succeeded", None, "translated-0\n"),
                    (1, "failed", "TRANSLATION_PROVIDER_REQUEST_FAILED", None),
                ],
                checkpoint_rows,
            )
            self.assertEqual(
                ("queued", 2, "TRANSLATION_PROVIDER_REQUEST_FAILED"),
                job_row,
            )

            worker_now[0] += timedelta(seconds=11)
            self.assertTrue(await worker.run_once())
            chunk_count = len(fixture.chunk_set.chunks)
            self.assertGreaterEqual(chunk_count, 2)
            self.assertEqual(
                [0, 1, *range(1, chunk_count)],
                [request.sequence for request in provider.calls],
            )
            self.assertEqual(1, len(published))
            expected_content = "".join(
                f"translated-{sequence}\n" for sequence in range(chunk_count)
            )
            self.assertEqual(expected_content, published[0].content)
            async with fixture.session_factory() as session:
                final_rows = (
                    await session.execute(
                        text(
                            "SELECT j.status,j.attempt,a.status,a.content,t.content "
                            "FROM processing_jobs j "
                            "JOIN generated_artifacts a ON a.id=j.artifact_id "
                            "JOIN translations t ON t.paper_id=j.paper_id "
                            "WHERE j.id=:job_id"
                        ),
                        {"job_id": fixture.job_id},
                    )
                ).one()
            self.assertEqual(
                (
                    "succeeded",
                    3,
                    "ready",
                    expected_content,
                    expected_content,
                ),
                final_rows,
            )

    async def test_checkpoint_identity_conflict_fails_closed_with_frozen_typed_code(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import TranslationCheckpointConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-checkpoint-conflict-",
            markdown="Only chunk.\n",
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
        ) as fixture:
            async with fixture.session_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO artifact_translation_checkpoints("
                        "artifact_id,chunk_id,sequence,source_content_sha256,provider,model,"
                        "prompt_version,status,translated_markdown,content_sha256,attempt,"
                        "error_code,error_message,created_at,updated_at) VALUES("
                        ":artifact_id,:chunk_id,0,:source_sha,'wrong-provider',:model,"
                        ":prompt_version,'failed',NULL,NULL,1,'OLD_FAILURE',NULL,:now,:now)"
                    ),
                    {
                        "artifact_id": fixture.artifact_id,
                        "chunk_id": fixture.chunk_set.chunks[0].id,
                        "source_sha": fixture.chunk_set.source_content_sha256,
                        "model": fixture.model,
                        "prompt_version": fixture.prompt_version,
                        "now": NOW.isoformat().replace("+00:00", "Z"),
                    },
                )
                await session.commit()

            class Provider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                async def translate(self, _request: TranslationRequest) -> str:
                    raise AssertionError("conflicted checkpoint must not call provider")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=Provider(),
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            with self.assertRaises(TranslationCheckpointConflictError) as raised:
                await service.run(fixture.lease, fixture.artifact_id)
            self.assertEqual("TRANSLATION_CHECKPOINT_CONFLICT", raised.exception.code)
            self.assertFalse(raised.exception.retryable)

    async def test_lease_loss_after_provider_result_preserves_prior_checkpoint_and_stops_next_chunk(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import JobLeaseLostError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-lease-loss-",
            markdown="First chunk.\nSecond chunk.\nThird chunk.\n",
            spec=ChunkingSpec(target_tokens=2, hard_cap_tokens=2),
            now=NOW,
        ) as fixture:
            class Provider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    if request.sequence == 1:
                        with closing(sqlite3.connect(fixture.database_path)) as connection:
                            connection.execute(
                                "UPDATE processing_jobs SET lease_token=? WHERE id=?",
                                ("replacement-owner-token", fixture.job_id),
                            )
                            connection.commit()
                    return f"translated-{request.sequence}\n"

            provider = Provider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            with self.assertRaises(JobLeaseLostError) as raised:
                await service.run(fixture.lease, fixture.artifact_id)
            self.assertEqual("JOB_LEASE_LOST", raised.exception.code)
            self.assertEqual([0, 1], [request.sequence for request in provider.calls])

            async with fixture.session_factory() as session:
                checkpoints = (
                    await session.execute(
                        text(
                            "SELECT sequence,status,translated_markdown "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id ORDER BY sequence"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).all()
                job_row = (
                    await session.execute(
                        text(
                            "SELECT status,lease_token FROM processing_jobs WHERE id=:job_id"
                        ),
                        {"job_id": fixture.job_id},
                    )
                ).one()
                artifact_status = (
                    await session.execute(
                        text(
                            "SELECT status FROM generated_artifacts WHERE id=:artifact_id"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).scalar_one()
            self.assertEqual([(0, "succeeded", "translated-0\n")], checkpoints)
            self.assertEqual(("running", "replacement-owner-token"), job_row)
            self.assertEqual("running", artifact_status)

    async def test_publication_recomputes_persisted_chunk_content_identity_after_provider_call(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.domain import PersistenceConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-publication-content-identity-",
            markdown="Original publication chunk.\n",
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=8),
            now=NOW,
        ) as fixture:
            class MutatingProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                async def translate(self, request) -> str:
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        connection.execute(
                            "UPDATE document_chunks SET content=? WHERE id=?",
                            ("Tampered publication chunk.\n", request.chunk_id),
                        )
                        connection.commit()
                    return "Translated publication chunk.\n"

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=MutatingProvider(),
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            with self.assertRaises(PersistenceConflictError) as raised:
                await service.run(fixture.lease, fixture.artifact_id)

            self.assertEqual("PERSISTENCE_CONFLICT", raised.exception.code)
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                artifact = connection.execute(
                    "SELECT status,content FROM generated_artifacts WHERE id=?",
                    (fixture.artifact_id,),
                ).fetchone()
                job = connection.execute(
                    "SELECT status FROM processing_jobs WHERE id=?",
                    (fixture.job_id,),
                ).fetchone()
            self.assertEqual(("running", None), artifact)
            self.assertEqual(("running",), job)

    async def test_cancel_request_preserves_succeeded_checkpoint_and_stops_before_next_provider_call(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import JobLeaseLostError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-cancel-resume-",
            markdown="Preserved first.\nNever call second.\n",
            spec=ChunkingSpec(target_tokens=2, hard_cap_tokens=2),
            now=NOW,
            succeeded_checkpoints={0: "preserved translation\n"},
        ) as fixture:
            async with fixture.unit_of_work_factory() as work:
                await work.jobs.cancel(fixture.job_id, now=NOW)
                await work.commit()

            class Provider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    return request.markdown

            provider = Provider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            with self.assertRaises(JobLeaseLostError):
                await service.run(fixture.lease, fixture.artifact_id)
            self.assertEqual([], provider.calls)
            async with fixture.session_factory() as session:
                checkpoints = (
                    await session.execute(
                        text(
                            "SELECT sequence,status,translated_markdown "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id ORDER BY sequence"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).all()
            self.assertEqual([(0, "succeeded", "preserved translation\n")], checkpoints)

    async def test_structured_placeholder_loss_duplicate_and_reorder_fail_closed(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import MarkdownStructureInvalidError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        markdown = "| Key | Formula |\n| --- | --- |\n| value | $x$ |\n"
        for mutation in ("missing", "duplicate", "reordered"):
            with self.subTest(mutation=mutation):
                async with p3_translation_fixture(
                    prefix=f"study-app-p3-translation-placeholder-{mutation}-",
                    markdown=markdown,
                    spec=ChunkingSpec(target_tokens=16, hard_cap_tokens=16),
                    now=NOW,
                ) as fixture:
                    class Provider:
                        provider_id = fixture.provider
                        model_id = fixture.model
                        prompt_version = fixture.prompt_version

                        async def translate(self, request: TranslationRequest) -> str:
                            tokens = [
                                part
                                for part in request.markdown.replace("\n", " ").split()
                                if part.startswith("P3MD_")
                            ]
                            self.assertGreaterEqual(len(tokens), 2)
                            if mutation == "missing":
                                return request.markdown.replace(tokens[0], "", 1)
                            if mutation == "duplicate":
                                return request.markdown.replace(
                                    tokens[0], f"{tokens[0]} {tokens[0]}", 1
                                )
                            first = request.markdown.find(tokens[0])
                            second = request.markdown.find(tokens[1])
                            return (
                                request.markdown[:first]
                                + tokens[1]
                                + request.markdown[first + len(tokens[0]) : second]
                                + tokens[0]
                                + request.markdown[second + len(tokens[1]) :]
                            )

                        @staticmethod
                        def assertGreaterEqual(left: int, right: int) -> None:
                            if left < right:
                                raise AssertionError("structured fixture needs placeholders")

                    service = DocumentArtifactService(
                        fixture.unit_of_work_factory,
                        context_builder=fixture.builder,
                        translation_provider=Provider(),
                        checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                            fixture.session_factory,
                            clock=lambda: NOW,
                        ),
                        clock=lambda: NOW,
                    )

                    with self.assertRaises(MarkdownStructureInvalidError) as raised:
                        await service.run(fixture.lease, fixture.artifact_id)
                    self.assertEqual("MARKDOWN_STRUCTURE_INVALID", raised.exception.code)
                    self.assertFalse(raised.exception.retryable)

    async def test_generic_provider_failure_is_typed_retryable_after_preserving_checkpoint(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import TranslationProviderRequestError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-generic-failure-",
            markdown="Provider failure chunk.\n",
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
        ) as fixture:
            class Provider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                async def translate(self, _request: TranslationRequest) -> str:
                    raise RuntimeError("provider raw response must not escape")

            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=Provider(),
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            with self.assertRaises(TranslationProviderRequestError) as raised:
                await service.run(fixture.lease, fixture.artifact_id)
            self.assertEqual("TRANSLATION_PROVIDER_REQUEST_FAILED", raised.exception.code)
            self.assertTrue(raised.exception.retryable)
            self.assertNotIn("raw response", str(raised.exception))
            async with fixture.session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT status,error_code,error_message "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id AND sequence=0"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).one()
            self.assertEqual(
                ("failed", "TRANSLATION_PROVIDER_REQUEST_FAILED", None),
                row,
            )

    async def test_checkpoint_repository_rejects_failed_identity_change_without_overwrite(
        self,
    ) -> None:
        from sqlalchemy import text

        from backend.app.domain import TranslationCheckpointConflictError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-failed-identity-",
            markdown="Checkpoint identity.\n",
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
        ) as fixture:
            checkpoint = SqlAlchemyTranslationCheckpointRepository(
                fixture.session_factory,
                clock=lambda: NOW,
            )
            chunk = fixture.chunk_set.chunks[0]
            await checkpoint.save_failure(
                lease=fixture.lease,
                artifact_id=fixture.artifact_id,
                chunk_id=chunk.id,
                sequence=0,
                source_content_sha256=fixture.chunk_set.source_content_sha256,
                provider=fixture.provider,
                model=fixture.model,
                prompt_version=fixture.prompt_version,
                error_code="TRANSIENT_FAILURE",
            )
            with self.assertRaises(TranslationCheckpointConflictError):
                await checkpoint.save_success(
                    lease=fixture.lease,
                    artifact_id=fixture.artifact_id,
                    chunk_id=chunk.id,
                    sequence=0,
                    source_content_sha256=fixture.chunk_set.source_content_sha256,
                    provider="changed-provider",
                    model=fixture.model,
                    prompt_version=fixture.prompt_version,
                    translated_markdown="must not overwrite\n",
                )
            async with fixture.session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT provider,status,error_code,translated_markdown "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id AND sequence=0"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).one()
            self.assertEqual(
                (fixture.provider, "failed", "TRANSIENT_FAILURE", None),
                row,
            )

    async def test_structured_table_row_layout_and_alignment_fail_closed(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import MarkdownStructureInvalidError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        markdown = "| Key | Value |\n| --- | :---: |\n| score | $x$ |\n"
        for mutation in ("merged-row", "alignment"):
            with self.subTest(mutation=mutation):
                async with p3_translation_fixture(
                    prefix=f"study-app-p3-translation-table-{mutation}-",
                    markdown=markdown,
                    spec=ChunkingSpec(target_tokens=16, hard_cap_tokens=16),
                    now=NOW,
                ) as fixture:
                    class Provider:
                        provider_id = fixture.provider
                        model_id = fixture.model
                        prompt_version = fixture.prompt_version

                        async def translate(self, request: TranslationRequest) -> str:
                            if mutation == "merged-row":
                                return request.markdown.replace("\n", " ", 1)
                            return request.markdown.replace("---", "translated", 1)

                    service = DocumentArtifactService(
                        fixture.unit_of_work_factory,
                        context_builder=fixture.builder,
                        translation_provider=Provider(),
                        checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                            fixture.session_factory,
                            clock=lambda: NOW,
                        ),
                        clock=lambda: NOW,
                    )

                    with self.assertRaises(MarkdownStructureInvalidError) as raised:
                        await service.run(fixture.lease, fixture.artifact_id)
                    self.assertEqual("MARKDOWN_STRUCTURE_INVALID", raised.exception.code)

    async def test_structured_provider_cannot_inject_extra_math_or_escaped_delimiters(
        self,
    ) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain import MarkdownStructureInvalidError
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        markdown = "| Key | Value |\n| --- | --- |\n| score | $x$ \\| |\n"
        for injected in (" $provider_math$", " \\*"):
            with self.subTest(injected=injected):
                async with p3_translation_fixture(
                    prefix="study-app-p3-translation-structure-injection-",
                    markdown=markdown,
                    spec=ChunkingSpec(target_tokens=16, hard_cap_tokens=16),
                    now=NOW,
                ) as fixture:
                    class Provider:
                        provider_id = fixture.provider
                        model_id = fixture.model
                        prompt_version = fixture.prompt_version

                        async def translate(self, request: TranslationRequest) -> str:
                            return request.markdown + injected

                    service = DocumentArtifactService(
                        fixture.unit_of_work_factory,
                        context_builder=fixture.builder,
                        translation_provider=Provider(),
                        checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                            fixture.session_factory,
                            clock=lambda: NOW,
                        ),
                        clock=lambda: NOW,
                    )

                    with self.assertRaises(MarkdownStructureInvalidError):
                        await service.run(fixture.lease, fixture.artifact_id)

    async def test_translation_structure_and_checkpoint_conflicts_are_not_explicitly_retryable(
        self,
    ) -> None:
        from backend.app.domain.context import ChunkingSpec
        from backend.app.domain.processing import JobFailure
        from backend.tests.support.p3_database import p3_translation_fixture

        for code in ("MARKDOWN_STRUCTURE_INVALID", "TRANSLATION_CHECKPOINT_CONFLICT"):
            with self.subTest(code=code):
                async with p3_translation_fixture(
                    prefix="study-app-p3-translation-nonretryable-",
                    markdown="Terminal translation.\n",
                    spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
                    now=NOW,
                ) as fixture:
                    async with fixture.unit_of_work_factory() as work:
                        await work.jobs.fail(
                            fixture.lease,
                            JobFailure(code=code, retryable=False),
                            now=NOW,
                        )
                        await work.commit()
                    async with fixture.unit_of_work_factory() as work:
                        with self.assertRaises(Exception) as raised:
                            await work.jobs.retry(fixture.job_id, now=NOW)
                    self.assertEqual("JOB_NOT_RETRYABLE", getattr(raised.exception, "code", None))

    async def test_verbatim_is_checkpointed_without_provider_and_structured_layout_is_protected(self) -> None:
        """Translation consumes every chunk while preserving markdown structure.

        The provider boundary is deliberately observed through the public
        ``TranslationRequest``.  Verbatim chunks must never cross it; a
        structured request may contain protected placeholders, but the final
        artifact must restore the table/math/delimiter bytes exactly.
        """
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        source_markdown = (
            "# Structure test\n\n"
            "Plain prose sentinel.\n\n"
            "```python\n"
            "value = 1  # fence | $x$ `tick`\n"
            "```\n\n"
            "| Metric | Value |\n"
            "| --- | --- |\n"
            "| score | $x$ \\| literal |\n\n"
            "Tail structure sentinel.\n"
        )

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-structure-",
            markdown=source_markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=32),
            now=NOW,
        ) as fixture:
            class StructureProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    if request.content_kind == "verbatim":
                        raise AssertionError("verbatim chunks must not call the provider")
                    if request.content_kind == "structured":
                        # Return the protected payload unchanged.  The service
                        # still has to validate and restore its structure.
                        return request.markdown
                    return request.markdown.replace(
                        "Plain prose sentinel.", "Translated prose sentinel."
                    ).replace(
                        "Tail structure sentinel.", "Translated tail sentinel."
                    )

            provider = StructureProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            artifact = await service.run(fixture.lease, fixture.artifact_id)

            self.assertTrue(provider.calls)
            self.assertNotIn("verbatim", [call.content_kind for call in provider.calls])
            self.assertIn("structured", [call.content_kind for call in provider.calls])
            structured_request = next(
                call for call in provider.calls if call.content_kind == "structured"
            )
            self.assertNotIn("|", structured_request.markdown)
            self.assertNotIn("$x$", structured_request.markdown)
            self.assertNotIn("\\|", structured_request.markdown)
            self.assertIn("P3MD_", structured_request.markdown)
            self.assertIn("Translated prose sentinel.", artifact.content or "")
            self.assertIn("Translated tail sentinel.", artifact.content or "")
            self.assertIn("```python\nvalue = 1  # fence | $x$ `tick`\n```", artifact.content or "")
            self.assertIn(
                "| Metric | Value |\n| --- | --- |\n| score | $x$ \\| literal |",
                artifact.content or "",
            )

    async def test_same_line_display_math_is_protected_while_tail_is_translated(self) -> None:
        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        source_markdown = (
            "$$x + y$$ first tail sentinel.\n\n"
            "\\[z^2\\] second tail sentinel.\n"
        )
        async with p3_translation_fixture(
            prefix="study-app-p3-translation-display-tail-",
            markdown=source_markdown,
            spec=ChunkingSpec(target_tokens=8, hard_cap_tokens=16),
            now=NOW,
        ) as fixture:
            class DisplayMathProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    self.calls.append(request)
                    return request.markdown.replace(
                        "first tail sentinel", "translated first tail"
                    ).replace(
                        "second tail sentinel", "translated second tail"
                    )

            provider = DisplayMathProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            artifact = await service.run(fixture.lease, fixture.artifact_id)

        self.assertEqual(2, len(provider.calls))
        protected_requests = "".join(call.markdown for call in provider.calls)
        self.assertNotIn("$", protected_requests)
        self.assertNotIn("\\[", protected_requests)
        self.assertNotIn("\\]", protected_requests)
        self.assertNotIn("x + y", protected_requests)
        self.assertNotIn("z^2", protected_requests)
        self.assertEqual(2, protected_requests.count("P3MD_"))
        self.assertIn("$$x + y$$ translated first tail.", artifact.content or "")
        self.assertIn("\\[z^2\\] translated second tail.", artifact.content or "")

    async def test_resume_skips_succeeded_chunk_and_atomically_publishes_complete_tail(self) -> None:
        from sqlalchemy import event, text

        from backend.app.application.document_artifacts import DocumentArtifactService
        from backend.app.application.ports.translation_provider import TranslationRequest
        from backend.app.domain.context import ChunkingSpec
        from backend.app.repositories.translation_checkpoints import (
            SqlAlchemyTranslationCheckpointRepository,
        )
        from backend.tests.support.p3_database import p3_translation_fixture

        source_markdown = (
            "Alpha one two.\n"
            "Beta three four.\n"
            "Tail sentinel five.\n"
        )
        expected_translation = "甲一二。\n乙三四。\n尾部哨兵五。\n"
        provider_outputs = {
            "Beta three four.\n": "乙三四。\n",
            "Tail sentinel five.\n": "尾部哨兵五。\n",
        }

        async with p3_translation_fixture(
            prefix="study-app-p3-translation-resume-",
            markdown=source_markdown,
            spec=ChunkingSpec(target_tokens=4, hard_cap_tokens=4),
            now=NOW,
            succeeded_checkpoints={0: "甲一二。\n"},
        ) as fixture:
            active_transactions: set[int] = set()
            engine = fixture.session_factory.kw["bind"]

            def transaction_started(connection) -> None:
                active_transactions.add(id(connection))

            def transaction_finished(connection) -> None:
                active_transactions.discard(id(connection))

            event.listen(engine.sync_engine, "begin", transaction_started)
            event.listen(engine.sync_engine, "commit", transaction_finished)
            event.listen(engine.sync_engine, "rollback", transaction_finished)

            class RecordingProvider:
                provider_id = fixture.provider
                model_id = fixture.model
                prompt_version = fixture.prompt_version

                def __init__(self) -> None:
                    self.calls: list[TranslationRequest] = []

                async def translate(self, request: TranslationRequest) -> str:
                    if active_transactions:
                        raise AssertionError(
                            "translation provider was called while a database transaction was active"
                        )
                    self.calls.append(request)
                    return provider_outputs[request.markdown]

            provider = RecordingProvider()
            service = DocumentArtifactService(
                fixture.unit_of_work_factory,
                context_builder=fixture.builder,
                translation_provider=provider,
                checkpoint_repository=SqlAlchemyTranslationCheckpointRepository(
                    fixture.session_factory,
                    clock=lambda: NOW,
                ),
                clock=lambda: NOW,
            )

            try:
                artifact = await service.run(fixture.lease, fixture.artifact_id)
            finally:
                event.remove(engine.sync_engine, "begin", transaction_started)
                event.remove(engine.sync_engine, "commit", transaction_finished)
                event.remove(engine.sync_engine, "rollback", transaction_finished)

            self.assertEqual([1, 2], [request.sequence for request in provider.calls])
            self.assertEqual(
                ["Beta three four.\n", "Tail sentinel five.\n"],
                [request.markdown for request in provider.calls],
            )
            self.assertEqual(expected_translation, artifact.content)
            self.assertIn("尾部哨兵", artifact.content or "")
            self.assertEqual(
                hashlib.sha256(expected_translation.encode("utf-8")).hexdigest(),
                artifact.content_sha256,
            )

            async with fixture.session_factory() as session:
                checkpoints = (
                    await session.execute(
                        text(
                            "SELECT sequence,chunk_id,status,translated_markdown,"
                            "source_content_sha256,provider,model,prompt_version,content_sha256 "
                            "FROM artifact_translation_checkpoints "
                            "WHERE artifact_id=:artifact_id ORDER BY sequence"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).all()
                artifact_row = (
                    await session.execute(
                        text(
                            "SELECT status,content,content_sha256 FROM generated_artifacts "
                            "WHERE id=:artifact_id"
                        ),
                        {"artifact_id": fixture.artifact_id},
                    )
                ).one()
                head = (
                    await session.execute(
                        text(
                            "SELECT artifact_id FROM paper_artifact_heads "
                            "WHERE paper_id='paper-1' AND kind='translation'"
                        )
                    )
                ).scalar_one()
                legacy = (
                    await session.execute(
                        text("SELECT content FROM translations WHERE paper_id='paper-1'")
                    )
                ).scalar_one()
                job = (
                    await session.execute(
                        text(
                            "SELECT status,result_json,lease_owner,lease_token "
                            "FROM processing_jobs WHERE id=:job_id"
                        ),
                        {"job_id": fixture.job_id},
                    )
                ).one()

            self.assertEqual([0, 1, 2], [row.sequence for row in checkpoints])
            self.assertEqual(
                list(fixture.chunk_set.chunk_ids),
                [row.chunk_id for row in checkpoints],
            )
            self.assertTrue(all(row.status == "succeeded" for row in checkpoints))
            self.assertEqual(
                ["甲一二。\n", "乙三四。\n", "尾部哨兵五。\n"],
                [row.translated_markdown for row in checkpoints],
            )
            self.assertTrue(
                all(
                    row.source_content_sha256 == fixture.chunk_set.source_content_sha256
                    and row.provider == fixture.provider
                    and row.model == fixture.model
                    and row.prompt_version == fixture.prompt_version
                    and row.content_sha256
                    == hashlib.sha256(row.translated_markdown.encode("utf-8")).hexdigest()
                    for row in checkpoints
                )
            )
            self.assertEqual(
                (
                    "ready",
                    expected_translation,
                    hashlib.sha256(expected_translation.encode("utf-8")).hexdigest(),
                ),
                artifact_row,
            )
            self.assertEqual(fixture.artifact_id, head)
            self.assertEqual(expected_translation, legacy)
            self.assertEqual("succeeded", job.status)
            self.assertIn(fixture.artifact_id, job.result_json)
            self.assertIsNone(job.lease_owner)
            self.assertIsNone(job.lease_token)
