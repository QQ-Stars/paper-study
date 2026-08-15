from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
import unittest


SHA_A = "a" * 64
SHA_B = "b" * 64
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


class P1DomainTests(unittest.TestCase):
    def test_seven_domain_terms_are_standard_library_only(self) -> None:
        forbidden = {
            "sqlalchemy",
            "aiosqlite",
            "fastapi",
            "agent.config",
            "anthropic",
            "httpx",
            "openai",
        }
        before = set(sys.modules)
        domain = importlib.import_module("backend.app.domain")
        imported = set(sys.modules) - before
        for module_name in forbidden:
            self.assertFalse(
                any(name == module_name or name.startswith(f"{module_name}.") for name in imported),
                module_name,
            )
        for term in (
            "Paper",
            "SourceDocument",
            "GeneratedArtifact",
            "ProcessingJob",
            "VaultProjection",
            "ProviderProfile",
            "Credential",
        ):
            self.assertTrue(hasattr(domain, term), term)

        domain_root = Path(domain.__file__).parent
        source = "\n".join(path.read_text(encoding="utf-8") for path in domain_root.glob("*.py"))
        for package in forbidden:
            self.assertNotIn(f"import {package}", source.lower())
            self.assertNotIn(f"from {package}", source.lower())

    def test_source_modes_statuses_and_artifact_kinds_are_exact(self) -> None:
        from backend.app.domain import (
            ArtifactKind,
            ProcessingJobStatus,
            SourceDocumentStatus,
            SourceMode,
        )

        self.assertEqual(["native", "ocr"], [value.value for value in SourceMode])
        self.assertEqual(
            ["queued", "running", "ready", "failed", "stale", "cancelled"],
            [value.value for value in SourceDocumentStatus],
        )
        self.assertEqual(
            ["queued", "running", "succeeded", "failed", "cancelled"],
            [value.value for value in ProcessingJobStatus],
        )
        self.assertEqual(
            [
                "explainer",
                "translation",
                "summary",
                "outline",
                "study_card",
                "classification",
                "metadata",
            ],
            [value.value for value in ArtifactKind],
        )
        with self.assertRaises(ValueError):
            SourceMode("native ")
        with self.assertRaises(ValueError):
            ArtifactKind("notes")

    def test_source_document_invariants_hashes_counts_and_utc_normalization(self) -> None:
        from backend.app.domain import SourceDocument

        offset_time = datetime(2026, 8, 9, 20, 0, tzinfo=timezone(timedelta(hours=8)))
        ready = SourceDocument(
            id="src_1",
            paper_id=" Paper-ID\t",
            mode="native",
            status="ready",
            provider="local",
            model="pymupdf4llm-pymupdf",
            pdf_sha256=SHA_A,
            options_hash=SHA_B,
            processing_version="native-v1",
            created_at=offset_time,
            updated_at=offset_time,
            markdown="# Canonical\n",
            content_sha256=SHA_A,
            page_count=0,
        )
        self.assertEqual(" Paper-ID\t", ready.paper_id)
        self.assertEqual(NOW, ready.created_at)
        self.assertEqual("native", ready.mode.value)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ready.status = "stale"

        invalid_values = (
            {"status": "ready", "markdown": "", "content_sha256": SHA_A},
            {"status": "ready", "markdown": "content", "content_sha256": None},
            {"status": "failed", "error_code": ""},
            {"status": "queued", "pdf_sha256": SHA_A.upper()},
            {"status": "queued", "page_count": -1},
        )
        for overrides in invalid_values:
            values = {
                "id": "src_bad",
                "paper_id": "paper-1",
                "mode": "native",
                "status": "queued",
                "provider": "local",
                "model": "model",
                "pdf_sha256": SHA_A,
                "options_hash": SHA_B,
                "processing_version": "v1",
                "created_at": NOW,
                "updated_at": NOW,
                **overrides,
            }
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                SourceDocument(**values)

        naive_values = {**dataclasses.asdict(ready), "created_at": datetime(2026, 8, 9)}
        with self.assertRaises(ValueError):
            SourceDocument(**naive_values)

    def test_generated_artifact_requires_proven_identity_and_terminal_payloads(self) -> None:
        from backend.app.domain import GeneratedArtifact

        base = {
            "id": "art_1",
            "paper_id": "paper-1",
            "kind": "explainer",
            "source_document_id": "src_1",
            "status": "queued",
            "generator_provider": "provider",
            "generator_model": "model",
            "prompt_version": "prompt-v1",
            "created_at": NOW,
            "updated_at": NOW,
        }
        ready = GeneratedArtifact(
            **{**base, "status": "ready"},
            content="artifact",
            content_sha256=SHA_A,
        )
        self.assertEqual("src_1", ready.source_document_id)

        invalid_values = (
            {"status": "ready", "content": "", "content_sha256": SHA_A},
            {"status": "ready", "content": "artifact", "content_sha256": None},
            {"status": "failed", "error_code": ""},
            {"status": "queued", "source_document_id": ""},
            {"status": "queued", "generator_model": ""},
            {"status": "queued", "content_sha256": SHA_A.upper()},
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                GeneratedArtifact(**{**base, **overrides})

    def test_processing_job_scope_pairings_statuses_and_attempts(self) -> None:
        from backend.app.domain import ProcessingJob

        base = {
            "id": "job_1",
            "status": "queued",
            "idempotency_key": "once",
            "created_at": NOW,
        }
        accepted = (
            {"job_type": "source_materialize", "paper_id": "paper-1", "source_mode": "native"},
            {"job_type": "ocr", "paper_id": "paper-1", "source_mode": "ocr"},
            {"job_type": "explain", "paper_id": "paper-1", "source_mode": "native"},
            {"job_type": "translate", "paper_id": "paper-1", "source_mode": "native"},
            {"job_type": "embed", "paper_id": "paper-1", "source_mode": "native"},
            {"job_type": "obsidian_export", "paper_id": "paper-1", "source_mode": None},
            {"job_type": "obsidian_sync", "paper_id": None, "source_mode": None},
        )
        for index, values in enumerate(accepted):
            with self.subTest(values=values):
                job = ProcessingJob(
                    **{
                        **base,
                        "id": f"job_{index}",
                        "idempotency_key": f"once-{index}",
                        **values,
                    }
                )
                self.assertEqual(values["paper_id"], job.paper_id)

        rejected = (
            {"job_type": "source_materialize", "paper_id": "paper-1", "source_mode": "ocr"},
            {"job_type": "ocr", "paper_id": "paper-1", "source_mode": "native"},
            {"job_type": "explain", "paper_id": None, "source_mode": "native"},
            {"job_type": "translate", "paper_id": "paper-1", "source_mode": None},
            {"job_type": "obsidian_export", "paper_id": None, "source_mode": None},
        )
        for values in rejected:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ProcessingJob(**base, **values)
        for field, value in (("attempt", -1), ("max_attempts", 0)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ProcessingJob(
                    **base,
                    job_type="obsidian_sync",
                    **{field: value},
                )

    def test_chunk_and_projection_counts_pages_and_hashes_are_validated(self) -> None:
        from backend.app.domain import DocumentChunk, VaultProjection

        chunk = DocumentChunk(
            id="chk_1",
            source_document_id="src_1",
            sequence=0,
            heading_path=None,
            page_start=0,
            page_end=1,
            content="content",
            content_sha256=SHA_A,
            token_count=0,
        )
        self.assertEqual(0, chunk.sequence)
        with self.assertRaises(ValueError):
            dataclasses.replace(chunk, sequence=-1)
        with self.assertRaises(ValueError):
            dataclasses.replace(chunk, token_count=-1)
        with self.assertRaises(ValueError):
            dataclasses.replace(chunk, page_end=-1)
        with self.assertRaises(ValueError):
            dataclasses.replace(chunk, page_start=2, page_end=1)
        with self.assertRaises(ValueError):
            dataclasses.replace(chunk, content_sha256=SHA_A.upper())

        projection = VaultProjection(
            id="exp_1",
            paper_id="paper-1",
            artifact_id=None,
            target_path="papers/paper-1.md",
            source_hash=SHA_A,
            exported_hash=None,
            status="pending",
        )
        self.assertEqual("paper-1", projection.paper_id)
        with self.assertRaises(ValueError):
            dataclasses.replace(projection, source_hash="not-a-sha")

    def test_credentials_profiles_and_statuses_never_expose_secrets(self) -> None:
        from backend.app.domain import Credential, CredentialKind, CredentialStatus, ProviderProfile

        self.assertEqual(
            ["llm", "ocr", "embedding", "semantic_scholar"],
            [kind.value for kind in CredentialKind],
        )
        profile = ProviderProfile(provider="openai-compatible", model="model", base_url=None)
        self.assertEqual(
            {"provider", "model", "base_url"},
            {field.name for field in dataclasses.fields(profile)},
        )
        credential = Credential(CredentialKind.LLM, "top-secret-value")
        self.assertNotIn("top-secret-value", repr(credential))
        self.assertNotIn("top-secret-value", f"{credential}")
        try:
            raise RuntimeError(f"credential rejected: {credential}")
        except RuntimeError as error:
            self.assertNotIn("top-secret-value", str(error))

        status = CredentialStatus(
            kind=CredentialKind.LLM,
            has_key=True,
            key_tail="****alue",
            environment_managed=False,
        )
        self.assertEqual(
            {
                "kind": "llm",
                "hasKey": True,
                "keyTail": "****alue",
                "environmentManaged": False,
            },
            status.to_dict(),
        )

    def test_typed_errors_have_stable_sanitized_public_format(self) -> None:
        from backend.app.domain import (
            ArtifactKindUnsupportedError,
            EmptyArtifactError,
            EmptySourceError,
            ExtractionFailureError,
            GenerationFailureError,
            InvalidSourceModeError,
            MissingPaperError,
            MissingPdfError,
            OcrUnavailableError,
            PersistenceConflictError,
            SchemaRevisionMismatchError,
            StaleSourceError,
        )

        error_types = (
            MissingPaperError,
            MissingPdfError,
            InvalidSourceModeError,
            OcrUnavailableError,
            ExtractionFailureError,
            EmptySourceError,
            GenerationFailureError,
            EmptyArtifactError,
            ArtifactKindUnsupportedError,
            StaleSourceError,
            PersistenceConflictError,
            SchemaRevisionMismatchError,
        )
        codes = set()
        for error_type in error_types:
            error = error_type(
                paper_id="paper-1",
                provider_body="raw-provider-secret",
                credential="secret-key",
            )
            with self.subTest(error_type=error_type):
                self.assertTrue(error.code)
                self.assertTrue(error.public_message)
                self.assertNotIn("raw-provider-secret", str(error))
                self.assertNotIn("secret-key", str(error))
                self.assertNotIn("provider_body", error.details)
                self.assertNotIn("credential", error.details)
                codes.add(error.code)
        self.assertEqual(len(error_types), len(codes))


if __name__ == "__main__":
    unittest.main()
