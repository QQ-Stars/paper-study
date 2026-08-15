from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


class OcrDisabledBaselineTests(unittest.TestCase):
    def test_disabled_ocr_never_constructs_or_calls_provider(self) -> None:
        import fitz

        from agent import extract as legacy_extract
        from backend.app.application.extraction_gate import extract_with_ocr_gate
        from backend.app.rollout import load_rollout_settings

        counts = {"constructed": 0, "extract": 0, "native": 0}

        class CountingProvider:
            def extract(self, _source):
                counts["extract"] += 1
                return "OCR text"

        def provider_factory():
            counts["constructed"] += 1
            return CountingProvider()

        def native_extraction(source):
            counts["native"] += 1
            return legacy_extract._plain_pages(source, 8)

        environment = {**os.environ, "OCR_ENABLED": "0"}
        with tempfile.TemporaryDirectory(prefix="study-app-empty-native-") as temp_dir:
            scanned_pdf = Path(temp_dir) / "scanned.pdf"
            document = fitz.open()
            try:
                document.new_page()
                document.save(scanned_pdf)
            finally:
                document.close()

            self.assertNotIn("paddleocr", sys.modules)
            result = extract_with_ocr_gate(
                scanned_pdf,
                native_extract=native_extraction,
                ocr_provider_factory=provider_factory,
                rollout=load_rollout_settings(environment),
            )
        self.assertEqual("", result)
        self.assertEqual({"constructed": 0, "extract": 0, "native": 1}, counts)
        self.assertNotIn("paddleocr", sys.modules)

    def test_enabled_ocr_is_rejected_with_a_classified_unavailable_error(self) -> None:
        from backend.app.application.extraction_gate import extract_with_ocr_gate
        from backend.app.rollout import RolloutConfigurationError, RolloutSettings

        with self.assertRaises(RolloutConfigurationError) as raised:
            extract_with_ocr_gate(
                "scanned.pdf",
                native_extract=lambda _source: "",
                ocr_provider_factory=lambda: self.fail("P0 must not construct an OCR provider"),
                rollout=RolloutSettings(ocr_enabled=True),
            )

        self.assertEqual(raised.exception.code, "ROLLOUT_ADAPTER_UNAVAILABLE")
        self.assertEqual(raised.exception.variable, "OCR_ENABLED")
        self.assertEqual(raised.exception.value, "1")


if __name__ == "__main__":
    unittest.main()
