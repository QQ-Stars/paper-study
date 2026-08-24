from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from backend.app.application.search_coordinator import SearchCoordinator


class SearchCoordinatorExpandTests(unittest.TestCase):
    def test_expand_reports_nonzero_agent_exit_instead_of_success(self) -> None:
        class Agent:
            async def run(self, command, args):
                self.call = (command, args)
                return SimpleNamespace(
                    returncode=3,
                    stdout='["stale"]',
                    stderr="ERROR::模型连接失败",
                )

        agent = Agent()
        result = asyncio.run(
            SearchCoordinator(agent, lambda: None).expand(
                {"query": "hallucination", "expandN": 6}
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual([], result["queries"])
        self.assertEqual("模型连接失败", result["error"])
        self.assertEqual(("expand", ("--query", "hallucination", "--expand-n", "6")), agent.call)

    def test_expand_preserves_explicit_local_fallback_metadata(self) -> None:
        class Agent:
            async def run(self, command, args):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            "multimodal hallucination detection",
                            "vision language model factuality",
                        ]
                    ),
                    stderr="EXPAND_FALLBACK::模型连接失败，已使用本地扩展词",
                )

        result = asyncio.run(
            SearchCoordinator(Agent(), lambda: None).expand(
                {"query": "多模态幻觉", "expandN": 6}
            )
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["fallback"])
        self.assertEqual("模型连接失败，已使用本地扩展词", result["warning"])
        self.assertEqual(2, len(result["queries"]))


if __name__ == "__main__":
    unittest.main()
