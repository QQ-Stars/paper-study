from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.app.application.obsidian_pdf import ObsidianPdfProjector
from backend.app.application.obsidian_projection import ObsidianProjectionError
from backend.app.infrastructure.bound_vault_root import BoundVaultRoot, VaultRelativePath
from backend.app.providers.obsidian_vault import parse_manifest
from backend.app.providers.pdf_files import PdfFiles


class _RecordingPdfFiles(PdfFiles):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.resolve_calls = 0
        self.open_calls = 0
        self.materialize_calls = 0
        self.enqueue_calls = 0
        self.ocr_calls = 0

    def resolve_for_id(self, paper_id: str, *, stored_path=None):
        self.resolve_calls += 1
        return super().resolve_for_id(paper_id, stored_path=stored_path)

    def open_for_id(self, paper_id: str, *, stored_path=None):
        self.open_calls += 1
        return super().open_for_id(paper_id, stored_path=stored_path)

    def materialize(self, *args: object, **kwargs: object) -> None:
        self.materialize_calls += 1
        raise AssertionError("PDF export must not materialize a source document")

    def enqueue(self, *args: object, **kwargs: object) -> None:
        self.enqueue_calls += 1
        raise AssertionError("PDF export must not enqueue source processing")

    def ocr(self, *args: object, **kwargs: object) -> None:
        self.ocr_calls += 1
        raise AssertionError("PDF export must not invoke OCR")


class _ProjectionRepository:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def find_by_target_path(self, target_path: str):
        return self.rows.get(target_path)

    async def upsert(self, projection):
        self.rows[projection.target_path] = projection
        return projection


class ObsidianPdfModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_reference_copy_have_distinct_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "PDF Files"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-1.pdf"
            source_bytes = b"%PDF-1.7\nsource-pdf-tail\n%%EOF\n"
            source.write_bytes(source_bytes)
            vault = base / "vault"
            vault.mkdir()

            pdf_files = _RecordingPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )
            repository = _ProjectionRepository()
            with BoundVaultRoot.open(vault) as bound_root:
                projector = ObsidianPdfProjector(
                    pdf_files=pdf_files,
                    root=bound_root,
                    repository=repository,
                    root_folder="Research",
                )

                none = await projector.project(
                    paper_id="paper-1",
                    mode="none",
                    stored_path=source,
                )
                self.assertEqual("none", none.status)
                self.assertIsNone(none.link)
                self.assertIsNone(none.projection_file)
                self.assertEqual((0, 0), (pdf_files.resolve_calls, pdf_files.open_calls))

                reference = await projector.project(
                    paper_id="paper-1",
                    mode="reference",
                    stored_path=source,
                )
                self.assertEqual("reference", reference.status)
                self.assertEqual(source.resolve().as_uri(), reference.link)
                self.assertIn("PDF%20Files", reference.link)
                self.assertIsNone(reference.projection_file)
                self.assertEqual((1, 0), (pdf_files.resolve_calls, pdf_files.open_calls))

                copied = await projector.project(
                    paper_id="paper-1",
                    mode="copy",
                    stored_path=source,
                )

            self.assertEqual("exported", copied.status)
            self.assertEqual(
                "../Attachments/PDF/paper-1.pdf",
                copied.link,
            )
            self.assertEqual(
                "Attachments/PDF/paper-1.pdf",
                copied.projection_file.path,
            )
            self.assertEqual(source_bytes, copied.projection_file.data)
            self.assertEqual((1, 1), (pdf_files.resolve_calls, pdf_files.open_calls))
            self.assertEqual(
                source_bytes,
                (vault / "Research" / "Attachments" / "PDF" / "paper-1.pdf").read_bytes(),
            )

            manifest = parse_manifest(
                (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
            )
            self.assertEqual(1, len(manifest.entries))
            entry = manifest.entries[0]
            self.assertEqual("pdf-copy", entry.kind)
            self.assertEqual("paper-1", entry.paper_id)
            self.assertEqual("managed", entry.ownership)
            self.assertEqual("Attachments/PDF/paper-1.pdf", entry.path)
            ledger = repository.rows[entry.path]
            self.assertEqual("exported", ledger.status)
            self.assertEqual(entry.exported_hash, ledger.exported_hash)

    async def test_copy_rejects_pdf_that_no_longer_matches_frozen_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-1.pdf"
            frozen = b"%PDF-1.7\nfrozen\n%%EOF\n"
            source.write_bytes(frozen)
            expected_sha256 = hashlib.sha256(frozen).hexdigest()
            source.write_bytes(b"%PDF-1.7\nchanged-after-enqueue\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            repository = _ProjectionRepository()

            with BoundVaultRoot.open(vault) as bound_root:
                projector = ObsidianPdfProjector(
                    pdf_files=_RecordingPdfFiles(
                        root=base,
                        default_directory=pdf_directory,
                    ),
                    root=bound_root,
                    repository=repository,
                    root_folder="Research",
                )
                result = await projector.project(
                    paper_id="paper-1",
                    mode="copy",
                    stored_path=source,
                    expected_sha256=expected_sha256,
                )

            self.assertEqual("conflict", result.status)
            self.assertEqual("OBSIDIAN_PDF_SOURCE_CHANGED", result.error_code)
            self.assertIsNone(result.projection_file)
            self.assertEqual({}, repository.rows)
            self.assertFalse((vault / "Research").exists())

            source.write_bytes(frozen)
            with BoundVaultRoot.open(vault) as bound_root:
                projector = ObsidianPdfProjector(
                    pdf_files=_RecordingPdfFiles(
                        root=base,
                        default_directory=pdf_directory,
                    ),
                    root=bound_root,
                    repository=repository,
                    root_folder="Research",
                )
                copied = await projector.project(
                    paper_id="paper-1",
                    mode="copy",
                    stored_path=source,
                    expected_sha256=expected_sha256,
                )

            self.assertEqual("exported", copied.status)
            self.assertEqual(b"", copied.projection_file.data)
            self.assertEqual(
                frozen,
                (vault / "Research" / "Attachments" / "PDF" / "paper-1.pdf").read_bytes(),
            )

    async def test_export_never_materializes_missing_source_or_calls_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-only-pdf.pdf"
            source.write_bytes(b"%PDF-1.7\nonly-pdf\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            pdf_files = _RecordingPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )

            with BoundVaultRoot.open(vault) as bound_root:
                projector = ObsidianPdfProjector(
                    pdf_files=pdf_files,
                    root=bound_root,
                    repository=_ProjectionRepository(),
                    root_folder="Research",
                )
                outputs = [
                    await projector.project(
                        paper_id="paper-only-pdf",
                        mode=mode,
                        stored_path=source,
                        source_available=False,
                    )
                    for mode in ("none", "reference", "copy")
                ]

            self.assertEqual(
                [("source_unavailable",)] * 3,
                [output.issues for output in outputs],
            )
            self.assertEqual(
                (0, 0, 0),
                (
                    pdf_files.materialize_calls,
                    pdf_files.enqueue_calls,
                    pdf_files.ocr_calls,
                ),
            )

    async def test_copy_uses_validated_raw_paper_id_and_never_updates_pdf_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "renamed-source.pdf"
            source_bytes = b"%PDF-1.7\nidentity-source\n%%EOF\n"
            source.write_bytes(source_bytes)
            collision_source = pdf_directory / "collision-source.pdf"
            collision_source.write_bytes(b"%PDF-1.7\ncollision\n%%EOF\n")
            conflict_source = pdf_directory / "conflict-source.pdf"
            conflict_source.write_bytes(b"%PDF-1.7\nconflict-source\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            paper_rows = {
                "Raw.ID-7": {"pdf_path": str(source)},
                "missing-paper": {"pdf_path": "data/pdfs/missing.pdf"},
                "conflict-paper": {"pdf_path": str(conflict_source)},
            }
            original_paths = {
                paper_id: row["pdf_path"] for paper_id, row in paper_rows.items()
            }
            pdf_files = _RecordingPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )
            repository = _ProjectionRepository()

            with BoundVaultRoot.open(vault) as bound_root:
                bound_root.publish_new(
                    VaultRelativePath(
                        "Research/Attachments/PDF/conflict-paper.pdf"
                    ),
                    b"user-owned-pdf",
                )
                projector = ObsidianPdfProjector(
                    pdf_files=pdf_files,
                    root=bound_root,
                    repository=repository,
                    root_folder="Research",
                )
                success = await projector.project(
                    paper_id="Raw.ID-7",
                    mode="copy",
                    stored_path=paper_rows["Raw.ID-7"]["pdf_path"],
                )
                self.assertEqual(
                    "Attachments/PDF/Raw.ID-7.pdf",
                    success.projection_file.path,
                )

                calls_before_rejection = pdf_files.open_calls
                with self.assertRaises(ObsidianProjectionError) as unsafe:
                    await projector.project(
                        paper_id="../unsafe",
                        mode="copy",
                        stored_path=source,
                    )
                self.assertEqual("OBSIDIAN_PAPER_ID_UNSAFE", unsafe.exception.code)
                self.assertEqual(calls_before_rejection, pdf_files.open_calls)

                with self.assertRaises(ObsidianProjectionError) as collision:
                    await projector.project(
                        paper_id="Case-ID",
                        mode="copy",
                        stored_path=collision_source,
                        existing_paths=("Papers/case-id.md",),
                    )
                self.assertEqual(
                    "OBSIDIAN_PAPER_ID_CASE_COLLISION",
                    collision.exception.code,
                )
                self.assertEqual(calls_before_rejection, pdf_files.open_calls)

                missing = await projector.project(
                    paper_id="missing-paper",
                    mode="copy",
                    stored_path=paper_rows["missing-paper"]["pdf_path"],
                )
                conflict = await projector.project(
                    paper_id="conflict-paper",
                    mode="copy",
                    stored_path=paper_rows["conflict-paper"]["pdf_path"],
                )

            self.assertEqual("pdf_missing", missing.status)
            self.assertEqual("conflict", conflict.status)
            self.assertEqual(
                original_paths,
                {paper_id: row["pdf_path"] for paper_id, row in paper_rows.items()},
            )
            self.assertEqual(source_bytes, source.read_bytes())
            self.assertEqual(
                b"%PDF-1.7\nconflict-source\n%%EOF\n",
                conflict_source.read_bytes(),
            )
            self.assertEqual(
                source_bytes,
                (
                    vault
                    / "Research"
                    / "Attachments"
                    / "PDF"
                    / "Raw.ID-7.pdf"
                ).read_bytes(),
            )
            self.assertEqual(
                b"user-owned-pdf",
                (
                    vault
                    / "Research"
                    / "Attachments"
                    / "PDF"
                    / "conflict-paper.pdf"
                ).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
