from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


TOOL_NAMES = {
    "get_explainer",
    "get_paper",
    "get_translation",
    "library_overview",
    "list_categories",
    "list_due_reviews",
    "related_papers",
    "search_papers",
    "semantic_search",
}
FIXTURES = {"normal", "empty", "error"}


def _complete_window() -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    for tool in sorted(TOOL_NAMES):
        for fixture in sorted(FIXTURES):
            diffs: list[dict[str, object]] = []
            if tool == "get_paper" and fixture == "normal":
                diffs.append(
                    {
                        "path": "$.sourceDocument",
                        "category": "approved_additive_optional",
                        "legacyHash": None,
                        "applicationHash": "a" * 64,
                    }
                )
            observations.append(
                {
                    "tool": tool,
                    "fixture": fixture,
                    "diffs": diffs,
                    "sourceIdentity": "source-identity",
                    "buildIdentity": "build-identity",
                }
            )
    return observations


class McpShadowTests(unittest.TestCase):
    def test_mode_is_strict_and_defaults_to_legacy(self) -> None:
        from agent import mcp_server
        from backend.tests.test_mcp_contract import mcp_fixture_database

        self.assertEqual("legacy", mcp_server.parse_mcp_mode(None))
        for mode in ("legacy", "shadow", "application"):
            self.assertEqual(mode, mcp_server.parse_mcp_mode(mode))
        for invalid in ("", "LEGACY", " legacy", "application ", "unknown"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    mcp_server.parse_mcp_mode(invalid)

        class FakeApplication:
            def get_explainer(
                self,
                id: str,
                offset: int = 0,
                max_chars: int = 12000,
            ) -> dict[str, object]:
                return {
                    "ok": True,
                    "id": id,
                    "content": "APPLICATION",
                    "offset": offset,
                    "next_offset": None,
                    "total_chars": 11,
                    "truncated": False,
                }

        fake = FakeApplication()
        with (
            patch.object(mcp_server, "MCP_MODE", "application"),
            patch.object(mcp_server, "_application_adapter", return_value=fake),
        ):
            self.assertEqual(
                "APPLICATION", mcp_server.get_explainer("p1")["content"]
            )

        with mcp_fixture_database(), tempfile.TemporaryDirectory(
            prefix="study-app-mcp-entry-"
        ) as directory:
            environment = {
                "PAPER_STUDY_MCP_CONFIG_DIR": directory,
                "PAPER_STUDY_MCP_SHADOW_PATH": str(Path(directory) / "shadow.jsonl"),
                "PAPER_STUDY_MCP_SOURCE_IDENTITY": "source-id",
                "PAPER_STUDY_MCP_BUILD_IDENTITY": "build-id",
            }
            with (
                patch.object(mcp_server, "MCP_MODE", "shadow"),
                patch.object(mcp_server, "_application_adapter", return_value=fake),
                patch.dict(os.environ, environment, clear=False),
            ):
                result = mcp_server.get_explainer("p1")
            self.assertEqual("LEGACY-EXPLAINER-尾", result["content"])
            observation = json.loads(
                (Path(directory) / "shadow.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual("get_explainer", observation["tool"])

    def test_shadow_returns_legacy_and_records_canonical_diff(self) -> None:
        from backend.app.api.compat.mcp_shadow import ShadowRecorder, shadow_call

        legacy = {
            "ok": True,
            "title": "legacy-title",
            "abstract": "BODY-SECRET api-key-123",
        }
        application = {
            "ok": True,
            "abstract": "DIFFERENT-BODY-SECRET api-key-456",
            "sourceDocument": {
                "native": {
                    "currentId": "source-1",
                    "status": "ready",
                    "updatedAt": "2026-08-15T00:00:00Z",
                    "error": None,
                },
                "ocr": None,
            },
        }
        with tempfile.TemporaryDirectory(prefix="study-app-mcp-shadow-") as directory:
            root = Path(directory)
            path = root / "observations.jsonl"
            recorder = ShadowRecorder(path, allowed_root=root)
            returned = shadow_call(
                tool="get_paper",
                fixture="normal",
                legacy_call=lambda: legacy,
                application_call=lambda: application,
                recorder=recorder,
                source_identity="source-id",
                build_identity="build-id",
            )
            raw = path.read_text(encoding="utf-8")
            observation = json.loads(raw)

        self.assertEqual(legacy, returned)
        self.assertEqual("get_paper", observation["tool"])
        self.assertEqual("$.sourceDocument", observation["diffs"][-1]["path"])
        self.assertEqual(
            "approved_additive_optional",
            observation["diffs"][-1]["category"],
        )
        categories = {item["category"] for item in observation["diffs"]}
        self.assertIn("missing_in_application", categories)
        self.assertIn("value_mismatch", categories)
        for item in observation["diffs"]:
            self.assertIn("legacyHash", item)
            self.assertIn("applicationHash", item)
        self.assertNotIn("BODY-SECRET", raw)
        self.assertNotIn("api-key", raw)
        self.assertNotIn("legacy-title", raw)

    def test_application_switch_requires_complete_zero_diff_window(self) -> None:
        from backend.app.api.compat.mcp_shadow import evaluate_shadow_window

        evidence = {
            "readOnly": True,
            "zeroEnqueue": True,
            "zeroOcr": True,
            "sourceIdentity": "source-identity",
            "buildIdentity": "build-identity",
        }
        complete = _complete_window()
        result = evaluate_shadow_window(
            complete,
            evidence=evidence,
            required_tools=TOOL_NAMES,
            required_fixtures=FIXTURES,
        )
        self.assertTrue(result.ok)
        self.assertIsNone(result.code)

        cases: dict[str, tuple[list[dict[str, object]], dict[str, object]]] = {
            "missing coverage": (complete[1:], evidence),
            "unexplained diff": (
                complete
                + [
                    {
                        "tool": "search_papers",
                        "fixture": "normal",
                        "diffs": [{"path": "$.count", "category": "value_mismatch"}],
                        "sourceIdentity": "source-identity",
                        "buildIdentity": "build-identity",
                    }
                ],
                evidence,
            ),
            "wrong identity": (
                [dict(complete[0], sourceIdentity="wrong"), *complete[1:]],
                evidence,
            ),
            "read-only evidence missing": (complete, dict(evidence, readOnly=False)),
            "approved addition missing": (
                [
                    dict(item, diffs=[])
                    if item["tool"] == "get_paper" and item["fixture"] == "normal"
                    else item
                    for item in complete
                ],
                evidence,
            ),
        }
        for name, (window, candidate_evidence) in cases.items():
            with self.subTest(name=name):
                rejected = evaluate_shadow_window(
                    window,
                    evidence=candidate_evidence,
                    required_tools=TOOL_NAMES,
                    required_fixtures=FIXTURES,
                )
                self.assertFalse(rejected.ok)
                self.assertEqual("MCP_SHADOW_NOT_CONVERGED", rejected.code)
                self.assertTrue(rejected.reasons)


if __name__ == "__main__":
    unittest.main()
