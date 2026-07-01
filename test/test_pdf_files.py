import tempfile
import unittest
from pathlib import Path

from agent import pdf_files


class PdfFilesTest(unittest.TestCase):
    def test_title_pdf_filename_sanitizes_titles_and_reserved_names(self):
        self.assertEqual(
            pdf_files.title_pdf_filename("Chain-of-Verification: Reduces Hallucination? / Test*"),
            "Chain-of-Verification Reduces Hallucination Test.pdf",
        )
        self.assertEqual(pdf_files.title_pdf_filename("   ...   ", fallback="paper-id"), "paper-id.pdf")
        self.assertEqual(pdf_files.title_pdf_filename("CON"), "_CON.pdf")

    def test_unique_pdf_path_uses_stable_id_suffix_on_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_dir = Path(tmp)
            (pdf_dir / "A Study.pdf").write_bytes(b"existing")

            self.assertEqual(
                pdf_files.unique_pdf_path(pdf_dir, "A Study", paper_id="2024.12345"),
                pdf_dir / "A Study - 2024.12345.pdf",
            )

    def test_archive_pdf_copies_external_files_and_moves_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_dir = root / "data" / "pdfs"
            external = root / "outside" / "source.pdf"
            local = pdf_dir / "old-id.pdf"
            external.parent.mkdir(parents=True)
            pdf_dir.mkdir(parents=True)
            external.write_bytes(b"external")
            local.write_bytes(b"local")

            copied = pdf_files.archive_pdf(external, "External Paper", "external-id", pdf_dir)
            moved = pdf_files.archive_pdf(local, "Local Paper", "local-id", pdf_dir)

            self.assertEqual(copied, pdf_dir / "External Paper.pdf")
            self.assertTrue(external.exists())
            self.assertEqual(copied.read_bytes(), b"external")
            self.assertEqual(moved, pdf_dir / "Local Paper.pdf")
            self.assertFalse(local.exists())
            self.assertEqual(moved.read_bytes(), b"local")


if __name__ == "__main__":
    unittest.main()
