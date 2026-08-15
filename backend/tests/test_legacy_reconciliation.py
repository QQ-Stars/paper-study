from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.app.api.compat.database_identity import DatabaseEvidenceIdentityService
from backend.app.api.compat.legacy_reconciliation import (
    LegacyReconciliationError,
    assert_reconciliation_gate,
    reconcile_legacy_database,
)
from backend.app.infrastructure.database_backup import verify_origin_receipt


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "compatibility"
    / "runtime"
    / "p0-origin-receipt-v1.json"
)
RECEIPT_FILE_SHA256 = "7428474fb74bee7bbe6db97a56f08f30520f7d020ff51c149e85ea8a27be6224"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _fixture():
    with tempfile.TemporaryDirectory(prefix="study-app-p6-legacy-reconciliation-") as temp:
        root = Path(temp)
        database = root / "app.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(
                """
                CREATE TABLE papers(id TEXT PRIMARY KEY, explainer TEXT);
                CREATE TABLE translations(paper_id TEXT PRIMARY KEY, content TEXT);
                CREATE TABLE document_sources(
                    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, status TEXT NOT NULL,
                    content_sha256 TEXT
                );
                CREATE TABLE generated_artifacts(
                    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, kind TEXT NOT NULL,
                    source_document_id TEXT, status TEXT NOT NULL, content TEXT,
                    content_sha256 TEXT
                );
                CREATE TABLE notes(paper_id TEXT PRIMARY KEY, content TEXT);
                CREATE TABLE paper_vectors(paper_id TEXT PRIMARY KEY, dim INTEGER, vector BLOB);
                CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY);
                INSERT INTO alembic_version VALUES('20260807_03');
                """
            )
            rows = (
                ("paper-proven", "explainer-proven"),
                ("paper-unprovable", "explainer-unprovable"),
                ("paper-mismatch", "explainer-mismatch"),
                ("paper-empty", ""),
                ("paper-null", None),
            )
            connection.executemany("INSERT INTO papers VALUES(?,?)", rows)
            connection.executemany(
                "INSERT INTO translations VALUES(?,?)",
                (
                    ("paper-proven", "translation-proven"),
                    ("paper-unprovable", "translation-unprovable"),
                    ("paper-mismatch", "translation-mismatch"),
                    ("paper-empty", ""),
                    ("paper-null", None),
                ),
            )
            connection.executemany(
                "INSERT INTO document_sources VALUES(?,?,?,?)",
                (
                    ("source-proven", "paper-proven", "ready", _sha("source-proven")),
                    ("source-other", "paper-unprovable", "ready", _sha("source-other")),
                ),
            )
            connection.executemany(
                "INSERT INTO generated_artifacts VALUES(?,?,?,?,?,?,?)",
                (
                    (
                        "artifact-explainer-proven",
                        "paper-proven",
                        "explainer",
                        "source-proven",
                        "ready",
                        "explainer-proven",
                        _sha("explainer-proven"),
                    ),
                    (
                        "artifact-translation-proven",
                        "paper-proven",
                        "translation",
                        "source-proven",
                        "ready",
                        "translation-proven",
                        _sha("translation-proven"),
                    ),
                    (
                        "artifact-explainer-mismatch",
                        "paper-mismatch",
                        "explainer",
                        "source-other",
                        "ready",
                        "explainer-mismatch",
                        _sha("explainer-mismatch"),
                    ),
                    (
                        "artifact-translation-mismatch",
                        "paper-mismatch",
                        "translation",
                        "source-other",
                        "ready",
                        "different-translation",
                        _sha("different-translation"),
                    ),
                ),
            )
            connection.execute("INSERT INTO notes VALUES('paper-proven','private note')")
            connection.execute(
                "INSERT INTO paper_vectors VALUES(?,?,?)",
                ("paper-proven", 2, b"\x00\xff"),
            )
            connection.commit()
        origin = verify_origin_receipt(RECEIPT_PATH, RECEIPT_FILE_SHA256)
        identity_path = root / "database-identity.json"
        DatabaseEvidenceIdentityService().create_live_database_identity(
            database=database,
            p0_origin_receipt=origin.receipt_path,
            expected_p0_origin_receipt_sha256=origin.origin_receipt_file_sha256,
            origin_backup=origin.backup_path,
            origin_manifest=origin.manifest_path,
            output=identity_path,
        )
        yield database, identity_path


class LegacyReconciliationTests(unittest.TestCase):
    def test_explainer_and_translation_require_proven_source_relation_and_content_hash(
        self,
    ) -> None:
        with _fixture() as (database, identity):
            ledger = reconcile_legacy_database(database, identity)
        proven = {
            (item["paperId"], item["kind"]): item
            for item in ledger["items"]
            if item["classification"] == "proven_migrated"
        }
        self.assertEqual(
            {("paper-proven", "explainer"), ("paper-proven", "translation")},
            set(proven),
        )
        for item in proven.values():
            self.assertEqual("source-proven", item["sourceDocumentId"])
            self.assertEqual(item["legacyContentSha256"], item["artifactContentSha256"])
            self.assertRegex(item["sourceContentSha256"], r"^[0-9a-f]{64}$")

    def test_unprovable_history_keeps_null_source_relation_and_never_backfills(self) -> None:
        with _fixture() as (database, identity):
            ledger = reconcile_legacy_database(database, identity)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT count(*) FROM generated_artifacts WHERE paper_id='paper-unprovable'"
                    ).fetchone()[0],
                )
        unprovable = [
            item for item in ledger["items"] if item["classification"] == "legacy_only_unprovable"
        ]
        self.assertEqual(2, len(unprovable))
        for item in unprovable:
            self.assertIsNone(item["artifactId"])
            self.assertIsNone(item["sourceDocumentId"])
            self.assertIsNone(item["artifactContentSha256"])
            self.assertIsNone(item["sourceContentSha256"])

    def test_mismatch_ambiguity_or_invalid_relation_fails_gate(self) -> None:
        with _fixture() as (database, identity):
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "INSERT INTO generated_artifacts VALUES(?,?,?,?,?,?,?)",
                    (
                        "artifact-explainer-proven-duplicate",
                        "paper-proven",
                        "explainer",
                        "source-proven",
                        "ready",
                        "explainer-proven",
                        _sha("explainer-proven"),
                    ),
                )
                connection.commit()
            ledger = reconcile_legacy_database(database, identity)
        with self.assertRaises(LegacyReconciliationError) as raised:
            assert_reconciliation_gate(ledger)
        self.assertEqual("LEGACY_RECONCILIATION_MISMATCH", raised.exception.code)
        self.assertGreaterEqual(ledger["classificationCounts"]["mismatch"], 3)

    def test_ledger_counts_sets_and_hashes_cover_every_legacy_item_exactly_once(self) -> None:
        with _fixture() as (database, identity):
            ledger = reconcile_legacy_database(database, identity)
        self.assertEqual(6, ledger["itemCount"])
        self.assertEqual(
            ledger["itemCount"],
            sum(ledger["classificationCounts"].values()),
        )
        self.assertEqual(ledger["itemCount"], len(ledger["items"]))
        self.assertEqual(
            6,
            len({(item["paperId"], item["kind"]) for item in ledger["items"]}),
        )
        hashes = (
            *ledger["inputSetHashes"].values(),
            *ledger["classificationSetHashes"].values(),
            ledger["legacyAggregateSha256"],
            ledger["artifactAggregateSha256"],
            ledger["provenanceAggregateSha256"],
        )
        for value in hashes:
            self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_notes_and_paper_vectors_are_preserved_without_claimed_migration(self) -> None:
        with _fixture() as (database, identity):
            ledger = reconcile_legacy_database(database, identity)
        preservation = ledger["preservation"]
        self.assertEqual(1, preservation["notes"]["count"])
        self.assertEqual(1, preservation["paper_vectors"]["count"])
        self.assertEqual(["paper-proven"], preservation["notes"]["primaryKeys"])
        self.assertEqual(["paper-proven"], preservation["paper_vectors"]["primaryKeys"])
        self.assertNotIn("content", preservation["notes"])
        self.assertNotIn("vector", preservation["paper_vectors"])

    def test_reconciliation_is_readonly(self) -> None:
        with _fixture() as (database, identity):
            sidecars = [Path(f"{database}{suffix}") for suffix in ("-wal", "-shm", "-journal")]
            before = (database.read_bytes(), database.stat().st_mtime_ns, [path.exists() for path in sidecars])
            reconcile_legacy_database(database, identity)
            after = (database.read_bytes(), database.stat().st_mtime_ns, [path.exists() for path in sidecars])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
