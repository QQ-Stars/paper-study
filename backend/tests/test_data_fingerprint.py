from __future__ import annotations

import copy
import hashlib
import json
import shutil
import unittest

from backend.app.api.compat.data_fingerprint import (
    DataFingerprintError,
    encode_row_v1,
    compare_backup_logical_evidence,
    compare_fingerprints,
    fingerprint_database,
    validate_fingerprint_document,
)
from backend.app.api.compat.schema_inventory import PROCESSING_JOB_COLUMNS
from backend.app.api.compat.database_identity import (
    DatabaseEvidenceIdentityService,
    DatabaseIdentityError,
    verify_evidence_database_binding,
)
from backend.app.infrastructure.database_backup import create_verified_backup
from backend.tests.support.p4_identity import p4_identity_fixture


APPLICATION_TABLES = {
    "papers",
    "progress",
    "paper_reviews",
    "notes",
    "favorites",
    "translations",
    "paper_vectors",
    "cite_edges",
    "ingest_jobs",
    "job_candidates",
    "job_schedules",
    "schema_migrations",
    "document_sources",
    "generated_artifacts",
    "processing_jobs",
    "document_chunks",
    "obsidian_exports",
    "paper_artifact_heads",
    "processing_job_events",
    "ocr_page_checkpoints",
    "document_chunk_embeddings",
    "artifact_translation_checkpoints",
}
TRIGGERS = {
    "document_chunks_fts_ad",
    "document_chunks_fts_ai",
    "document_chunks_fts_au",
    "processing_jobs_spec_guard_insert",
    "processing_jobs_spec_guard_update",
}


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _resign(report: dict[str, object]) -> None:
    report["canonicalDataSha256"] = _canonical_sha(
        {key: value for key, value in report.items() if key != "canonicalDataSha256"}
    )


def _delta_entry(
    table: str,
    before: dict[str, object],
    after: dict[str, object],
) -> dict[str, object]:
    before_table = before["tables"][table]
    after_table = after["tables"][table]
    unsigned = {
        "table": table,
        "operation": "insert",
        "primaryKeys": ["job-p6-write-smoke"],
        "countDelta": after_table["count"] - before_table["count"],
        "beforePrimaryKeySetSha256": before_table["primaryKeySetSha256"],
        "afterPrimaryKeySetSha256": after_table["primaryKeySetSha256"],
        "beforeRowSha256": before_table["rowSha256"],
        "afterRowSha256": after_table["rowSha256"],
        "jobId": "job-p6-write-smoke",
        "sourceDocumentId": "source-p6-write-smoke",
        "artifactId": "artifact-p6-write-smoke",
    }
    return {**unsigned, "evidenceSha256": _canonical_sha(unsigned)}


class DataFingerprintTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_encoding_is_stable_for_null_real_text_blob(self) -> None:
        expected = (
            '{"version":1,"cells":['
            '{"type":"null"},'
            '{"type":"real","value":"8000000000000000"},'
            '{"type":"real","value":"3ff8000000000000"},'
            '{"type":"text","value":"\u00e9\\r\\nline"},'
            '{"type":"blob","value":"00ff"}'
            ']}'
        ).encode("utf-8")

        encoded = encode_row_v1(
            (None, -0.0, 1.5, "e\u0301\r\nline", b"\x00\xff")
        )

        self.assertEqual(expected, encoded)
        self.assertEqual(
            "e2a7fca78b397a233e589c55a9e99620ebe450736784a3b49d81085ab82e3af4",
            hashlib.sha256(encoded).hexdigest(),
        )

    async def test_fingerprint_reports_required_counts_pk_and_legacy_hashes(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            report = fingerprint_database(fixture.database_path)

        self.assertEqual(APPLICATION_TABLES, set(report["tables"]))
        self.assertEqual(22, len(report["tables"]))
        self.assertGreater(report["tables"]["papers"]["count"], 0)
        self.assertEqual(["id"], report["tables"]["papers"]["primaryKeyColumns"])
        self.assertEqual(
            {"explainer", "translation"},
            set(report["legacyColumnHashes"]),
        )
        for entry in report["legacyColumnHashes"].values():
            self.assertIsInstance(entry["count"], int)
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    async def test_fingerprint_emits_canonical_data_sha_and_forbids_backup_logical_field(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            report = fingerprint_database(fixture.database_path)

        unsigned = {key: value for key, value in report.items() if key != "canonicalDataSha256"}
        expected = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, report["canonicalDataSha256"])
        self.assertNotIn("logicalSha256", report)
        validate_fingerprint_document(report)
        invalid = {**report, "logicalSha256": report["canonicalDataSha256"]}
        with self.assertRaises(DataFingerprintError) as raised:
            validate_fingerprint_document(invalid)
        self.assertEqual("FINGERPRINT_DOCUMENT_INVALID", raised.exception.code)

    async def test_fingerprint_freezes_processing_job_spec_and_exact_five_trigger_inventory(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            report = fingerprint_database(fixture.database_path)

        self.assertEqual(
            list(PROCESSING_JOB_COLUMNS),
            report["tables"]["processing_jobs"]["columns"],
        )
        self.assertEqual(
            report["tables"]["processing_jobs"]["count"],
            report["processingJobSpecs"]["count"],
        )
        self.assertEqual(0, report["processingJobSpecs"]["strictDecodeErrorCount"])
        self.assertEqual(TRIGGERS, set(report["triggers"]))
        self.assertEqual(
            "trigram case_sensitive 0 remove_diacritics 1",
            report["fts"]["tokenizer"],
        )
        self.assertTrue(report["fts"]["externalContentRowidJoin"])
        self.assertEqual({"revision": "20260807_03", "count": 1}, report["alembic"])
        self.assertEqual({"quickCheck": "ok", "foreignKeyViolationCount": 0}, report["integrity"])

    async def test_strict_compare_rejects_any_table_delta(self) -> None:
        with p4_identity_fixture() as fixture:
            before = fingerprint_database(fixture.database_path)
        after = copy.deepcopy(before)
        after["tables"]["notes"]["rowSha256"] = "1" * 64
        _resign(after)

        with self.assertRaises(DataFingerprintError) as raised:
            compare_fingerprints(before, after, mode="strict-readonly")
        self.assertEqual("FINGERPRINT_MISMATCH", raised.exception.code)

    async def test_explained_write_compare_requires_exact_new_table_delta_ledger_and_unchanged_legacy(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            before = fingerprint_database(fixture.database_path)
        after = copy.deepcopy(before)
        jobs = after["tables"]["processing_jobs"]
        jobs["count"] += 1
        jobs["primaryKeySetSha256"] = "1" * 64
        jobs["rowSha256"] = "2" * 64
        specs = after["processingJobSpecs"]
        specs["count"] += 1
        specs["strictDecodeCount"] += 1
        specs["sha256"] = "3" * 64
        _resign(after)
        ledger = {
            "schemaVersion": 1,
            "entries": [_delta_entry("processing_jobs", before, after)],
        }

        self.assertTrue(
            compare_fingerprints(
                before,
                after,
                mode="explained-write",
                delta_ledger=ledger,
            )
        )
        invalid = copy.deepcopy(ledger)
        invalid["entries"][0]["afterRowSha256"] = "4" * 64
        with self.assertRaises(DataFingerprintError):
            compare_fingerprints(
                before,
                after,
                mode="explained-write",
                delta_ledger=invalid,
            )
        legacy_after = copy.deepcopy(after)
        legacy_after["tables"]["notes"]["rowSha256"] = "5" * 64
        _resign(legacy_after)
        with self.assertRaises(DataFingerprintError):
            compare_fingerprints(
                before,
                legacy_after,
                mode="explained-write",
                delta_ledger=ledger,
            )

    def test_cutover_backup_equality_uses_backup_compatible_logical_sha(self) -> None:
        evidence = {"logicalSha256": "a" * 64}
        self.assertTrue(compare_backup_logical_evidence(evidence, dict(evidence)))
        with self.assertRaises(DataFingerprintError) as raised:
            compare_backup_logical_evidence(
                evidence,
                {"logicalSha256": "b" * 64},
            )
        self.assertEqual("BACKUP_LOGICAL_MISMATCH", raised.exception.code)

    def test_canonical_data_sha_is_not_accepted_as_backup_logical_sha(self) -> None:
        canonical = {"canonicalDataSha256": "a" * 64}
        with self.assertRaises(DataFingerprintError) as raised:
            compare_backup_logical_evidence(canonical, canonical)
        self.assertEqual("BACKUP_LOGICAL_EVIDENCE_INVALID", raised.exception.code)

    def test_p6_evidence_binding_rejects_wrong_subject_parent_chain_or_origin_anchor(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            service = DatabaseEvidenceIdentityService()
            live_manifest_path = fixture.root / "live-identity.json"
            service.create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=live_manifest_path,
            )
            parent = create_verified_backup(
                fixture.database_path,
                fixture.root / "p6-parent",
                label="p6-parent",
            )
            candidate = fixture.root / "p6-candidate.db"
            shutil.copyfile(fixture.database_path, candidate)
            candidate_manifest = fixture.root / "p6-candidate-identity.json"
            service.create_descendant_database_identity(
                database=candidate,
                subject_kind="p6_candidate",
                parent_database_identity_manifest=live_manifest_path,
                parent_backup=parent.backup_path,
                parent_manifest=parent.manifest_path,
                output=candidate_manifest,
            )

            verified = verify_evidence_database_binding(
                database=candidate,
                database_identity_manifest=candidate_manifest,
                parent_database_identity_manifest=live_manifest_path,
                parent_backup=parent.backup_path,
                parent_manifest=parent.manifest_path,
                origin_receipt=fixture.receipt_path,
                expected_origin_receipt_file_sha256=fixture.receipt_file_sha256,
                expected_subject_kind="p6_candidate",
            )
            self.assertEqual("p6_candidate", verified.subject_kind)
            with self.assertRaises(DatabaseIdentityError):
                verify_evidence_database_binding(
                    database=candidate,
                    database_identity_manifest=candidate_manifest,
                    parent_database_identity_manifest=live_manifest_path,
                    parent_backup=parent.backup_path,
                    parent_manifest=parent.manifest_path,
                    origin_receipt=fixture.receipt_path,
                    expected_origin_receipt_file_sha256="0" * 64,
                    expected_subject_kind="p6_candidate",
                )

    def test_p6_evidence_binding_accepts_two_distinct_verified_descendants_in_one_lineage(
        self,
    ) -> None:
        with p4_identity_fixture() as fixture:
            service = DatabaseEvidenceIdentityService()
            live_manifest_path = fixture.root / "live-identity.json"
            service.create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=live_manifest_path,
            )
            parent = create_verified_backup(
                fixture.database_path,
                fixture.root / "p6-parent",
                label="p6-parent",
            )
            bindings = []
            for suffix in ("a", "b"):
                database = fixture.root / f"p6-candidate-{suffix}.db"
                manifest = fixture.root / f"p6-candidate-{suffix}-identity.json"
                shutil.copyfile(fixture.database_path, database)
                service.create_descendant_database_identity(
                    database=database,
                    subject_kind="p6_candidate",
                    parent_database_identity_manifest=live_manifest_path,
                    parent_backup=parent.backup_path,
                    parent_manifest=parent.manifest_path,
                    output=manifest,
                )
                bindings.append(
                    verify_evidence_database_binding(
                        database=database,
                        database_identity_manifest=manifest,
                        parent_database_identity_manifest=live_manifest_path,
                        parent_backup=parent.backup_path,
                        parent_manifest=parent.manifest_path,
                        origin_receipt=fixture.receipt_path,
                        expected_origin_receipt_file_sha256=fixture.receipt_file_sha256,
                        expected_subject_kind="p6_candidate",
                    )
                )

        self.assertEqual(bindings[0].database_lineage_id, bindings[1].database_lineage_id)
        self.assertNotEqual(bindings[0].subject_database_id, bindings[1].subject_database_id)


if __name__ == "__main__":
    unittest.main()
