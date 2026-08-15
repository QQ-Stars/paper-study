from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import traceback
import unittest

from backend.app.application.generated_artifacts import (
    ArtifactReader,
    GenerationPipeline,
)
from backend.app.application.ports.artifact_generator import (
    GeneratorIdentity,
    PaperMetadata,
)
from backend.app.config import DatabaseSettings
from backend.app.domain import (
    ArtifactKindUnsupportedError,
    EmptyArtifactError,
    GeneratedArtifact,
    GenerationFailureError,
    SourceDocument,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64


class FakeSourcePipeline:
    def __init__(self, source: SourceDocument) -> None:
        self.source = source
        self.calls: list[tuple[str, str, str]] = []

    async def materialize_source(self, paper_id: str, source_mode: str, purpose: str) -> SourceDocument:
        self.calls.append((paper_id, str(source_mode), purpose))
        return self.source


class FakeGenerator:
    def __init__(self, output: str = "# generated\n", failure: Exception | None = None) -> None:
        self.output = output
        self.failure = failure
        self.calls: list[tuple[str, PaperMetadata, str]] = []

    def identity(self, kind: str) -> GeneratorIdentity:
        return GeneratorIdentity("fake-provider", "fake-model", f"{kind}-prompt-v1")

    def generate(self, kind: str, paper: PaperMetadata, source_markdown: str) -> str:
        self.calls.append((kind, paper, source_markdown))
        if self.failure is not None:
            raise self.failure
        return self.output


class GenerationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-generation-")
        self.database_path = Path(self._temp.name) / "legacy" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("UPDATE papers SET explainer='legacy explainer' WHERE id='paper-1'")
            connection.commit()
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]
        self.source = SourceDocument(
            id="src_generation",
            paper_id="paper-1",
            mode="native",
            status="ready",
            provider="local",
            model="native-model",
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
            processing_version="native-v1",
            created_at=NOW,
            updated_at=NOW,
            markdown="# proven source\n",
            content_sha256=hashlib.sha256(b"# proven source\n").hexdigest(),
            page_count=1,
        )
        async with SqlAlchemyUnitOfWork(self.session_factory) as work:
            await work.sources.add(self.source)
            await work.commit()
        self.ids = iter((f"art_{value:032x}" for value in range(1, 20)))

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    def _pipeline(self, generator: FakeGenerator, source_pipeline: FakeSourcePipeline | None = None) -> GenerationPipeline:
        return GenerationPipeline(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            source_pipeline or FakeSourcePipeline(self.source),
            generator,
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )

    async def test_explainer_success_is_cached_and_dual_written_atomically(self) -> None:
        source_pipeline = FakeSourcePipeline(self.source)
        generator = FakeGenerator("# explainer\n")
        pipeline = self._pipeline(generator, source_pipeline)
        first = await pipeline.generate_artifact("paper-1", "explainer", "native")
        second = await pipeline.generate_artifact("paper-1", "explainer", "native")
        self.assertEqual(first, second)
        self.assertEqual(1, len(generator.calls))
        self.assertEqual(
            [("paper-1", "native", "artifact:explainer"), ("paper-1", "native", "artifact:explainer")],
            source_pipeline.calls,
        )
        kind, metadata, source_markdown = generator.calls[0]
        self.assertEqual("explainer", kind)
        self.assertEqual(self.source.markdown, source_markdown)
        self.assertFalse(hasattr(metadata, "pdf_path"))
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual("# explainer\n", connection.execute("SELECT explainer FROM papers WHERE id='paper-1'").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT count(*) FROM generated_artifacts WHERE status='ready'").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM paper_vectors WHERE paper_id='paper-1'").fetchone()[0])

    async def test_translation_success_upserts_legacy_in_same_transaction(self) -> None:
        generator = FakeGenerator("# translation\n")
        artifact = await self._pipeline(generator).generate_artifact(
            "paper-1", "translation", "native"
        )
        self.assertEqual("translation", artifact.kind.value)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                "# translation\n",
                connection.execute("SELECT content FROM translations WHERE paper_id='paper-1'").fetchone()[0],
            )
            self.assertEqual(1, connection.execute("SELECT count(*) FROM generated_artifacts").fetchone()[0])

    async def test_success_canonicalizes_before_hash_dual_write_and_mirror(self) -> None:
        mirrored: list[tuple[str, str, str]] = []
        pipeline = GenerationPipeline(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            FakeSourcePipeline(self.source),
            FakeGenerator("line 1  \r\nline 2\t\r\n\r\n"),
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
            mirror=lambda kind, paper_id, content: mirrored.append(
                (kind, paper_id, content)
            ),
        )

        artifact = await pipeline.generate_artifact(
            "paper-1", "explainer", "native"
        )

        expected_content = "line 1\nline 2\n"
        expected_sha = "9060554863a62b9db5f726216876654e561896071d2e6480f2048b70e0fdadb9"
        self.assertEqual(expected_content, artifact.content)
        self.assertEqual(expected_sha, artifact.content_sha256)
        self.assertEqual(
            [("explainer", "paper-1", expected_content)],
            mirrored,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            stored = connection.execute(
                "SELECT content,content_sha256 FROM generated_artifacts WHERE id=?",
                (artifact.id,),
            ).fetchone()
            legacy = connection.execute(
                "SELECT explainer FROM papers WHERE id='paper-1'"
            ).fetchone()[0]
        self.assertEqual((expected_content, expected_sha), stored)
        self.assertEqual(expected_content, legacy)

    async def test_unsupported_empty_and_provider_failure_never_touch_legacy_content(self) -> None:
        for kind in ("summary", "outline", "study_card", "classification", "metadata"):
            with self.subTest(kind=kind), self.assertRaises(ArtifactKindUnsupportedError):
                await self._pipeline(FakeGenerator()).generate_artifact("paper-1", kind, "native")

        with self.assertRaises(EmptyArtifactError):
            await self._pipeline(FakeGenerator("  ")).generate_artifact(
                "paper-1", "explainer", "native"
            )
        with self.assertRaises(GenerationFailureError) as raised:
            await self._pipeline(
                FakeGenerator(failure=RuntimeError("provider-body TOP-SECRET"))
            ).generate_artifact("paper-1", "translation", "native")
        self.assertNotIn("TOP-SECRET", str(raised.exception))
        rendered = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertNotIn("TOP-SECRET", rendered)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual("legacy explainer", connection.execute("SELECT explainer FROM papers WHERE id='paper-1'").fetchone()[0])
            self.assertEqual("translation", connection.execute("SELECT content FROM translations WHERE paper_id='paper-1'").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM generated_artifacts WHERE status='ready'").fetchone()[0])

    async def test_injected_legacy_write_failure_rolls_back_new_artifact(self) -> None:
        class FailingUow(SqlAlchemyUnitOfWork):
            async def __aenter__(inner_self):
                await super(FailingUow, inner_self).__aenter__()

                async def fail(*_args, **_kwargs):
                    raise RuntimeError("legacy write failed")

                inner_self.artifacts.write_legacy_explainer = fail
                return inner_self

        pipeline = GenerationPipeline(
            lambda: FailingUow(self.session_factory),
            FakeSourcePipeline(self.source),
            FakeGenerator("new value"),
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )
        with self.assertRaisesRegex(RuntimeError, "legacy write failed"):
            await pipeline.generate_artifact("paper-1", "explainer", "native")
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM generated_artifacts").fetchone()[0])
            self.assertEqual("legacy explainer", connection.execute("SELECT explainer FROM papers WHERE id='paper-1'").fetchone()[0])

    async def test_reader_prefers_proven_ready_new_and_legacy_mode_never_fabricates_ids(self) -> None:
        reader_legacy = ArtifactReader(lambda: SqlAlchemyUnitOfWork(self.session_factory), "legacy")
        legacy = await reader_legacy.read("paper-1", "explainer")
        self.assertEqual("legacy", legacy.provenance)
        self.assertEqual("legacy explainer", legacy.content)
        self.assertIsNone(legacy.artifact_id)
        self.assertIsNone(legacy.source_document_id)

        generated = await self._pipeline(FakeGenerator("new explainer")).generate_artifact(
            "paper-1", "explainer", "native"
        )
        reader_new = ArtifactReader(lambda: SqlAlchemyUnitOfWork(self.session_factory), "prefer_new")
        preferred = await reader_new.read("paper-1", "explainer")
        self.assertEqual("new", preferred.provenance)
        self.assertEqual(generated.id, preferred.artifact_id)
        self.assertEqual(self.source.id, preferred.source_document_id)
        self.assertEqual("new explainer\n", preferred.content)

    async def test_failed_identity_retries_to_ready_and_mirror_is_post_commit_best_effort(self) -> None:
        with self.assertRaises(EmptyArtifactError):
            await self._pipeline(FakeGenerator(" ")).generate_artifact(
                "paper-1", "explainer", "native"
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            failed_id = connection.execute(
                "SELECT id FROM generated_artifacts WHERE status='failed'"
            ).fetchone()[0]

        mirror_observations: list[tuple[str, int]] = []

        def mirror(_kind: str, _paper_id: str, _content: str) -> None:
            with closing(sqlite3.connect(self.database_path)) as connection:
                ready_count = connection.execute(
                    "SELECT count(*) FROM generated_artifacts WHERE status='ready'"
                ).fetchone()[0]
            mirror_observations.append(("after-commit", ready_count))
            raise RuntimeError("best effort mirror failed")

        pipeline = GenerationPipeline(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            FakeSourcePipeline(self.source),
            FakeGenerator("retry success"),
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
            mirror=mirror,
        )
        ready = await pipeline.generate_artifact("paper-1", "explainer", "native")
        self.assertEqual(failed_id, ready.id)
        self.assertEqual([("after-commit", 1)], mirror_observations)
        never_called = FakeGenerator(failure=RuntimeError("late failure"))
        cached = await self._pipeline(never_called).generate_artifact(
            "paper-1", "explainer", "native"
        )
        self.assertEqual(ready, cached)
        self.assertEqual([], never_called.calls)

    async def test_reader_falls_back_for_stale_source_but_surfaces_new_table_errors(self) -> None:
        await self._pipeline(FakeGenerator("new explainer")).generate_artifact(
            "paper-1", "explainer", "native"
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE document_sources SET status='stale' WHERE id=?",
                (self.source.id,),
            )
            connection.commit()
        reader = ArtifactReader(lambda: SqlAlchemyUnitOfWork(self.session_factory), "prefer_new")
        fallback = await reader.read("paper-1", "explainer")
        self.assertEqual("legacy", fallback.provenance)
        self.assertIsNone(fallback.artifact_id)
        self.assertIsNone(fallback.source_document_id)

        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TABLE generated_artifacts")
            connection.commit()
        from backend.app.domain import PersistenceReadError

        with self.assertRaises(PersistenceReadError):
            await reader.read("paper-1", "explainer")


if __name__ == "__main__":
    unittest.main()
