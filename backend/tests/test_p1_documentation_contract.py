from __future__ import annotations

from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class P1DocumentationContractTests(unittest.TestCase):
    def test_database_runbook_records_complete_p1_contract_and_operations(self) -> None:
        runbook = (REPOSITORY_ROOT / "docs" / "DATABASE.md").read_text(
            encoding="utf-8"
        )
        required_snippets = (
            "20260807_01",
            "document_sources",
            "generated_artifacts",
            "processing_jobs",
            "document_chunks",
            "obsidian_exports",
            "(paper_id, pdf_sha256, mode, provider, model, options_hash, processing_version)",
            "(source_document_id, kind, generator_provider, generator_model, prompt_version)",
            "source_materialize, ocr, explain, translate, embed, obsidian_export, obsidian_sync",
            "paper_id and source_mode are both required",
            "obsidian_export requires paper_id",
            "obsidian_sync permits paper_id and source_mode to be NULL",
            "explainer, translation, summary, outline, study_card, classification, metadata",
            "No historical backfill",
            "environment -> Keyring -> legacy settings",
            "LLM_API_KEY",
            "OCR_API_KEY",
            "EMBED_API_KEY",
            "S2_API_KEY",
            "apiKey",
            "ocrApiKey",
            "embedApiKey",
            "s2ApiKey",
            "credential:llm",
            "credential:ocr",
            "credential:embedding",
            "credential:semantic_scholar",
            "hasKey",
            "keyTail",
            "Blank submission preserves",
            "Explicit clear",
            "fixed fixture",
            "OCR_PROVIDER_CONTRACT_UNVERIFIED",
            "CREDENTIAL_PROBE_UNSUPPORTED",
            "retained legacy plaintext security debt",
            "Node runtime rollback",
            "No P0-P6 phase removes",
            "database ledger row",
            "non-auto-cleanable orphan/tombstone",
            "Python 3.10",
            "Do not rebuild the existing virtual environment",
            "database_backup create",
            "database_backup verify",
            "database_backup restore-check",
            "database_backup inspect",
            "upgrade -> downgrade -> re-upgrade",
            "legacy table count/hash",
            "five P1 table counts are zero",
            "exactly one Alembic head",
            "quick_check=ok",
            "integrity_check=ok",
            "foreign_key_violations=0",
            "DOCUMENT_PIPELINE_MODE=p1",
            "GENERATION_PIPELINE_MODE=p1",
            "ARTIFACT_READ_MODE=prefer_new",
            "ARTIFACT_WRITE_MODE=dual",
            "DOCUMENT_PIPELINE_MODE=legacy",
            "GENERATION_PIPELINE_MODE=legacy",
            "ARTIFACT_READ_MODE=legacy",
            "ARTIFACT_WRITE_MODE=legacy",
            "Runtime rollback comes before schema downgrade",
            "P1_DOWNGRADE_NONEMPTY",
            "discards every write made after that snapshot",
        )
        missing = [snippet for snippet in required_snippets if snippet not in runbook]
        self.assertEqual([], missing, f"P1 runbook is missing: {missing}")


if __name__ == "__main__":
    unittest.main()
