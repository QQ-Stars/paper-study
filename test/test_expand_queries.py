import json
import unittest
from types import SimpleNamespace
from unittest import mock

from agent import llm, pipeline


class ExpandQueriesTests(unittest.TestCase):
    def tearDown(self):
        llm._client = None
        llm._client_signature = None

    def test_provider_failure_returns_six_english_local_queries(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=mock.Mock(side_effect=RuntimeError("network down"))
                )
            )
        )
        with mock.patch.object(llm, "client", return_value=client):
            queries = llm.expand_queries("多模态大模型幻觉检测与缓解", 6)

        self.assertEqual(6, len(queries))
        for query in queries:
            self.assertNotRegex(query, r"[\u3400-\u9fff]")
            self.assertGreaterEqual(len(query.split()), 2)
            self.assertLessEqual(len(query.split()), 6)

    def test_invalid_model_queries_are_replaced_by_local_english_queries(self):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps({"queries": ["中文检索词"]}, ensure_ascii=False)
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=mock.Mock(return_value=response))
            )
        )
        with mock.patch.object(llm, "client", return_value=client):
            queries = llm.expand_queries("多模态大模型幻觉检测与缓解", 6)

        self.assertEqual(6, len(queries))
        self.assertTrue(all(not any("\u3400" <= char <= "\u9fff" for char in query) for query in queries))


class PipelineSearchFailureTests(unittest.TestCase):
    def test_all_source_failures_are_not_reported_as_empty_success(self):
        class BrokenSource:
            def search(self, _query, _years, _limit):
                raise OSError("network blocked")

        with mock.patch.object(pipeline, "SOURCES", {"broken": BrokenSource}):
            with self.assertRaisesRegex(RuntimeError, "所有检索数据源均不可用"):
                pipeline.search("research", ["broken"], "2024-2026", 1)


if __name__ == "__main__":
    unittest.main()
