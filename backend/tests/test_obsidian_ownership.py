from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.application.obsidian_projection import ExportOptions, build_projection_plan
from backend.app.domain import VaultProjection
from backend.tests.test_obsidian_layout import _snapshot


GOLDEN = Path(__file__).parent / "fixtures" / "obsidian" / "golden"


class ObsidianOwnershipTests(unittest.TestCase):
    def test_manifest_and_markers_are_deterministic(self) -> None:
        from backend.app.providers.obsidian_vault import (
            build_manifest,
            parse_managed_marker,
            parse_note_seed_marker,
            serialize_manifest,
        )

        plan = build_projection_plan(_snapshot(), ExportOptions())
        generated_at = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        manifest = build_manifest(plan.files, generated_at=generated_at)
        encoded = serialize_manifest(manifest)

        self.assertEqual((GOLDEN / "manifest.json").read_bytes(), encoded)
        self.assertEqual(
            tuple(sorted(entry.path for entry in manifest.entries)),
            tuple(entry.path for entry in manifest.entries),
        )
        for entry in manifest.entries:
            self.assertFalse(entry.path.startswith(("/", "\\")))
            self.assertNotIn("\\", entry.path)
            self.assertNotIn("..", entry.path.split("/"))

        by_kind = {item.kind: item for item in plan.files}
        for kind in ("paper", "source", "explainer", "translation"):
            item = by_kind[kind]
            marker = parse_managed_marker(item.data)
            self.assertIsNotNone(marker)
            self.assertEqual("paper-01", marker.paper_id)
            self.assertEqual(kind, marker.kind)
            self.assertEqual(item.artifact_id, marker.artifact_id)
            self.assertEqual(item.source_hash, marker.source_hash)
            self.assertIsNone(parse_note_seed_marker(item.data))

        note = by_kind["note"]
        note_marker = parse_note_seed_marker(note.data)
        self.assertIsNotNone(note_marker)
        self.assertEqual("paper-01", note_marker.paper_id)
        self.assertIsNone(parse_managed_marker(note.data))
        self.assertEqual("user", next(e for e in manifest.entries if e.kind == "note").ownership)

        duplicate = by_kind["paper"].data.replace(
            b"paper-id: \"paper-01\"\n",
            b"paper-id: \"paper-01\"\npaper-id: \"paper-01\"\n",
            1,
        )
        self.assertIsNone(parse_managed_marker(duplicate))

    def test_manifest_carries_forward_user_stale_and_orphan_entries(self) -> None:
        from backend.app.providers.obsidian_vault import (
            ManifestEntry,
            ObsidianManifest,
            merge_manifest,
        )

        prior_note = ManifestEntry(
            "Notes/paper-01.md", "note", "paper-01", None, "user", "1" * 64, "2" * 64
        )
        stale = ManifestEntry(
            "Translations/paper-01.md",
            "translation",
            "paper-01",
            "old-artifact",
            "managed",
            "3" * 64,
            "4" * 64,
        )
        orphan = ManifestEntry(
            "Papers/deleted-paper.md",
            "paper",
            "deleted-paper",
            None,
            "managed",
            "5" * 64,
            "6" * 64,
        )
        prior = ObsidianManifest(NOW, (prior_note, stale, orphan))
        attempted_note_reseed = ManifestEntry(
            prior_note.path,
            prior_note.kind,
            prior_note.paper_id,
            None,
            "user",
            "7" * 64,
            "8" * 64,
        )
        published = ManifestEntry(
            "Papers/paper-01.md", "paper", "paper-01", None, "managed", "9" * 64, "a" * 64
        )

        merged = merge_manifest(
            prior,
            (attempted_note_reseed, published),
            generated_at=NOW,
        )

        by_path = {entry.path: entry for entry in merged.entries}
        self.assertEqual(prior_note, by_path[prior_note.path])
        self.assertEqual(stale, by_path[stale.path])
        self.assertEqual(orphan, by_path[orphan.path])
        self.assertEqual(published, by_path[published.path])

    def test_user_modified_managed_file_is_never_overwritten(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import VaultWriter

        old_file = next(
            item for item in build_projection_plan(_snapshot(), ExportOptions()).files if item.kind == "paper"
        )
        new_snapshot = _snapshot(title="A changed title", paper_source_hash="b" * 64)
        new_file = next(
            item for item in build_projection_plan(new_snapshot, ExportOptions()).files if item.kind == "paper"
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-writer-conflict-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            with BoundVaultRoot.open(vault) as bound:
                writer = VaultWriter(bound, root_folder="Research")
                first = writer.publish(old_file)
                target = vault / "Research" / old_file.path
                user_bytes = target.read_bytes() + b"\nuser edit\n"
                target.write_bytes(user_bytes)
                ledger = _ledger(old_file, first.entry.exported_hash)

                result = writer.publish(new_file, prior=first.entry, ledger=ledger)

            self.assertEqual("conflict", result.status)
            self.assertEqual(user_bytes, target.read_bytes())
            self.assertEqual([], list(target.parent.glob(".*.tmp")))

    def test_note_is_seeded_once_then_permanently_user_managed(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import VaultWriter

        note = next(
            item for item in build_projection_plan(_snapshot(), ExportOptions()).files if item.kind == "note"
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-writer-note-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            with BoundVaultRoot.open(vault) as bound:
                writer = VaultWriter(bound, root_folder="Research")
                first = writer.publish(note)
                target = vault / "Research" / note.path
                target.write_bytes(b"user owned note\n")
                with (
                    patch.object(bound, "publish_new", wraps=bound.publish_new) as publish,
                    patch.object(bound, "replace_managed", wraps=bound.replace_managed) as replace,
                    patch.object(bound, "delete_managed", wraps=bound.delete_managed) as delete,
                ):
                    second = writer.publish(note, prior=first.entry)

                self.assertEqual("user_managed", second.status)
                self.assertEqual(b"user owned note\n", target.read_bytes())
                publish.assert_not_called()
                replace.assert_not_called()
                delete.assert_not_called()

    def test_same_source_hash_is_noop(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import VaultWriter

        paper = next(
            item for item in build_projection_plan(_snapshot(), ExportOptions()).files if item.kind == "paper"
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-writer-noop-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            with BoundVaultRoot.open(vault) as bound:
                writer = VaultWriter(bound, root_folder="Research")
                first = writer.publish(paper)
                ledger = _ledger(paper, first.entry.exported_hash)
                with (
                    patch.object(bound, "publish_new", wraps=bound.publish_new) as publish,
                    patch.object(bound, "replace_managed", wraps=bound.replace_managed) as replace,
                    patch.object(bound, "delete_managed", wraps=bound.delete_managed) as delete,
                ):
                    second = writer.publish(paper, prior=first.entry, ledger=ledger)

                self.assertEqual("unchanged", second.status)
                publish.assert_not_called()
                replace.assert_not_called()
                delete.assert_not_called()

    def test_vault_writer_has_no_unproven_or_automatic_delete_path(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import CleanupProof, VaultWriter

        paper = next(
            item for item in build_projection_plan(_snapshot(), ExportOptions()).files if item.kind == "paper"
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-writer-delete-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            with BoundVaultRoot.open(vault) as bound:
                writer = VaultWriter(bound, root_folder="Research")
                first = writer.publish(paper)
                with patch.object(bound, "delete_managed", wraps=bound.delete_managed) as delete:
                    with self.assertRaises(TypeError):
                        writer.delete_with_proof(object())
                    delete.assert_not_called()
                    writer.delete_with_proof(
                        CleanupProof(
                            entry=first.entry,
                            ledger=_ledger(paper, first.entry.exported_hash),
                            target=first.target,
                        )
                    )
                    delete.assert_called_once()


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _ledger(item, exported_hash: str) -> VaultProjection:
    return VaultProjection(
        id=f"export-{item.kind}",
        paper_id="paper-01",
        artifact_id=item.artifact_id,
        target_path=item.path,
        source_hash=item.source_hash,
        exported_hash=exported_hash,
        status="exported",
        exported_at=NOW,
        error_message=None,
    )


if __name__ == "__main__":
    unittest.main()
