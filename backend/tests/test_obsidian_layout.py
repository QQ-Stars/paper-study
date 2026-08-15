from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.application.obsidian_projection import (
    ExportOptions,
    ObsidianProjectionError,
    ProjectionArtifact,
    ProjectionSnapshot,
    build_projection_plan,
    build_projection_plans,
    project_paths,
)


GOLDEN = Path(__file__).parent / "fixtures" / "obsidian" / "golden"


def _snapshot(**overrides: object) -> ProjectionSnapshot:
    values: dict[str, object] = {
        "paper_id": "paper-01",
        "title": "A Deterministic Paper",
        "title_zh": "一篇确定性论文",
        "authors": "Ada Lovelace; Alan Turing",
        "aliases": ("Deterministic Paper", "确定性论文"),
        "tags": ("research", "yaml:safe", "研究", "research"),
        "paper_source_hash": "1" * 64,
        "source_markdown": "# Full Source\n\nSource tail sentinel.\n",
        "source_hash": "2" * 64,
        "explainer": ProjectionArtifact(
            artifact_id="explainer-01",
            markdown="# Full Explainer\n\nExplainer tail sentinel.\n",
            source_hash="3" * 64,
        ),
        "translation": ProjectionArtifact(
            artifact_id="translation-01",
            markdown="# 完整翻译\n\nTranslation tail sentinel.\n",
            source_hash="4" * 64,
        ),
        "note_markdown": "A durable personal note.\n",
        "note_source_hash": "5" * 64,
    }
    values.update(overrides)
    return ProjectionSnapshot(**values)


class ObsidianLayoutTests(unittest.TestCase):
    def test_projection_matches_golden_layout(self) -> None:
        plan = build_projection_plan(_snapshot(), ExportOptions())

        expected_paths = {
            "paper": "Papers/paper-01.md",
            "source": "Sources/paper-01.md",
            "explainer": "Explainers/paper-01.md",
            "translation": "Translations/paper-01.md",
            "note": "Notes/paper-01.md",
        }
        self.assertEqual({item.kind: item.path for item in plan.files}, expected_paths)

        for item in plan.files:
            self.assertEqual(item.data, (GOLDEN / f"{item.kind}.md").read_bytes())
            self.assertNotIn("A Deterministic Paper", item.path)
            self.assertTrue(item.data.endswith(b"\n"))
            self.assertFalse(item.data.endswith(b"\n\n"))

    def test_rejects_unsafe_paper_ids_without_partial_projection(self) -> None:
        unsafe_ids = (
            "",
            ".",
            "..",
            "/absolute",
            "C:\\absolute",
            "\\\\server\\share",
            "paper/id",
            "paper\\id",
            "paper:id",
            "paper\nid",
            "paper\x00id",
            "论文",
            "paper🙂",
            "CON",
            "con.md",
            "_starts-with-symbol",
            "-starts-with-symbol",
            ".starts-with-symbol",
            "paper.",
            "paper ",
            "a" * 181,
        )

        for paper_id in unsafe_ids:
            with self.subTest(paper_id=paper_id):
                with self.assertRaises(ObsidianProjectionError) as caught:
                    build_projection_plans((_snapshot(paper_id=paper_id),), ExportOptions())
                self.assertEqual(caught.exception.code, "OBSIDIAN_PAPER_ID_UNSAFE")

        self.assertEqual(
            project_paths("A.valid_ID-9").paper,
            "Papers/A.valid_ID-9.md",
        )

    def test_rejects_casefold_id_collisions_before_projection(self) -> None:
        snapshots = (
            _snapshot(paper_id="Paper-1"),
            _snapshot(paper_id="paper-1"),
        )

        with self.assertRaises(ObsidianProjectionError) as caught:
            build_projection_plans(snapshots, ExportOptions())

        self.assertEqual(caught.exception.code, "OBSIDIAN_PAPER_ID_CASE_COLLISION")

    def test_paper_template_links_placeholders_and_yaml_lists_are_canonical(self) -> None:
        snapshot = _snapshot(
            title="Title: !unsafe\ncontinued",
            authors="One: Author\nTwo Authors",
            aliases=(),
            tags=(),
            source_markdown=None,
            source_hash=None,
        )

        first = build_projection_plan(
            snapshot,
            ExportOptions(export_source=True, export_explainer=False, export_translation=True),
        )
        second = build_projection_plan(
            snapshot,
            ExportOptions(export_source=True, export_explainer=False, export_translation=True),
        )
        files = {item.kind: item for item in first.files}

        self.assertEqual(first, second)
        self.assertEqual(set(files), {"paper", "translation", "note"})
        paper = files["paper"].data.decode("utf-8")
        self.assertIn('title: "Title: !unsafe\\ncontinued"\n', paper)
        self.assertIn('authors: "One: Author\\nTwo Authors"\n', paper)
        self.assertIn("aliases: []\n", paper)
        self.assertIn("tags: []\n", paper)
        self.assertIn("## Source\n\n*Source unavailable.*", paper)
        self.assertIn("## Explainer\n\n*Explainer unavailable.*", paper)
        self.assertIn("[Open translation](../Translations/paper-01.md)", paper)
        self.assertIn("[Open notes](../Notes/paper-01.md)", paper)

        tagged = build_projection_plan(
            _snapshot(
                aliases=("Alias: one", "Alias\ntwo"),
                tags=("研究", "yaml: safe", "alpha", "!tag", "alpha", "line\nbreak"),
            ),
            ExportOptions(),
        )
        tagged_paper = tagged.files[0].data.decode("utf-8")
        self.assertIn(
            'aliases:\n  - "Alias: one"\n  - "Alias\\ntwo"\n'
            'tags:\n  - "!tag"\n  - "alpha"\n  - "line\\nbreak"\n'
            '  - "yaml: safe"\n  - "研究"\n',
            tagged_paper,
        )

    def test_title_change_keeps_all_paths_and_only_changes_managed_bytes(self) -> None:
        original = build_projection_plan(_snapshot(), ExportOptions())
        renamed = build_projection_plan(
            _snapshot(title="Renamed Paper", title_zh="重命名论文"),
            ExportOptions(),
        )
        original_files = {item.kind: item for item in original.files}
        renamed_files = {item.kind: item for item in renamed.files}

        self.assertEqual(
            {kind: item.path for kind, item in original_files.items()},
            {kind: item.path for kind, item in renamed_files.items()},
        )
        for kind in ("paper", "source", "explainer", "translation"):
            self.assertNotEqual(original_files[kind].data, renamed_files[kind].data)
        self.assertEqual(original_files["note"].data, renamed_files["note"].data)


if __name__ == "__main__":
    unittest.main()
