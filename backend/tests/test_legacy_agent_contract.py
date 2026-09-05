from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent import db, explain, extract, translate


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_PATH = REPOSITORY_ROOT / "agent" / "__main__.py"
REVIEWED_DISPATCH_SHA256 = "272044ca6ae61d737c34091074244426cf51a8db802f0e739cf01d5918b6b6b4"


class StubConnection:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def execute(self, _sql, _parameters=()):
        return SimpleNamespace(fetchone=lambda: self.row)

    def close(self):
        self.closed = True


class LegacyAgentContractTests(unittest.TestCase):
    def test_agent_dispatch_source_matches_reviewed_fingerprint(self) -> None:
        observed = hashlib.sha256(DISPATCH_PATH.read_bytes()).hexdigest()
        self.assertEqual(REVIEWED_DISPATCH_SHA256, observed)
        diff = subprocess.run(
            ["git", "diff", "--exit-code", "--", "agent/__main__.py"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, diff.returncode, diff.stdout + diff.stderr)

    def test_first_pages_freezes_markdown_fallback_abstract_and_cap(self) -> None:
        document = SimpleNamespace(page_count=12, close=mock.Mock())
        fitz = SimpleNamespace(open=mock.Mock(return_value=document))
        markdown = SimpleNamespace(to_markdown=mock.Mock(return_value="M" * 120))
        with mock.patch.dict(sys.modules, {"fitz": fitz, "pymupdf4llm": markdown}):
            self.assertEqual("M" * 120, extract.first_pages("paper.pdf", n=8))
        markdown.to_markdown.assert_called_once_with(
            "paper.pdf", pages=list(range(8)), show_progress=False
        )
        document.close.assert_called_once_with()

        short_markdown = SimpleNamespace(to_markdown=mock.Mock(return_value="short"))
        with (
            mock.patch.dict(sys.modules, {"fitz": fitz, "pymupdf4llm": short_markdown}),
            mock.patch.object(extract, "_plain_pages", return_value="plain fallback" * 20) as fallback,
        ):
            value = extract.first_pages("paper.pdf", n=3, abstract="Known abstract")
        fallback.assert_called_once_with("paper.pdf", 3)
        self.assertTrue(value.startswith("摘要:Known abstract\n\nplain fallback"))

        long_markdown = SimpleNamespace(to_markdown=mock.Mock(return_value="X" * 25000))
        with mock.patch.dict(sys.modules, {"fitz": fitz, "pymupdf4llm": long_markdown}):
            self.assertEqual(24000, len(extract.first_pages("paper.pdf")))

    def test_full_text_freezes_markdown_fallback_abstract_and_caller_cap(self) -> None:
        markdown = SimpleNamespace(to_markdown=mock.Mock(return_value="M" * 220))
        with (
            mock.patch.object(extract.config, "PDF_TEXT_PROVIDER", "default"),
            mock.patch.dict(sys.modules, {"pymupdf4llm": markdown}),
        ):
            self.assertEqual("M" * 220, extract.full_text("paper.pdf"))
        markdown.to_markdown.assert_called_once_with("paper.pdf", show_progress=False)

        short_markdown = SimpleNamespace(to_markdown=mock.Mock(return_value="short"))
        with (
            mock.patch.object(extract.config, "PDF_TEXT_PROVIDER", "default"),
            mock.patch.dict(sys.modules, {"pymupdf4llm": short_markdown}),
            mock.patch.object(extract, "_plain_pages", return_value="plain fallback" * 25) as fallback,
        ):
            value = extract.full_text(
                "paper.pdf", abstract="Known abstract", max_chars=180
            )
        fallback.assert_called_once_with("paper.pdf")
        self.assertEqual(180, len(value))
        self.assertTrue(value.startswith("摘要:Known abstract\n\nplain fallback"))

    def test_ocr_full_text_does_not_fall_back_to_native_extraction(self) -> None:
        native = SimpleNamespace(to_markdown=mock.Mock(return_value="N" * 500))
        with (
            mock.patch.object(extract.config, "PDF_TEXT_PROVIDER", "ocr"),
            mock.patch.object(
                extract,
                "_ocr_full_text",
                side_effect=RuntimeError("OCR transport failed"),
            ),
            mock.patch.object(extract, "_plain_pages", return_value="P" * 500) as plain,
            mock.patch.dict(sys.modules, {"pymupdf4llm": native}),
        ):
            with self.assertRaisesRegex(RuntimeError, "OCR transport failed"):
                extract.full_text("paper.pdf")
        native.to_markdown.assert_not_called()
        plain.assert_not_called()

    def test_ocr_failure_does_not_write_ocr_markdown(self) -> None:
        with (
            mock.patch.object(extract.config, "OCR_ENABLED", True),
            mock.patch.object(
                extract,
                "_ocr_settings",
                return_value={"base": "https://ocr.example/v1", "model": "vision", "dpi": 200, "batch": 1, "max_pages": 0},
            ),
            mock.patch.object(extract, "_ocr_page_images", return_value=["image"]),
            mock.patch.object(
                extract,
                "_ocr_transcribe",
                side_effect=RuntimeError("OCR transport failed"),
            ),
            mock.patch.object(db, "set_ocr_markdown") as write,
        ):
            with self.assertRaisesRegex(RuntimeError, "OCR transport failed"):
                extract._ocr_full_text("paper.pdf", paper_id="paper-1")
        write.assert_not_called()

    def test_ocr_disabled_is_reported_before_provider_configuration(self) -> None:
        with (
            mock.patch.object(extract.config, "OCR_ENABLED", False),
            mock.patch.object(extract, "_ocr_settings", side_effect=AssertionError("settings must not be read")),
        ):
            with self.assertRaisesRegex(RuntimeError, "OCR 已禁用"):
                extract._ocr_full_text("paper.pdf")

    def test_ocr_request_uses_saved_timeout_in_seconds(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "# OCR result"}}]}
        ).encode("utf-8")
        with (
            mock.patch.object(extract, "urllib") as urllib,
            mock.patch.object(extract.config, "OCR_TIMEOUT", 2500),
        ):
            urllib.request.urlopen.return_value = response
            value = extract._ocr_transcribe(["image"], {"base": "https://ocr.example/v1", "key": "", "model": "vision", "timeout": 2.5})
        self.assertEqual("# OCR result", value)
        self.assertEqual(2.5, urllib.request.urlopen.call_args.kwargs["timeout"])

    def test_translate_ocr_failure_does_not_use_local_pdf_text(self) -> None:
        row = {"id": "paper-1", "title": "Paper", "pdf_path": "paper.pdf"}
        connection = StubConnection(row)
        with (
            mock.patch.object(translate.config, "PDF_TEXT_PROVIDER", "ocr"),
            mock.patch.object(translate.db, "get_ocr_markdown", return_value=None),
            mock.patch.object(translate, "_find_pdf", return_value=Path("paper.pdf")),
            mock.patch.object(translate.extract, "page_count", return_value=1),
            mock.patch.object(
                translate.extract,
                "_ocr_full_text",
                side_effect=RuntimeError("OCR transport failed"),
            ),
            mock.patch.object(translate.extract, "full_text", return_value="local text") as native,
            mock.patch.object(translate.db, "set_translation") as write,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            mock.patch.object(translate.db, "connect", return_value=connection),
        ):
            with self.assertRaises(SystemExit) as raised:
                translate.translate_paper("paper-1")
        self.assertEqual(4, raised.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("OCRERR::OCR transport failed", stderr.getvalue())
        native.assert_not_called()
        write.assert_not_called()
        self.assertTrue(connection.closed)

    def test_deep_explain_ocr_failure_does_not_use_local_pdf_text(self) -> None:
        row = {"id": "paper-1", "title": "Paper", "authors": "[]", "pdf_path": "paper.pdf"}
        connection = StubConnection(row)
        with (
            mock.patch.object(explain.config, "PDF_TEXT_PROVIDER", "ocr"),
            mock.patch.object(explain.db, "get_ocr_markdown", return_value=None),
            mock.patch.object(explain, "_find_pdf", return_value=Path("paper.pdf")),
            mock.patch.object(
                explain.extract,
                "_ocr_full_text",
                side_effect=RuntimeError("OCR transport failed"),
            ),
            mock.patch.object(explain.extract, "full_text", return_value="local text") as native,
            mock.patch.object(explain.llm, "generate_explainer", return_value="# fallback") as generate,
            mock.patch.object(explain.db, "set_explainer") as write,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            mock.patch.object(explain.db, "connect", return_value=connection),
        ):
            with self.assertRaises(RuntimeError, msg="OCR failure must be visible to the caller"):
                explain.explain_paper("paper-1", deep=True)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("OCRERR::OCR transport failed", stderr.getvalue())
        native.assert_not_called()
        generate.assert_not_called()
        write.assert_not_called()
        self.assertTrue(connection.closed)

    def test_explain_freezes_missing_empty_and_successful_write_contracts(self) -> None:
        missing = StubConnection(None)
        with (
            mock.patch.object(explain.db, "connect", return_value=missing),
            mock.patch.object(explain.db, "set_explainer") as write,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as raised:
                explain.explain_paper("missing")
        self.assertEqual(2, raised.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("ERR::论文不存在: missing", stderr.getvalue())
        write.assert_not_called()
        self.assertTrue(missing.closed)

        row = {"id": "paper-1", "title": "Paper", "authors": "[]", "abstract": "A"}
        empty = StubConnection(row)
        with (
            mock.patch.object(explain.db, "connect", return_value=empty),
            mock.patch.object(explain.llm, "generate_explainer", return_value=""),
            mock.patch.object(explain.db, "set_explainer") as write,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            with self.assertRaises(SystemExit) as raised:
                explain.explain_paper("paper-1")
        self.assertEqual(3, raised.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("ERR::模型返回为空", stderr.getvalue())
        write.assert_not_called()
        self.assertTrue(empty.closed)

        success = StubConnection(row)
        with (
            mock.patch.object(explain.db, "connect", return_value=success),
            mock.patch.object(explain.llm, "generate_explainer", return_value="# output"),
            mock.patch.object(explain.db, "set_explainer") as write,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            result = explain.explain_paper("paper-1")
        self.assertEqual("# output", result)
        self.assertEqual("# output", stdout.getvalue())
        self.assertIn("DONE::8", stderr.getvalue())
        write.assert_called_once_with(success, "paper-1", "# output")
        self.assertTrue(success.closed)

    def test_explain_deep_mode_preserves_missing_pdf_metadata_fallback(self) -> None:
        row = {"id": "paper-1", "title": "Paper", "authors": "[]", "abstract": "A"}
        connection = StubConnection(row)
        with (
            mock.patch.object(explain.db, "connect", return_value=connection),
            mock.patch.object(explain, "_find_pdf", return_value=None),
            mock.patch.object(explain.config, "PDF_TEXT_PROVIDER", "default"),
            mock.patch.object(explain.llm, "generate_explainer", return_value="# metadata") as generate,
            mock.patch.object(explain.db, "set_explainer"),
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            explain.explain_paper("paper-1", deep=True)
        generate.assert_called_once()
        self.assertIsNone(generate.call_args.args[1])
        self.assertEqual("# metadata", stdout.getvalue())
        self.assertIn("PDFMISS::未找到本地PDF，改用摘要 / TLDR 生成", stderr.getvalue())

    def test_agent_process_keeps_results_on_stdout_and_progress_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-agent-contract-") as temp:
            temp_root = Path(temp)
            database_path = temp_root / "app.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    (REPOSITORY_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
                )
                connection.execute(
                    "INSERT INTO papers(id, source, title) VALUES(?, ?, ?)",
                    ("paper-1", "manual", "Process Contract"),
                )
                connection.commit()

            (temp_root / "sitecustomize.py").write_text(
                "from agent import llm\n"
                "llm.generate_explainer = lambda row, fulltext=None: '# subprocess markdown'\n"
                "llm.expand_queries = lambda query, count=6: ['one', 'two']\n",
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "DB_PATH": str(database_path),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    [str(temp_root), str(REPOSITORY_ROOT), os.environ.get("PYTHONPATH", "")]
                ),
            }
            explained = subprocess.run(
                [sys.executable, "-m", "agent", "explain", "--id", "paper-1"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, explained.returncode, explained.stderr)
            self.assertEqual("# subprocess markdown", explained.stdout)
            self.assertIn("STAGE::generate::", explained.stderr)
            self.assertIn("DONE::21", explained.stderr)
            self.assertNotIn("STAGE::", explained.stdout)
            with closing(sqlite3.connect(database_path)) as connection:
                persisted = connection.execute(
                    "SELECT explainer FROM papers WHERE id=?", ("paper-1",)
                ).fetchone()[0]
            self.assertEqual("# subprocess markdown", persisted)

            expanded = subprocess.run(
                [sys.executable, "-m", "agent", "expand", "--query", "q", "--expand-n", "2"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, expanded.returncode, expanded.stderr)
            self.assertEqual(["one", "two"], json.loads(expanded.stdout))
            self.assertEqual("", expanded.stderr)


if __name__ == "__main__":
    unittest.main()
