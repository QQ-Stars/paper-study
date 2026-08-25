from __future__ import annotations

import io
from types import SimpleNamespace
import unittest
from unittest import mock

from agent import pipeline
from agent.models import PaperStub


class _Connection:
    def close(self) -> None:
        return None


class AgentPipelineFailureTests(unittest.TestCase):
    def test_all_classification_failures_are_not_empty_success(self) -> None:
        class Source:
            def search(self, _query: str, _years: object, _limit: int):
                yield PaperStub(
                    source="fixture",
                    source_id="fixture-1",
                    title="Fixture paper",
                    venue="arXiv",
                )

        with (
            mock.patch.object(pipeline, "SOURCES", {"fixture": Source}),
            mock.patch.object(pipeline.db, "connect", return_value=_Connection()),
            mock.patch.object(pipeline.db, "known_categories", return_value=([], [])),
            mock.patch.object(pipeline.db, "title_norm", return_value="fixturepaper"),
            mock.patch.object(pipeline.db, "exists", return_value=False),
            mock.patch.object(pipeline.db, "norm_venue", side_effect=lambda value: value),
            mock.patch.object(
                pipeline.llm,
                "classify",
                side_effect=RuntimeError("provider secret should not be rendered"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "所有候选论文分类失败"):
                pipeline.search("fixture", ["fixture"], (2024, 2026), 1)

    def test_candidate_import_failure_returns_nonzero_contract(self) -> None:
        candidate = {
            "source": "fixture",
            "source_id": "fixture-1",
            "title": "Fixture paper",
            "venue": "arXiv",
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(pipeline.db, "connect", return_value=_Connection()),
            mock.patch.object(pipeline.db, "known_categories", return_value=([], [])),
            mock.patch.object(pipeline.db, "title_norm", return_value="fixturepaper"),
            mock.patch.object(pipeline.db, "exists", return_value=False),
            mock.patch.object(
                pipeline.llm,
                "classify",
                return_value=SimpleNamespace(
                    type="检测",
                    topic="其他",
                    task=None,
                    models=[],
                    datasets=[],
                    contribution="",
                    tldr=None,
                    tags=[],
                    relevance=1.0,
                ),
            ),
            mock.patch.object(
                pipeline.db,
                "insert_paper",
                side_effect=RuntimeError("provider secret should not be rendered"),
            ),
            mock.patch.object(pipeline, "_p", side_effect=lambda message: stderr.write(f"{message}\n")),
        ):
            with self.assertRaisesRegex(RuntimeError, "候选导入失败"):
                pipeline.ingest_candidates([candidate], download_pdf=False)
        self.assertNotIn("provider secret", stderr.getvalue())

    def test_partial_candidate_import_returns_the_actual_added_count(self) -> None:
        candidates = [
            {
                "source": "fixture",
                "source_id": f"fixture-{index}",
                "title": f"Fixture paper {index}",
                "venue": "arXiv",
                "type": "method",
                "topic": "migration regression",
            }
            for index in (1, 2)
        ]
        stderr = io.StringIO()
        with (
            mock.patch.object(pipeline.db, "connect", return_value=_Connection()),
            mock.patch.object(pipeline.db, "known_categories", return_value=([], [])),
            mock.patch.object(
                pipeline.db,
                "title_norm",
                side_effect=("fixturepaper1", "fixturepaper2"),
            ),
            mock.patch.object(pipeline.db, "exists", return_value=False),
            mock.patch.object(
                pipeline.db,
                "insert_paper",
                side_effect=(None, RuntimeError("fixture insert failed")),
            ),
            mock.patch.object(
                pipeline,
                "_p",
                side_effect=lambda message: stderr.write(f"{message}\n"),
            ),
        ):
            added = pipeline.ingest_candidates(candidates, download_pdf=False)

        self.assertEqual(1, added)
        self.assertIn("INGESTED::1", stderr.getvalue())
        self.assertIn("SKIP::RuntimeError", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
