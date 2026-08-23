import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent import extract, translate


class StubConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.closed = False

    def execute(self, _sql: str, _parameters: object = ()) -> SimpleNamespace:
        return SimpleNamespace(fetchone=lambda: self.row)

    def close(self) -> None:
        self.closed = True


class StripReferencesTests(unittest.TestCase):
    def test_keeps_same_level_appendix_after_references(self) -> None:
        markdown = (
            "# Main Results\n\n"
            "The main body.\n\n"
            "## References\n\n"
            "[1] A reference that must not be translated.\n\n"
            "## A APPENDIX\n\n"
            "The appendix must still be translated."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertIn("# Main Results", result)
        self.assertNotIn("A reference that must not be translated", result)
        self.assertIn("## A APPENDIX", result)
        self.assertIn("The appendix must still be translated", result)

    def test_keeps_appendix_when_tail_heading_has_no_markdown_level(self) -> None:
        markdown = (
            "Introduction\n\n"
            "References\n\n"
            "[1] Omitted reference\n\n"
            "Appendix B - Extra Evaluation\n\n"
            "Additional evaluation details."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertNotIn("Omitted reference", result)
        self.assertIn("Appendix B - Extra Evaluation", result)
        self.assertIn("Additional evaluation details", result)

    def test_still_truncates_references_when_they_are_last(self) -> None:
        markdown = (
            "# Main Results\n\n"
            "The main body.\n\n"
            "# References\n\n"
            "[1] The only trailing reference."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertEqual("# Main Results\n\nThe main body.", result)

    def test_keeps_latex_letter_appendix_after_references(self) -> None:
        """截图场景：OCR 输出用 LaTeX 式单字母附录标题（# A <标题>），不含 appendix 关键词。"""
        markdown = (
            "# Conclusion\n\n"
            "The body ends here.\n\n"
            "# References\n\n"
            "[1] Nikhil Kandpal, Haikang Deng. Large language models struggle.\n\n"
            "[2] Stephanie Lin, Jacob Hilton. TruthfulQA.\n\n"
            "# A Precision-recall tradeoffs with weaker models\n\n"
            "(b) ROC curves for individual models.\n\n"
            "Figure 4: (a) The entropy distributions show a clearer separation."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertIn("# Conclusion", result)
        self.assertNotIn("Nikhil Kandpal", result)
        self.assertNotIn("Stephanie Lin", result)
        self.assertIn("# A Precision-recall tradeoffs with weaker models", result)
        self.assertIn("Figure 4: (a) The entropy distributions", result)

    def test_keeps_deeper_appendix_heading_after_references(self) -> None:
        """附录标题层级比 References 深时也应恢复（旧实现要求同级/更高才恢复）。"""
        markdown = (
            "# Main\n\nBody.\n\n"
            "# References\n\n[1] Trailing ref.\n\n"
            "## Appendix A Extra Tables\n\nTable 5 results."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertNotIn("Trailing ref", result)
        self.assertIn("## Appendix A Extra Tables", result)
        self.assertIn("Table 5 results", result)

    def test_resumes_at_same_level_non_appendix_heading(self) -> None:
        """参考文献后的同级非附录标题（如伦理声明）也属后续正文，应保留。"""
        markdown = (
            "## Experiments\n\nBody.\n\n"
            "## References\n\n[1] Omitted.\n\n"
            "## Ethics Statement\n\nWe respect privacy."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertNotIn("Omitted", result)
        self.assertIn("## Ethics Statement", result)
        self.assertIn("We respect privacy", result)

    def test_acknowledgments_then_references_both_stripped(self) -> None:
        """Acknowledgments 与 References 连续出现时两者都跳，附录仍保留。"""
        markdown = (
            "# Main\n\nBody.\n\n"
            "# Acknowledgments\n\nWe thank the reviewers.\n\n"
            "# References\n\n[1] Omitted ref.\n\n"
            "# A Extra analysis\n\nKeep this."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertIn("# Main", result)
        self.assertNotIn("We thank the reviewers", result)
        self.assertNotIn("Omitted ref", result)
        self.assertIn("# A Extra analysis", result)
        self.assertIn("Keep this", result)

    def test_reference_entries_with_author_initials_stay_stripped(self) -> None:
        """防护：纯文本参考文献条目以作者缩写「A. Author」开头，不得被单字母规则当作附录恢复。"""
        markdown = (
            "# Main\n\nBody.\n\n"
            "References\n\n"
            "A. Author, B. Writer. Some paper title. 2020.\n\n"
            "C. Researcher. Another paper. 2021."
        )

        result, stripped = extract.strip_references(markdown)

        self.assertTrue(stripped)
        self.assertNotIn("Some paper title", result)
        self.assertNotIn("Another paper", result)

    def test_full_translation_sends_appendix_but_not_references_to_the_model(self) -> None:
        markdown = (
            "# Main Results\n\n"
            "The main body.\n\n"
            "## References\n\n"
            "[1] A reference that must not be translated.\n\n"
            "## A APPENDIX\n\n"
            "Appendix content that must be translated."
        )
        connection = StubConnection({"id": "paper-1", "title": "Paper"})
        translated_inputs: list[str] = []

        def translate_markdown(value: str) -> str:
            translated_inputs.append(value)
            return "# 已翻译"

        with (
            mock.patch.object(translate.db, "connect", return_value=connection),
            mock.patch.object(translate, "_find_pdf", return_value=Path("paper.pdf")),
            mock.patch.object(translate.extract, "page_count", return_value=1),
            mock.patch.object(translate.extract, "full_text", return_value=markdown),
            mock.patch.object(translate.llm, "translate_md", side_effect=translate_markdown),
            mock.patch.object(translate.db, "set_translation") as write,
            mock.patch.object(translate.config, "PDF_TEXT_PROVIDER", "default"),
            mock.patch.object(translate.config, "TRANSLATE_SKIP_REFERENCES", True),
            mock.patch.object(translate.config, "TRANSLATE_MODE", "full"),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = translate.translate_paper("paper-1", workers=1)

        self.assertEqual("# 已翻译", result)
        self.assertEqual(1, len(translated_inputs))
        self.assertNotIn("A reference that must not be translated", translated_inputs[0])
        self.assertIn("## A APPENDIX", translated_inputs[0])
        self.assertIn("Appendix content that must be translated", translated_inputs[0])
        write.assert_called_once_with(connection, "paper-1", "# 已翻译")
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
