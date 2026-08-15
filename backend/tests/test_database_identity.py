from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from backend.tests.support.p4_identity import p4_identity_fixture


def _service():
    from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService

    return DatabaseEvidenceIdentityService()


def _create_live(service: object, fixture: object, output: Path):
    return service.create_live_database_identity(
        database=fixture.database_path,
        p0_origin_receipt=fixture.receipt_path,
        expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
        origin_backup=fixture.origin_backup_path,
        origin_manifest=fixture.origin_manifest_path,
        output=output,
    )


class DatabaseIdentityTests(unittest.TestCase):
    def test_exclusive_write_preserves_exact_crlf_bytes(self) -> None:
        from backend.app.api.compat.database_identity import exclusive_write_bytes

        payload = b'{"ok":true}\r\n'
        with tempfile.TemporaryDirectory(prefix="study-app-binary-evidence-") as raw:
            output = Path(raw) / "capture.stdout.bin"
            exclusive_write_bytes(output, payload)
            self.assertEqual(payload, output.read_bytes())

    def test_descendant_identity_rejects_the_live_database_subject(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseIdentityError
        from backend.app.infrastructure.database_backup import create_verified_backup

        with p4_identity_fixture() as fixture:
            service = _service()
            live_identity = _create_live(
                service,
                fixture,
                fixture.root / "live-identity.json",
            )
            backup = create_verified_backup(
                fixture.database_path,
                fixture.root / "candidate-backups",
                label="candidate",
            )

            with self.assertRaises(DatabaseIdentityError) as raised:
                service.create_descendant_database_identity(
                    database=fixture.database_path,
                    subject_kind="p4_candidate",
                    parent_database_identity_manifest=live_identity.manifest_path,
                    parent_backup=backup.backup_path,
                    parent_manifest=backup.manifest_path,
                    output=fixture.root / "candidate-identity.json",
                )

            self.assertEqual(
                "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
                raised.exception.code,
            )
            self.assertFalse((fixture.root / "candidate-identity.json").exists())

            live_alias = fixture.root / "live-alias.db"
            shutil.copyfile(backup.backup_path, live_alias)
            with (
                mock.patch(
                    "backend.app.api.compat.database_identity.read_platform_file_identity",
                    return_value=live_identity.platform_file_identity,
                ),
                self.assertRaises(DatabaseIdentityError) as alias_raised,
            ):
                service.create_descendant_database_identity(
                    database=live_alias,
                    subject_kind="p4_candidate",
                    parent_database_identity_manifest=live_identity.manifest_path,
                    parent_backup=backup.backup_path,
                    parent_manifest=backup.manifest_path,
                    output=fixture.root / "alias-identity.json",
                )
            self.assertEqual(
                "DATABASE_IDENTITY_DESCENDANT_LIVE_SUBJECT",
                alias_raised.exception.code,
            )
            self.assertFalse((fixture.root / "alias-identity.json").exists())

    def test_descendant_identity_requires_the_verified_backup_fingerprint(self) -> None:
        from backend.app.api.compat.database_identity import DatabaseIdentityError
        from backend.app.infrastructure.database_backup import create_verified_backup

        with p4_identity_fixture() as fixture:
            service = _service()
            live_identity = _create_live(
                service,
                fixture,
                fixture.root / "live-identity.json",
            )
            backup = create_verified_backup(
                fixture.database_path,
                fixture.root / "candidate-backups",
                label="candidate",
            )
            restored = fixture.root / "restore-validation-fixed" / "app.db"
            restored.parent.mkdir()
            shutil.copyfile(backup.backup_path, restored)
            with closing(sqlite3.connect(restored)) as connection:
                connection.execute("CREATE TABLE restore_tamper(value TEXT NOT NULL)")
                connection.execute("INSERT INTO restore_tamper VALUES ('changed')")
                connection.commit()

            rejected_output = fixture.root / "rejected-descendant.json"
            with self.assertRaises(DatabaseIdentityError) as raised:
                service.create_descendant_database_identity(
                    database=restored,
                    subject_kind="p4_candidate",
                    parent_database_identity_manifest=live_identity.manifest_path,
                    parent_backup=backup.backup_path,
                    parent_manifest=backup.manifest_path,
                    output=rejected_output,
                )
            self.assertEqual(
                "DATABASE_IDENTITY_RESTORE_MISMATCH",
                raised.exception.code,
            )
            self.assertFalse(rejected_output.exists())

            restored.unlink()
            shutil.copyfile(backup.backup_path, restored)
            accepted = service.create_descendant_database_identity(
                database=restored,
                subject_kind="p4_candidate",
                parent_database_identity_manifest=live_identity.manifest_path,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                output=fixture.root / "accepted-descendant.json",
            )
            self.assertEqual(backup.verification.backup_id, accepted.parent_backup_id)
            self.assertEqual(
                backup.verification.manifest_file_sha256,
                accepted.parent_manifest_sha256,
            )

    def test_v1_lineage_is_stable_and_subject_is_file_instance_specific(self) -> None:
        with p4_identity_fixture() as fixture:
            service = _service()
            first = _create_live(service, fixture, fixture.root / "identity-1.json")
            with closing(sqlite3.connect(fixture.database_path)) as connection:
                connection.execute(
                    "UPDATE notes SET content = 'ordinary live write' WHERE paper_id = 'paper-1'"
                )
                connection.commit()
            second = _create_live(service, fixture, fixture.root / "identity-2.json")
            self.assertEqual(first.database_lineage_id, second.database_lineage_id)
            self.assertEqual(first.subject_database_id, second.subject_database_id)

            replacement = fixture.root / "replacement.db"
            replacement.write_bytes(fixture.database_path.read_bytes())
            os.replace(replacement, fixture.database_path)
            third = _create_live(service, fixture, fixture.root / "identity-3.json")
            self.assertEqual(first.database_lineage_id, third.database_lineage_id)
            self.assertNotEqual(first.subject_database_id, third.subject_database_id)
            self.assertEqual("live", third.subject_kind)

            document = json.loads((fixture.root / "identity-1.json").read_text(encoding="utf-8"))
            self.assertEqual(1, document["schemaVersion"])
            self.assertEqual(first.database_lineage_id, document["databaseLineageId"])
            self.assertEqual(first.subject_database_id, document["subjectDatabaseId"])

    def test_p0_origin_receipt_is_exclusive_and_tamper_evident(self) -> None:
        with p4_identity_fixture() as fixture:
            service = _service()
            output = fixture.root / "identity.json"
            _create_live(service, fixture, output)
            before = output.read_bytes()
            with self.assertRaisesRegex(Exception, "exists|exclusive|already"):
                _create_live(service, fixture, output)
            self.assertEqual(before, output.read_bytes())

            tampered_receipt = fixture.root / "tampered" / fixture.receipt_path.name
            tampered_receipt.parent.mkdir()
            shutil.copyfile(fixture.receipt_path, tampered_receipt)
            tampered = json.loads(tampered_receipt.read_text(encoding="utf-8"))
            tampered["databaseLineageId"] = "0" * 64
            tampered_receipt.write_text(
                json.dumps(tampered, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            rejected_output = fixture.root / "tampered-identity.json"
            with self.assertRaises(Exception):
                service.create_live_database_identity(
                    database=fixture.database_path,
                    p0_origin_receipt=tampered_receipt,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                    output=rejected_output,
                )
            self.assertFalse(rejected_output.exists())

    def test_live_identity_rejects_verified_origin_not_named_by_p0_receipt(self) -> None:
        with p4_identity_fixture() as fixture:
            service = _service()
            output = fixture.root / "wrong-origin-identity.json"
            with self.assertRaises(Exception):
                service.create_live_database_identity(
                    database=fixture.database_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.alternate_backup_path,
                    origin_manifest=fixture.alternate_manifest_path,
                    output=output,
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
