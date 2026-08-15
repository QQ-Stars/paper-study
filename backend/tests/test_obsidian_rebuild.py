from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.application.obsidian_projection import (
    ExportOptions,
    ProjectionPlan,
    build_projection_plan,
)
from backend.tests.test_obsidian_layout import _snapshot


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class _MemoryExports:
    def __init__(self) -> None:
        self.rows = {}

    async def find_by_target_path(self, path: str):
        return self.rows.get(path)

    async def upsert(self, projection):
        previous = self.rows.get(projection.target_path)
        if previous is not None and projection.status not in {"exported", "unchanged"}:
            projection = type(projection)(
                id=previous.id,
                paper_id=projection.paper_id,
                artifact_id=projection.artifact_id,
                target_path=projection.target_path,
                source_hash=projection.source_hash,
                exported_hash=previous.exported_hash,
                status=projection.status,
                exported_at=previous.exported_at,
                error_message=projection.error_message,
            )
        self.rows[projection.target_path] = projection
        return projection

    async def find_cleanup_projection(
        self, *, paper_id: str, target_path: str, source_hash: str, exported_hash: str
    ):
        row = self.rows.get(target_path)
        if row is None:
            return None
        if (
            row.paper_id,
            row.source_hash,
            row.exported_hash,
            row.status,
        ) != (paper_id, source_hash, exported_hash, row.status) or row.status not in {
            "exported",
            "unchanged",
        }:
            return None
        return row

    async def delete_if_matches(self, projection) -> bool:
        current = self.rows.get(projection.target_path)
        if current != projection:
            return False
        del self.rows[projection.target_path]
        return True


class ObsidianRebuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_change_updates_managed_bytes_without_moving_paths(self) -> None:
        from backend.app.application.obsidian_exporter import ObsidianBatchExporter
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot

        initial = build_projection_plan(_snapshot(paper_id="paper-1"), ExportOptions())
        changed = build_projection_plan(
            _snapshot(
                paper_id="paper-1",
                title="Renamed research paper",
                paper_source_hash="e" * 64,
                translation=None,
            ),
            ExportOptions(),
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-stable-path-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            repository = _MemoryExports()
            with BoundVaultRoot.open(vault) as bound:
                exporter = ObsidianBatchExporter(
                    bound, repository, root_folder="Research", now=lambda: NOW
                )
                await exporter.export((initial,))
                note = vault / "Research" / "Notes" / "paper-1.md"
                note.write_bytes(b"user note edit\n")
                old_paths = tuple(sorted(_relative_files(vault / "Research")))
                with (
                    patch("os.rename", side_effect=AssertionError("rename forbidden")),
                    patch("os.replace", side_effect=AssertionError("replace forbidden")),
                ):
                    result = await exporter.export((changed,))

            self.assertEqual(old_paths, tuple(sorted(_relative_files(vault / "Research"))))
            self.assertIn(
                b"Renamed research paper",
                (vault / "Research" / "Papers" / "paper-1.md").read_bytes(),
            )
            self.assertEqual(b"user note edit\n", note.read_bytes())
            self.assertTrue(
                (vault / "Research" / "Translations" / "paper-1.md").is_file()
            )
            self.assertIn(
                "Translations/paper-1.md",
                tuple(item.path for item in result.cleanup.items),
            )

    async def test_cleanup_requires_matching_plan_sha_and_three_proofs(self) -> None:
        from backend.app.application.obsidian_exporter import (
            CleanupPlanMismatchError,
            ObsidianBatchExporter,
        )
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot

        initial = build_projection_plan(_snapshot(paper_id="paper-1"), ExportOptions())
        desired = ProjectionPlan(
            paper_id="paper-1",
            files=tuple(item for item in initial.files if item.kind in {"paper", "source", "note"}),
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-cleanup-plan-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            repository = _MemoryExports()
            with BoundVaultRoot.open(vault) as bound:
                exporter = ObsidianBatchExporter(
                    bound, repository, root_folder="Research", now=lambda: NOW
                )
                await exporter.export((initial,))
                preview = await exporter.preview_cleanup((desired,))
                before = _tree(vault)
                for confirmation in (None, "0" * 64):
                    with self.subTest(confirmation=confirmation):
                        with self.assertRaises(CleanupPlanMismatchError):
                            await exporter.apply_cleanup((desired,), confirmation)
                        self.assertEqual(before, _tree(vault))

                def race(proof) -> None:
                    if proof.entry.kind == "explainer":
                        target = vault / "Research" / proof.entry.path
                        target.write_bytes(target.read_bytes() + b"user race\n")

                applied = await exporter.apply_cleanup(
                    (desired,), preview.sha256, before_delete=race
                )

            self.assertEqual(1, applied.deleted)
            self.assertEqual(1, applied.conflicts)
            self.assertTrue(
                (vault / "Research" / "Explainers" / "paper-1.md").is_file()
            )
            self.assertFalse(
                (vault / "Research" / "Translations" / "paper-1.md").exists()
            )
            self.assertTrue((vault / "Research" / "Notes" / "paper-1.md").is_file())

    async def test_empty_vault_rebuild_matches_incremental_managed_hashes(self) -> None:
        from backend.app.application.obsidian_exporter import ObsidianBatchExporter
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import parse_manifest

        plan = build_projection_plan(_snapshot(paper_id="paper-1"), ExportOptions())
        with tempfile.TemporaryDirectory(prefix="study-app-p5-rebuild-equivalence-") as temp:
            root = Path(temp)
            snapshots = []
            for mode in ("full", "incremental"):
                vault = root / mode
                vault.mkdir()
                repository = _MemoryExports()
                with BoundVaultRoot.open(vault) as bound:
                    exporter = ObsidianBatchExporter(
                        bound, repository, root_folder="Research", now=lambda: NOW
                    )
                    if mode == "incremental":
                        await exporter.export(
                            (ProjectionPlan(plan.paper_id, (plan.files[0],)),)
                        )
                    await exporter.export((plan,))
                manifest = parse_manifest(
                    (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                )
                snapshots.append(
                    (
                        tuple(manifest.entries),
                        {
                            path: data
                            for path, data in _tree(vault / "Research").items()
                            if path != ".paper-study/manifest.json"
                        },
                    )
                )
            self.assertEqual(snapshots[0], snapshots[1])

    async def test_rebuild_carries_forward_orphan_and_user_note_entries(self) -> None:
        from backend.app.application.obsidian_exporter import ObsidianBatchExporter
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import parse_manifest

        initial = build_projection_plan(_snapshot(paper_id="paper-1"), ExportOptions())
        desired = ProjectionPlan(
            initial.paper_id,
            tuple(item for item in initial.files if item.kind in {"paper", "source"}),
        )
        with tempfile.TemporaryDirectory(prefix="study-app-p5-rebuild-carry-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            repository = _MemoryExports()
            with BoundVaultRoot.open(vault) as bound:
                exporter = ObsidianBatchExporter(
                    bound, repository, root_folder="Research", now=lambda: NOW
                )
                await exporter.export((initial,))
                note = vault / "Research" / "Notes" / "paper-1.md"
                note.write_bytes(b"user-owned forever\n")
                del repository.rows["Translations/paper-1.md"]
                before = parse_manifest(
                    (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                )
                await exporter.export((desired,))
                after = parse_manifest(
                    (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                )

            before_by_path = {entry.path: entry for entry in before.entries}
            after_by_path = {entry.path: entry for entry in after.entries}
            for path in ("Notes/paper-1.md", "Translations/paper-1.md"):
                self.assertEqual(before_by_path[path], after_by_path[path])
            self.assertEqual(b"user-owned forever\n", note.read_bytes())


def _relative_files(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
