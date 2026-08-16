from __future__ import annotations

import json
import hashlib
from pathlib import Path
import tempfile
import unittest


class _NamedTest:
    def __init__(self, test_id: str) -> None:
        self._test_id = test_id

    def id(self) -> str:
        return self._test_id

class NativeWindowsSuiteApplicabilityTests(unittest.TestCase):
    def test_selection_excludes_only_container_and_unavailable_symlink_cases(self) -> None:
        from backend.app.api.compat.suite_applicability import (
            NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
            NATIVE_WINDOWS_SYMLINK_TEST_IDS,
            SymlinkCapability,
            select_native_windows_tests,
        )

        keep_id = "test_api_health.ApiHealthTests.test_health"
        tests = [
            _NamedTest(test_id)
            for test_id in (
                *NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
                *NATIVE_WINDOWS_SYMLINK_TEST_IDS.values(),
                keep_id,
            )
        ]
        selection = select_native_windows_tests(
            tests,
            symlink_capabilities=(
                SymlinkCapability(kind="directory", available=False, win_error=1314),
                SymlinkCapability(kind="file", available=False, win_error=1314),
            ),
        )

        self.assertEqual((keep_id,), selection.selected_test_ids)
        self.assertEqual(
            set(NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS)
            | set(NATIVE_WINDOWS_SYMLINK_TEST_IDS.values()),
            {item.test_id for item in selection.excluded_tests},
        )
        self.assertEqual(
            {
                "container-deployment-only",
                "windows-directory-symlink-privilege-unavailable",
                "windows-file-symlink-privilege-unavailable",
            },
            {item.reason_code for item in selection.excluded_tests},
        )

    def test_selection_fails_when_a_frozen_policy_test_id_is_missing(self) -> None:
        from backend.app.api.compat.suite_applicability import (
            NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
            NATIVE_WINDOWS_SYMLINK_TEST_IDS,
            SuiteApplicabilityError,
            SymlinkCapability,
            select_native_windows_tests,
        )

        tests = [
            _NamedTest(test_id)
            for test_id in (
                *NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS[:-1],
                *NATIVE_WINDOWS_SYMLINK_TEST_IDS.values(),
            )
        ]
        with self.assertRaises(SuiteApplicabilityError) as caught:
            select_native_windows_tests(
                tests,
                symlink_capabilities=(
                    SymlinkCapability(kind="directory", available=True, win_error=None),
                    SymlinkCapability(kind="file", available=True, win_error=None),
                ),
            )

        self.assertEqual("SUITE_APPLICABILITY_POLICY_STALE", caught.exception.code)

    def test_loader_rejects_an_extra_exclusion_or_unapproved_symlink_error(self) -> None:
        from backend.app.api.compat.build_identity import BuildIdentityManifest
        from backend.app.api.compat.suite_applicability import (
            NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
            NATIVE_WINDOWS_SYMLINK_TEST_IDS,
            SuiteApplicabilityError,
            SymlinkCapability,
            create_suite_applicability_report,
            load_suite_applicability_report,
            select_native_windows_tests,
        )

        keep_id = "test_api_health.ApiHealthTests.test_health"
        tests = [
            _NamedTest(test_id)
            for test_id in (
                *NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
                *NATIVE_WINDOWS_SYMLINK_TEST_IDS.values(),
                keep_id,
            )
        ]
        selection = select_native_windows_tests(
            tests,
            symlink_capabilities=(
                SymlinkCapability(kind="directory", available=False, win_error=1314),
                SymlinkCapability(kind="file", available=False, win_error=1314),
            ),
        )

        with tempfile.TemporaryDirectory(prefix="study-app-native-suite-") as raw:
            root = Path(raw)
            build_path = root / f"frozen-build-identity-{'a' * 64}.json"
            build_path.write_bytes(b"{}")
            build = BuildIdentityManifest(
                build_id="a" * 64,
                manifest_path=build_path.resolve(),
                manifest_file_sha256="b" * 64,
                git_revision="c" * 40,
                dirty=False,
                source_tree_hash="d" * 64,
                build_artifact_hash="e" * 64,
                deployment_kind="native-windows",
                canonical_bytes=b"{}",
            )
            report_path = root / "applicability.json"
            report = create_suite_applicability_report(
                selection=selection,
                build_identity=build,
                discovery_start_directory="backend/tests",
                discovery_pattern="test_*.py",
                output=report_path,
            )
            self.assertEqual(keep_id, report.selected_test_ids[0])

            document = json.loads(report_path.read_text(encoding="utf-8"))
            document["excludedTests"].append(
                {"testId": keep_id, "reasonCode": "container-deployment-only"}
            )
            report_path.write_text(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            with self.assertRaises(SuiteApplicabilityError) as extra:
                load_suite_applicability_report(report_path)
            self.assertEqual("SUITE_APPLICABILITY_INVALID", extra.exception.code)

            document["excludedTests"].pop()
            document["symlinkCapabilities"][0]["winError"] = 5
            report_path.write_text(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            with self.assertRaises(SuiteApplicabilityError) as wrong_error:
                load_suite_applicability_report(report_path)
            self.assertEqual("SUITE_APPLICABILITY_INVALID", wrong_error.exception.code)

    def test_gate_requires_the_exact_build_bound_applicability_artifact(self) -> None:
        from backend.app.api.compat.build_identity import BuildIdentityManifest
        from backend.app.api.compat.gates import (
            CompatibilityGateError,
            _verify_native_windows_applicability,
        )
        from backend.app.api.compat.suite_applicability import (
            NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
            NATIVE_WINDOWS_SYMLINK_TEST_IDS,
            SymlinkCapability,
            create_suite_applicability_report,
            select_native_windows_tests,
        )

        keep_id = "test_api_health.ApiHealthTests.test_health"
        tests = [
            _NamedTest(test_id)
            for test_id in (
                *NATIVE_WINDOWS_CONTAINER_ONLY_TEST_IDS,
                *NATIVE_WINDOWS_SYMLINK_TEST_IDS.values(),
                keep_id,
            )
        ]
        selection = select_native_windows_tests(
            tests,
            symlink_capabilities=(
                SymlinkCapability(kind="directory", available=False, win_error=1314),
                SymlinkCapability(kind="file", available=False, win_error=1314),
            ),
        )

        with tempfile.TemporaryDirectory(prefix="study-app-native-gate-") as raw:
            root = Path(raw)
            build_path = root / f"frozen-build-identity-{'a' * 64}.json"
            build_path.write_bytes(b"{}")
            build = BuildIdentityManifest(
                build_id="a" * 64,
                manifest_path=build_path.resolve(),
                manifest_file_sha256="b" * 64,
                git_revision="c" * 40,
                dirty=False,
                source_tree_hash="d" * 64,
                build_artifact_hash="e" * 64,
                deployment_kind="native-windows",
                canonical_bytes=b"{}",
            )
            report_path = root / "applicability.json"
            create_suite_applicability_report(
                selection=selection,
                build_identity=build,
                discovery_start_directory="backend/tests",
                discovery_pattern="test_*.py",
                output=report_path,
            )
            report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
            record = {
                "totals": 1,
                "buildIdentityManifestPath": str(build.manifest_path),
                "buildIdentityManifestSha256": build.manifest_file_sha256,
                "buildId": build.build_id,
                "artifacts": [
                    {
                        "name": "applicability",
                        "path": str(report_path.resolve()),
                        "sha256": report_sha,
                    }
                ],
            }
            _verify_native_windows_applicability(record, run_root=root, build=build)

            record["buildId"] = "f" * 64
            with self.assertRaises(CompatibilityGateError) as mismatch:
                _verify_native_windows_applicability(record, run_root=root, build=build)
            self.assertEqual("COMPATIBILITY_APPLICABILITY_INVALID", mismatch.exception.code)


if __name__ == "__main__":
    unittest.main()
