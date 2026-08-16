from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
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

    def test_node_selection_excludes_only_the_frozen_container_contract_file(self) -> None:
        from backend.app.api.compat.suite_applicability import (
            NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE,
            NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES,
            SuiteApplicabilityError,
            select_native_windows_node_files,
        )

        repository = Path(__file__).resolve().parents[2]
        selection = select_native_windows_node_files(repository)

        self.assertIn(NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE, selection.discovered_files)
        self.assertIn(
            "test/support/legacy-server-process.js",
            selection.selected_files,
        )
        self.assertNotIn(NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE, selection.selected_files)
        self.assertEqual(
            NATIVE_WINDOWS_NODE_CONTAINER_ONLY_TEST_NAMES,
            selection.excluded_test_names,
        )
        self.assertEqual(
            {NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE},
            set(selection.discovered_files) - set(selection.selected_files),
        )

        with tempfile.TemporaryDirectory(prefix="study-app-node-policy-") as raw:
            root = Path(raw)
            source = repository / NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE
            target = root / NATIVE_WINDOWS_NODE_CONTAINER_ONLY_FILE
            target.parent.mkdir(parents=True)
            shutil.copyfile(source, target)
            (target.parent / "kept.test.js").write_text(
                "const test=require('node:test');test('kept',()=>{});\n",
                encoding="utf-8",
            )
            select_native_windows_node_files(root)
            target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaises(SuiteApplicabilityError) as stale:
                select_native_windows_node_files(root)
            self.assertEqual("SUITE_APPLICABILITY_POLICY_STALE", stale.exception.code)

    def test_node_result_and_applicability_are_bound_to_the_node_suite(self) -> None:
        from backend.app.api.compat.build_identity import BuildIdentityManifest
        from backend.app.api.compat.gates import (
            CompatibilityGateError,
            _verify_native_windows_applicability,
        )
        from backend.app.api.compat.suite_applicability import (
            create_node_suite_applicability_report,
            load_node_suite_applicability_report,
            select_native_windows_node_files,
        )

        repository = Path(__file__).resolve().parents[2]
        selection = select_native_windows_node_files(repository)
        with tempfile.TemporaryDirectory(prefix="study-app-native-node-gate-") as raw:
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
            report_path = root / "node-applicability.json"
            report = create_node_suite_applicability_report(
                selection=selection,
                build_identity=build,
                selected_tests=293,
                output=report_path,
            )
            loaded = load_node_suite_applicability_report(report_path)
            self.assertEqual("node-suite", loaded.suite_key)
            self.assertEqual(293, loaded.selected_tests)
            self.assertEqual(298, loaded.discovered_tests)

            record = {
                "totals": 293,
                "buildIdentityManifestPath": str(build.manifest_path),
                "buildIdentityManifestSha256": build.manifest_file_sha256,
                "buildId": build.build_id,
                "artifacts": [
                    {
                        "name": "applicability",
                        "path": str(report.manifest_path),
                        "sha256": report.manifest_file_sha256,
                    }
                ],
            }
            _verify_native_windows_applicability(
                record,
                key="node-suite",
                run_root=root,
                build=build,
            )
            with self.assertRaises(CompatibilityGateError) as wrong_suite:
                _verify_native_windows_applicability(
                    record,
                    key="backend-suite",
                    run_root=root,
                    build=build,
                )
            self.assertEqual(
                "COMPATIBILITY_APPLICABILITY_INVALID",
                wrong_suite.exception.code,
            )

    def test_node_junit_conversion_counts_failures_and_skips_once(self) -> None:
        from backend.app.cli.native_windows_node_suite import _read_node_junit_result

        payload = b"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<testsuites>
  <testcase name=\"passing\" classname=\"test\"/>
  <testcase name=\"failing\" classname=\"test\" failure=\"boom\"><failure/></testcase>
  <testcase name=\"skipped\" classname=\"test\"><skipped/></testcase>
</testsuites>
"""
        with tempfile.TemporaryDirectory(prefix="study-app-node-junit-") as raw:
            result = Path(raw) / "result.xml"
            result.write_bytes(payload)
            self.assertEqual(
                {"tests": 3, "pass": 1, "fail": 1, "skipped": 1},
                _read_node_junit_result(result),
            )


if __name__ == "__main__":
    unittest.main()
