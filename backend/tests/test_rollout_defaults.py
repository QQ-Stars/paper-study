from __future__ import annotations

import dataclasses
import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.app.rollout import (
    RolloutConfigurationError,
    assert_shadow_request_allowed,
    is_shadow_read_only,
    load_rollout_settings,
    parse_rollout_settings,
    rollout_to_environment,
)
from backend.tests.support.p2_database import P2_TEST_PROCESSING_CURSOR_SECRET


LEGACY_ENVIRONMENT = {
    "API_BACKEND_MODE": "legacy",
    "DOCUMENT_PIPELINE_MODE": "legacy",
    "GENERATION_PIPELINE_MODE": "legacy",
    "ARTIFACT_READ_MODE": "legacy",
    "ARTIFACT_WRITE_MODE": "legacy",
    "OCR_ENABLED": "0",
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class RolloutDefaultsTests(unittest.TestCase):
    def test_candidate_settings_composition_uses_frozen_obsidian_rollout(self) -> None:
        from backend.app.application.settings import SettingsService
        from backend.app.cli.candidate_runtime import _rollout
        from backend.tests.test_obsidian_settings import _CredentialService

        environment = {"OBSIDIAN_ENABLED": "1"}
        rollout = _rollout(environment)
        environment["OBSIDIAN_ENABLED"] = "0"
        with tempfile.TemporaryDirectory(prefix="study-app-p5-frozen-rollout-") as temp:
            root = Path(temp)
            service = SettingsService(
                settings_path=root / "settings.json",
                root=root,
                credential_service=_CredentialService(),
                environment_snapshot=environment,
                rollout_snapshot=rollout,
            )
            effective = asyncio.run(service.obsidian())

        self.assertTrue(rollout.obsidian_enabled)
        self.assertTrue(effective.enabled)
        with self.assertRaises(RolloutConfigurationError):
            _rollout({"OBSIDIAN_ENABLED": "true"})

    def test_absent_variables_produce_frozen_p0_defaults(self) -> None:
        settings = load_rollout_settings({})
        self.assertEqual(LEGACY_ENVIRONMENT, rollout_to_environment(settings))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.api_backend_mode = "python"
        self.assertEqual("legacy", settings.api_backend_mode)

    def test_invalid_values_are_rejected_without_normalization(self) -> None:
        for variable in LEGACY_ENVIRONMENT:
            for value in ("", " legacy", "legacy ", "LEGACY", "unknown"):
                with self.subTest(variable=variable, value=value):
                    with self.assertRaises(RolloutConfigurationError) as raised:
                        load_rollout_settings({variable: value})
                    self.assertEqual("INVALID_ROLLOUT_VALUE", raised.exception.code)
                    self.assertEqual(variable, raised.exception.variable)

    def test_processing_cursor_secret_is_frozen_at_startup_and_not_projected(self) -> None:
        environment = {
            "PROCESSING_CURSOR_SECRET": P2_TEST_PROCESSING_CURSOR_SECRET
        }

        settings = parse_rollout_settings(environment)
        environment["PROCESSING_CURSOR_SECRET"] = "changed-after-startup"

        self.assertEqual(
            P2_TEST_PROCESSING_CURSOR_SECRET,
            settings.processing_cursor_secret,
        )
        self.assertNotIn("PROCESSING_CURSOR_SECRET", rollout_to_environment(settings))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.processing_cursor_secret = "changed"

    def test_unavailable_adapters_fail_and_shadow_is_read_only(self) -> None:
        unavailable = (
            ("API_BACKEND_MODE", "shadow"),
            ("API_BACKEND_MODE", "python"),
            ("DOCUMENT_PIPELINE_MODE", "p1"),
            ("GENERATION_PIPELINE_MODE", "p1"),
            ("ARTIFACT_READ_MODE", "prefer_new"),
            ("ARTIFACT_WRITE_MODE", "dual"),
            ("OCR_ENABLED", "1"),
        )
        for variable, value in unavailable:
            with self.subTest(variable=variable, value=value):
                with self.assertRaises(RolloutConfigurationError) as raised:
                    load_rollout_settings({variable: value})
                self.assertEqual("ROLLOUT_ADAPTER_UNAVAILABLE", raised.exception.code)
                self.assertEqual(variable, raised.exception.variable)

        shadow = parse_rollout_settings({"API_BACKEND_MODE": "shadow"})
        self.assertTrue(is_shadow_read_only(shadow))
        assert_shadow_request_allowed(shadow, "GET")
        with self.assertRaises(RolloutConfigurationError) as raised:
            assert_shadow_request_allowed(shadow, "POST")
        self.assertEqual("SHADOW_MUTATION_FORBIDDEN", raised.exception.code)

    def test_node_and_python_effective_values_match(self) -> None:
        cases = (
            {},
            {
                "API_BACKEND_MODE": "shadow",
                "DOCUMENT_PIPELINE_MODE": "p1",
                "GENERATION_PIPELINE_MODE": "p1",
                "ARTIFACT_READ_MODE": "prefer_new",
                "ARTIFACT_WRITE_MODE": "dual",
                "OCR_ENABLED": "1",
            },
        )
        script = (
            "const r=require('./lib/backend-rollout');"
            "const e=JSON.parse(process.argv[1]);"
            "process.stdout.write(JSON.stringify(r.rolloutToEnvironment(r.parseBackendRollout(e))));"
        )
        for environment in cases:
            with self.subTest(environment=environment):
                node = subprocess.run(
                    ["node", "-e", script, json.dumps(environment)],
                    cwd=REPOSITORY_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                self.assertEqual(0, node.returncode, node.stderr)
                python_effective = rollout_to_environment(
                    parse_rollout_settings(environment)
                )
                self.assertEqual(python_effective, json.loads(node.stdout))

    def test_P5_rollout_extends_the_frozen_P0_1_inventory_only_with_obsidian_enabled(
        self,
    ) -> None:
        self.assertEqual(
            LEGACY_ENVIRONMENT,
            rollout_to_environment(
                parse_rollout_settings({"OBSIDIAN_ENABLED": "1"})
            ),
        )

        environment = {"OBSIDIAN_ENABLED": "1"}
        settings = parse_rollout_settings(environment, vocabulary="p5")
        environment["OBSIDIAN_ENABLED"] = "0"
        expected = {**LEGACY_ENVIRONMENT, "OBSIDIAN_ENABLED": "1"}

        self.assertTrue(settings.obsidian_enabled)
        self.assertEqual(
            expected,
            rollout_to_environment(settings, vocabulary="p5"),
        )
        self.assertEqual(
            {**LEGACY_ENVIRONMENT, "OBSIDIAN_ENABLED": "0"},
            rollout_to_environment(
                parse_rollout_settings({}, vocabulary="p5"),
                vocabulary="p5",
            ),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.obsidian_enabled = False

        for value in ("", "true", "yes", "2", " 1"):
            with self.subTest(value=value):
                with self.assertRaises(RolloutConfigurationError) as raised:
                    parse_rollout_settings(
                        {"OBSIDIAN_ENABLED": value}, vocabulary="p5"
                    )
                self.assertEqual("INVALID_ROLLOUT_VALUE", raised.exception.code)
                self.assertEqual("OBSIDIAN_ENABLED", raised.exception.variable)

        script = (
            "const r=require('./lib/backend-rollout');"
            "const s=r.parseBackendRollout({OBSIDIAN_ENABLED:'1'},'p5');"
            "process.stdout.write(JSON.stringify(r.rolloutToEnvironment(s,'p5')));"
        )
        node = subprocess.run(
            ["node", "-e", script],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, node.returncode, node.stderr)
        self.assertEqual(expected, json.loads(node.stdout))


if __name__ == "__main__":
    unittest.main()
