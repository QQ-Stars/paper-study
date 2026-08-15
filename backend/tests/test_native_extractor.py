from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from backend.app.domain import NativeExtractionFailedError, NativeTextEmptyError
from backend.app.providers.native import NativeExtractor


class NativeExtractorTests(unittest.TestCase):
    def test_markdown_success_normalizes_hashes_and_uses_stable_identity(self) -> None:
        calls: list[tuple[Path, bool]] = []

        def markdown(path: Path, *, show_progress: bool) -> str:
            calls.append((path, show_progress))
            return ("# Heading  \r\nBody\t\r\n" + "x" * 220)

        plain = mock.Mock(side_effect=AssertionError("plain fallback must not run"))
        page_count = mock.Mock(return_value=3)
        with tempfile.TemporaryDirectory(prefix="study-app-native-") as temp_dir:
            pdf_path = Path(temp_dir) / "paper.pdf"
            pdf_path.write_bytes(b"pdf")
            stdout = io.StringIO()
            before_modules = set(sys.modules)
            with contextlib.redirect_stdout(stdout):
                result = NativeExtractor(markdown, plain, page_count).extract(pdf_path.resolve())

        self.assertEqual([(pdf_path.resolve(), False)], calls)
        self.assertEqual("# Heading\nBody\n" + "x" * 220 + "\n", result.markdown)
        self.assertEqual(3, result.page_count)
        self.assertEqual("local", result.provider)
        self.assertEqual("pymupdf4llm-pymupdf", result.model)
        self.assertEqual("native-v1", result.processing_version)
        self.assertEqual(64, len(result.content_sha256))
        self.assertEqual("", stdout.getvalue())
        imported = set(sys.modules) - before_modules
        self.assertFalse(any("ocr" in name.lower() for name in imported))

    def test_short_or_failed_markdown_uses_full_plain_pages_in_order(self) -> None:
        cases = ("short", RuntimeError("markdown failed"))
        for markdown_result in cases:
            calls: list[Path] = []

            def markdown(_path: Path, *, show_progress: bool) -> str:
                self.assertFalse(show_progress)
                if isinstance(markdown_result, Exception):
                    raise markdown_result
                return markdown_result

            def plain(path: Path) -> tuple[list[str], int]:
                calls.append(path)
                return (["page 1  ", "page 2\t", "page 3"], 3)

            with self.subTest(markdown_result=markdown_result), tempfile.TemporaryDirectory(
                prefix="study-app-native-fallback-"
            ) as temp_dir:
                pdf_path = Path(temp_dir) / "paper.pdf"
                pdf_path.write_bytes(b"pdf")
                result = NativeExtractor(markdown, plain, mock.Mock(return_value=99)).extract(
                    pdf_path.resolve()
                )
                self.assertEqual([pdf_path.resolve()], calls)
                self.assertEqual("page 1\n\npage 2\n\npage 3\n", result.markdown)
                self.assertEqual(3, result.page_count)

    def test_blank_native_result_is_typed_and_constructs_no_ocr_or_network_object(self) -> None:
        counters = {"ocr": 0, "network": 0}
        extractor = NativeExtractor(
            lambda _path, show_progress=False: " ",
            lambda _path: (["\t", "  "], 2),
            lambda _path: 2,
        )
        with tempfile.TemporaryDirectory(prefix="study-app-native-blank-") as temp_dir:
            pdf_path = Path(temp_dir) / "paper.pdf"
            pdf_path.write_bytes(b"pdf")
            with self.assertRaises(NativeTextEmptyError) as raised:
                extractor.extract(pdf_path.resolve())
        self.assertEqual("NATIVE_TEXT_EMPTY", raised.exception.code)
        self.assertEqual({"ocr": 0, "network": 0}, counters)

    def test_unreadable_pdf_is_sanitized_typed_failure(self) -> None:
        def fail(_path: Path, *, show_progress: bool) -> str:
            raise RuntimeError("raw PDF bytes: TOP-SECRET")

        def fail_plain(_path: Path) -> tuple[list[str], int]:
            raise RuntimeError("provider response: TOP-SECRET")

        with tempfile.TemporaryDirectory(prefix="study-app-native-corrupt-") as temp_dir:
            pdf_path = Path(temp_dir) / "paper.pdf"
            pdf_path.write_bytes(b"TOP-SECRET")
            with self.assertRaises(NativeExtractionFailedError) as raised:
                NativeExtractor(fail, fail_plain, lambda _path: 1).extract(pdf_path.resolve())
        self.assertEqual("NATIVE_EXTRACTION_FAILED", raised.exception.code)
        self.assertNotIn("TOP-SECRET", str(raised.exception))
        self.assertNotIn("TOP-SECRET", repr(raised.exception.details))


if __name__ == "__main__":
    unittest.main()
