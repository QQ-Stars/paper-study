from __future__ import annotations

from pathlib import Path
import hashlib
import json
import tempfile
import unittest


class SuiteIsolationTests(unittest.TestCase):
    def test_suite_sandbox_denies_live_paths_network_providers_and_reports_zero_access(self) -> None:
        from backend.app.api.compat.suite_isolation import (
            SuiteIsolationError,
            create_suite_isolation,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p6-isolation-") as raw:
            root = Path(raw).resolve()
            run_root = root / ("run-" + "a" * 32)
            run_root.mkdir()
            run_manifest = run_root / "evidence-run-manifest-v1.json"
            run_manifest.write_text('{"manifestKind":"evidence-run"}', encoding="utf-8")
            run_sha = hashlib.sha256(run_manifest.read_bytes()).hexdigest()
            live_db = root / "live" / "app.db"
            live_settings = root / "live" / "settings.json"
            live_pdf = root / "live" / "pdfs"
            live_vault = root / "live" / "vault"
            live_keyring = root / "live" / "keyring"
            live_db.parent.mkdir()
            live_db.write_bytes(b"live")
            live_settings.write_text("{}", encoding="utf-8")
            for directory in (live_pdf, live_vault, live_keyring):
                directory.mkdir()

            first = create_suite_isolation(
                run_manifest=run_manifest,
                expected_run_manifest_sha256=run_sha,
                suite_key="backend-suite",
                output=run_root / "backend-suite.isolation.json",
                deny_live_paths=(live_db, live_settings, live_pdf, live_vault, live_keyring),
                deny_network=True,
                deny_providers=True,
            )
            second = create_suite_isolation(
                run_manifest=run_manifest,
                expected_run_manifest_sha256=run_sha,
                suite_key="node-suite",
                output=run_root / "node-suite.isolation.json",
                deny_live_paths=(live_db, live_settings, live_pdf, live_vault, live_keyring),
                deny_network=True,
                deny_providers=True,
            )
            self.assertNotEqual(first.sandbox_root, second.sandbox_root)
            self.assertEqual(0, first.live_access_count)
            self.assertEqual(0, json.loads(first.manifest_path.read_text(encoding="utf-8"))["liveAccessCount"])
            for path in (first.database_path, first.settings_path, first.pdf_root, first.vault_root, first.keyring_root):
                self.assertTrue(path.exists())
                self.assertTrue(path.is_relative_to(first.sandbox_root))

            for operation in (
                lambda: first.guard_path(live_db),
                lambda: first.guard_sqlite(live_db),
                first.guard_network,
                first.guard_provider,
            ):
                with self.assertRaises(SuiteIsolationError) as denied:
                    operation()
                self.assertEqual("SUITE_LIVE_ACCESS_DENIED", denied.exception.code)


if __name__ == "__main__":
    unittest.main()
