from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest


class MachineSummaryTests(unittest.TestCase):
    def test_all_runner_adapters_require_json_or_junit_and_never_parse_console_text(self) -> None:
        from backend.app.api.compat.machine_summary import (
            MachineSummaryError,
            create_machine_summary,
        )

        fixtures = {
            "unittest": {"testsRun": 4, "failures": [], "errors": [], "skipped": []},
            "node-test": {"tests": 4, "pass": 4, "fail": 0, "skipped": 0},
            "vitest": {"numTotalTests": 4, "numFailedTests": 0, "numPendingTests": 0},
            "playwright": {"stats": {"expected": 4, "unexpected": 0, "skipped": 0}},
        }
        with tempfile.TemporaryDirectory(prefix="study-app-p6-summary-") as raw:
            root = Path(raw)
            for adapter, document in fixtures.items():
                with self.subTest(adapter=adapter):
                    result = root / f"{adapter}-result.json"
                    output = root / f"{adapter}-summary.json"
                    result.write_text(json.dumps(document), encoding="utf-8")
                    summary = create_machine_summary(
                        adapter=adapter,
                        raw_exit=0,
                        result_artifact=result,
                        summary_output=output,
                        console_stdout=b"FAILED 999 tests\n",
                        console_stderr=b"not ok\n",
                    )
                    self.assertEqual((4, 0, 0), (summary.totals, summary.failures, summary.skips))
                    self.assertEqual("machine-summary", json.loads(output.read_text(encoding="utf-8"))["manifestKind"])

            with self.assertRaises(MachineSummaryError) as missing:
                create_machine_summary(
                    adapter="unittest",
                    raw_exit=0,
                    result_artifact=root / "missing.json",
                    summary_output=root / "forged-summary.json",
                    console_stdout=b"Ran 4 tests in 0.001s\nOK\n",
                )
            self.assertEqual("MACHINE_RESULT_MISSING", missing.exception.code)

            junit = root / "results.xml"
            junit.write_text(
                '<testsuite tests="3" failures="1" errors="0" skipped="0"><testcase><failure/></testcase></testsuite>',
                encoding="utf-8",
            )
            with self.assertRaises(MachineSummaryError) as contradiction:
                create_machine_summary(
                    adapter="playwright",
                    raw_exit=0,
                    result_artifact=junit,
                    summary_output=root / "junit-summary.json",
                    console_stdout=b"OK",
                )
            self.assertEqual("MACHINE_RESULT_CONTRADICTORY", contradiction.exception.code)

            fixture = root / "test_runner_fixture.py"
            fixture.write_text(
                "import unittest\n"
                "class RunnerFixture(unittest.TestCase):\n"
                "    def test_machine_count(self):\n"
                "        self.assertEqual(2 + 2, 4)\n",
                encoding="utf-8",
            )
            from backend.app.cli.machine_summary_runner import main as runner_main

            direct_output = root / "direct-unittest-summary.json"
            exit_code = runner_main(
                [
                    "--adapter", "unittest", "--summary-output", str(direct_output), "--",
                    sys.executable, "-B", "-m", "unittest", "discover", "-s", str(root),
                    "-p", fixture.name, "-v",
                ]
            )
            self.assertEqual(0, exit_code)
            self.assertEqual(1, json.loads(direct_output.read_text(encoding="utf-8"))["totals"])

    def test_exit_zero_with_nonzero_skip_is_failure(self) -> None:
        from backend.app.api.compat.machine_summary import (
            MachineSummaryFailure,
            create_machine_summary,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p6-summary-skip-") as raw:
            root = Path(raw)
            result = root / "vitest.json"
            result.write_text(
                json.dumps({"numTotalTests": 3, "numFailedTests": 0, "numPendingTests": 1}),
                encoding="utf-8",
            )
            output = root / "summary.json"
            with self.assertRaises(MachineSummaryFailure) as caught:
                create_machine_summary(
                    adapter="vitest",
                    raw_exit=0,
                    result_artifact=result,
                    summary_output=output,
                )
            self.assertEqual("MACHINE_RESULT_NOT_CLEAN", caught.exception.code)
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(1, document["skips"])
            self.assertEqual(0, document["rawExit"])


if __name__ == "__main__":
    unittest.main()
