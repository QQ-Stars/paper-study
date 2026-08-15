from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.app.application.ports import ExtractedSource
from backend.app.application.source_documents import DocumentSourcePipeline
from backend.app.config import DatabaseSettings
from backend.app.domain import (
    MissingPaperError,
    MissingPdfError,
    NativeTextEmptyError,
    OcrUnavailableError,
    SourcePdfChangedError,
)
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.tests.support.p1_database import create_legacy_database, run_alembic


NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


class FakeExtractor:
    provider = "local"
    model = "pymupdf4llm-pymupdf"
    processing_version = "native-v1"

    def __init__(self, callback=None) -> None:
        self.calls = 0
        self.callback = callback

    def extract(self, path: Path) -> ExtractedSource:
        self.calls += 1
        if self.callback is not None:
            return self.callback(path)
        markdown = "# source\n"
        return ExtractedSource(
            markdown=markdown,
            content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            page_count=1,
            provider=self.provider,
            model=self.model,
            processing_version=self.processing_version,
        )


class DocumentSourcePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-source-pipeline-")
        self.root = Path(self._temp.name)
        self.database_path = self.root / "legacy" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260807_02")
        self.pdf_path = self.root / "paper.pdf"
        self.pdf_path.write_bytes(b"pdf-v1")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE papers SET pdf_path=? WHERE id='paper-1'",
                (str(self.pdf_path),),
            )
            connection.commit()
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.engine = self.session_factory.kw["bind"]
        self.ids = iter((f"src_{value:032x}" for value in range(1, 20)))

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp.cleanup()

    def _pipeline(self, extractor: FakeExtractor) -> DocumentSourcePipeline:
        return DocumentSourcePipeline(
            lambda: SqlAlchemyUnitOfWork(self.session_factory),
            extractor,
            clock=lambda: NOW,
            id_factory=lambda: next(self.ids),
        )

    async def test_ready_cache_identity_ignores_purpose_and_avoids_second_extraction(self) -> None:
        extractor = FakeExtractor()
        pipeline = self._pipeline(extractor)
        first = await pipeline.materialize_source("paper-1", "native", "explain")
        second = await pipeline.materialize_source("paper-1", "native", "translate")
        self.assertEqual(first, second)
        self.assertEqual(1, extractor.calls)
        self.assertEqual(hashlib.sha256(b"pdf-v1").hexdigest(), first.pdf_sha256)
        self.assertEqual(
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
            first.options_hash,
        )
        self.assertEqual(NOW, first.created_at)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM document_sources").fetchone()[0])

    async def test_validation_missing_inputs_and_ocr_fail_before_extraction_or_write(self) -> None:
        extractor = FakeExtractor()
        pipeline = self._pipeline(extractor)
        with self.assertRaises(ValueError):
            await pipeline.materialize_source("paper-1", "native", " ")
        with self.assertRaises(MissingPaperError):
            await pipeline.materialize_source("missing", "native", "test")
        with self.assertRaises(OcrUnavailableError):
            await pipeline.materialize_source("paper-1", "ocr", "test")
        self.pdf_path.unlink()
        with self.assertRaises(MissingPdfError):
            await pipeline.materialize_source("paper-1", "native", "test")
        self.assertEqual(0, extractor.calls)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT count(*) FROM document_sources").fetchone()[0])

    async def test_changed_pdf_or_processing_version_creates_distinct_identity(self) -> None:
        first_extractor = FakeExtractor()
        first = await self._pipeline(first_extractor).materialize_source("paper-1", "native", "one")
        self.pdf_path.write_bytes(b"pdf-v2")
        second = await self._pipeline(first_extractor).materialize_source("paper-1", "native", "two")
        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.pdf_sha256, second.pdf_sha256)

        versioned = FakeExtractor()
        versioned.processing_version = "native-v2"
        third = await self._pipeline(versioned).materialize_source("paper-1", "native", "three")
        self.assertNotEqual(second.id, third.id)
        self.assertEqual("native-v2", third.processing_version)

    async def test_file_change_during_extraction_and_empty_source_never_publish_ready(self) -> None:
        def mutate(path: Path) -> ExtractedSource:
            path.write_bytes(b"changed-during-extraction")
            return FakeExtractor().extract(path)

        with self.assertRaises(SourcePdfChangedError):
            await self._pipeline(FakeExtractor(mutate)).materialize_source(
                "paper-1", "native", "race"
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                0,
                connection.execute("SELECT count(*) FROM document_sources WHERE status='ready'").fetchone()[0],
            )

        self.pdf_path.write_bytes(b"stable-again")

        def empty(_path: Path) -> ExtractedSource:
            raise NativeTextEmptyError()

        with self.assertRaises(NativeTextEmptyError):
            await self._pipeline(FakeExtractor(empty)).materialize_source(
                "paper-1", "native", "empty"
            )
        with closing(sqlite3.connect(self.database_path)) as connection:
            statuses = connection.execute(
                "SELECT status,error_code FROM document_sources ORDER BY created_at,id"
            ).fetchall()
        self.assertTrue(statuses)
        self.assertTrue(all(status != "ready" for status, _code in statuses))

    async def test_concurrent_identical_materializations_return_one_winner(self) -> None:
        extractor = FakeExtractor()
        pipeline = self._pipeline(extractor)
        first, second = await asyncio.gather(
            pipeline.materialize_source("paper-1", "native", "one"),
            pipeline.materialize_source("paper-1", "native", "two"),
        )
        self.assertEqual(first, second)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM document_sources").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
