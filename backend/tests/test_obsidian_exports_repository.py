from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import text

from backend.app.application.obsidian_projection import ExportOptions, build_projection_plan
from backend.app.domain import VaultProjection
from backend.tests.test_obsidian_layout import _snapshot
from backend.tests.support.p3_database import p3_database_fixture


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class ObsidianExportsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_migration_repository_uses_exact_cas_and_restore(self) -> None:
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )

        async with p3_database_fixture(
            prefix="study-app-p5-pdf-migration-repository-"
        ) as fixture:
            async with fixture.session_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO papers (id, source, title, title_norm, pdf_path) "
                        "VALUES (:id, 'manual', :title, :title_norm, :pdf_path)"
                    ),
                    [
                        {
                            "id": "paper-b",
                            "title": "B",
                            "title_norm": "b",
                            "pdf_path": "old-b.pdf",
                        },
                        {
                            "id": "paper-a",
                            "title": "A",
                            "title_norm": "a",
                            "pdf_path": None,
                        },
                    ],
                )
                await session.commit()

            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            self.assertEqual(
                [
                    {"id": "paper-1", "pdf_path": None},
                    {"id": "paper-2", "pdf_path": None},
                    {"id": "paper-a", "pdf_path": None},
                    {"id": "paper-b", "pdf_path": "old-b.pdf"},
                ],
                await repository.list_papers_for_pdf_migration(),
            )
            self.assertIsNone(await repository.get_paper_pdf_path("paper-a"))
            self.assertFalse(
                await repository.compare_and_set_paper_pdf_path(
                    "paper-b",
                    expected="wrong.pdf",
                    replacement="new-b.pdf",
                )
            )
            self.assertTrue(
                await repository.compare_and_set_paper_pdf_path(
                    "paper-b",
                    expected="old-b.pdf",
                    replacement="new-b.pdf",
                )
            )
            self.assertEqual(
                "new-b.pdf", await repository.get_paper_pdf_path("paper-b")
            )

            expected = VaultProjection(
                id="migration-paper-b",
                paper_id="paper-b",
                artifact_id=None,
                target_path="Attachments/PDF/paper-b.pdf",
                source_hash="a" * 64,
                exported_hash="a" * 64,
                status="exported",
                exported_at=NOW,
                error_message=None,
            )
            prior = VaultProjection(
                id="prior-paper-b",
                paper_id="paper-b",
                artifact_id=None,
                target_path="Attachments/PDF/paper-b.pdf",
                source_hash="b" * 64,
                exported_hash="b" * 64,
                status="exported",
                exported_at=NOW - timedelta(minutes=1),
                error_message=None,
            )
            await repository.upsert(expected)
            self.assertTrue(
                await repository.restore_projection(expected=expected, prior=prior)
            )
            self.assertEqual(
                prior,
                await repository.find_by_target_path(prior.target_path),
            )
            self.assertFalse(
                await repository.restore_projection(expected=expected, prior=None)
            )
            self.assertTrue(
                await repository.restore_projection(expected=prior, prior=None)
            )
            self.assertIsNone(
                await repository.find_by_target_path(prior.target_path)
            )

    async def test_upsert_and_conflict_preserve_exported_hash(self) -> None:
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )

        async with p3_database_fixture(
            prefix="study-app-p5-obsidian-ledger-"
        ) as fixture:
            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            await repository.upsert(
                VaultProjection(
                    id="export-paper-1",
                    paper_id="paper-1",
                    artifact_id=None,
                    target_path="Papers/paper-1.md",
                    source_hash="a" * 64,
                    exported_hash="b" * 64,
                    status="exported",
                    exported_at=NOW,
                    error_message=None,
                )
            )
            await repository.upsert(
                VaultProjection(
                    id="replacement-id-must-not-win",
                    paper_id="paper-1",
                    artifact_id=None,
                    target_path="Papers/paper-1.md",
                    source_hash="c" * 64,
                    exported_hash="d" * 64,
                    status="conflict",
                    exported_at=NOW + timedelta(minutes=1),
                    error_message="Managed target changed.",
                )
            )

            row = await repository.find_by_target_path("Papers/paper-1.md")

        self.assertEqual(
            VaultProjection(
                id="export-paper-1",
                paper_id="paper-1",
                artifact_id=None,
                target_path="Papers/paper-1.md",
                source_hash="c" * 64,
                exported_hash="b" * 64,
                status="conflict",
                exported_at=NOW,
                error_message="Managed target changed.",
            ),
            row,
        )

    async def test_reconciles_published_file_after_ledger_crash(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import (
            ObsidianProjectionPublisher,
            parse_manifest,
        )
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )

        paper, source = (
            item
            for item in build_projection_plan(
                _snapshot(paper_id="paper-1"), ExportOptions()
            ).files
            if item.kind in {"paper", "source"}
        )
        async with p3_database_fixture(
            prefix="study-app-p5-obsidian-reconcile-file-"
        ) as fixture:
            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-reconcile-") as temp:
                vault = Path(temp) / "vault"
                vault.mkdir()
                fail_next = True

                def fail_once() -> None:
                    nonlocal fail_next
                    if fail_next:
                        fail_next = False
                        raise RuntimeError("simulated ledger commit failure")

                with BoundVaultRoot.open(vault) as bound:
                    publisher = ObsidianProjectionPublisher(
                        bound,
                        repository,
                        root_folder="Research",
                        now=lambda: NOW,
                        before_ledger=fail_once,
                    )
                    with self.assertRaisesRegex(RuntimeError, "ledger commit"):
                        await publisher.publish(paper)
                    target = vault / "Research" / paper.path
                    first_identity = bound.inspect_target(
                        publisher._writer._relative(paper.path)
                    ).identity
                    self.assertIsNone(await repository.find_by_target_path(paper.path))

                    with patch.object(
                        bound, "publish_new", wraps=bound.publish_new
                    ) as publish_new:
                        recovered = await publisher.publish(paper)
                    repeated_file_publishes = [
                        call
                        for call in publish_new.call_args_list
                        if call.args[0].value.endswith(paper.path)
                    ]
                    self.assertEqual([], repeated_file_publishes)
                    self.assertEqual(first_identity, recovered.target)
                    self.assertIsNotNone(await repository.find_by_target_path(paper.path))
                    manifest = parse_manifest(
                        (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                    )
                    self.assertEqual((paper.path,), tuple(entry.path for entry in manifest.entries))

                    fail_next = True
                    with self.assertRaisesRegex(RuntimeError, "ledger commit"):
                        await publisher.publish(source)
                    source_target = vault / "Research" / source.path
                    user_bytes = source_target.read_bytes() + b"\nuser edit\n"
                    source_target.write_bytes(user_bytes)
                    conflicted = await publisher.publish(source)

                self.assertEqual("conflict", conflicted.status)
                self.assertEqual(user_bytes, source_target.read_bytes())
                manifest = parse_manifest(
                    (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                )
                self.assertNotIn(source.path, {entry.path for entry in manifest.entries})

    async def test_reconciles_ledger_after_manifest_crash(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            ObsidianVaultError,
        )
        from backend.app.providers.obsidian_vault import (
            ManifestEntry,
            ObsidianProjectionPublisher,
            merge_manifest,
            parse_manifest,
            serialize_manifest,
        )
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )

        paper, source = (
            item
            for item in build_projection_plan(
                _snapshot(paper_id="paper-1"), ExportOptions()
            ).files
            if item.kind in {"paper", "source"}
        )
        async with p3_database_fixture(
            prefix="study-app-p5-obsidian-reconcile-manifest-"
        ) as fixture:
            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            with tempfile.TemporaryDirectory(
                prefix="study-app-p5-vault-manifest-reconcile-"
            ) as temp:
                vault = Path(temp) / "vault"
                vault.mkdir()
                fail_next = True

                def fail_once() -> None:
                    nonlocal fail_next
                    if fail_next:
                        fail_next = False
                        raise RuntimeError("simulated manifest publication failure")

                with BoundVaultRoot.open(vault) as bound:
                    publisher = ObsidianProjectionPublisher(
                        bound,
                        repository,
                        root_folder="Research",
                        now=lambda: NOW,
                        before_manifest=fail_once,
                    )
                    with self.assertRaisesRegex(RuntimeError, "manifest publication"):
                        await publisher.publish(paper)
                    target = vault / "Research" / paper.path
                    target_bytes = target.read_bytes()
                    ledger_before = await repository.find_by_target_path(paper.path)
                    self.assertIsNotNone(ledger_before)
                    manifest_path = vault / "Research" / ".paper-study" / "manifest.json"
                    self.assertFalse(manifest_path.exists())

                    with (
                        patch.object(bound, "publish_new", wraps=bound.publish_new) as publish_new,
                        patch.object(repository, "upsert", wraps=repository.upsert) as upsert,
                    ):
                        await publisher.publish(paper)
                    self.assertEqual(target_bytes, target.read_bytes())
                    self.assertEqual(ledger_before, await repository.find_by_target_path(paper.path))
                    upsert.assert_not_awaited()
                    self.assertEqual(
                        [],
                        [
                            call
                            for call in publish_new.call_args_list
                            if call.args[0].value.endswith(paper.path)
                        ],
                    )

                    competitor_bytes: bytes | None = None

                    def race_manifest() -> None:
                        nonlocal competitor_bytes
                        current = parse_manifest(manifest_path.read_bytes())
                        competitor = ManifestEntry(
                            path="Notes/user-owned.md",
                            kind="note",
                            paper_id="user-owned",
                            artifact_id=None,
                            ownership="user",
                            source_hash="c" * 64,
                            exported_hash="d" * 64,
                        )
                        competitor_bytes = serialize_manifest(
                            merge_manifest(current, (competitor,), generated_at=NOW + timedelta(seconds=1))
                        )
                        manifest_path.write_bytes(competitor_bytes)

                    racing = ObsidianProjectionPublisher(
                        bound,
                        repository,
                        root_folder="Research",
                        now=lambda: NOW + timedelta(seconds=2),
                        before_manifest=race_manifest,
                    )
                    with self.assertRaises(ObsidianVaultError) as caught:
                        await racing.publish(source)
                    self.assertEqual("OBSIDIAN_TARGET_CHANGED", caught.exception.code)
                    self.assertEqual(competitor_bytes, manifest_path.read_bytes())

    async def test_missing_live_ledger_can_never_authorize_stale_cleanup(self) -> None:
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import (
            ObsidianCleanupPlanner,
            ObsidianProjectionPublisher,
            parse_manifest,
        )
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )

        paper = next(
            item
            for item in build_projection_plan(
                _snapshot(paper_id="paper-1"), ExportOptions()
            ).files
            if item.kind == "paper"
        )
        async with p3_database_fixture(
            prefix="study-app-p5-obsidian-orphan-"
        ) as fixture:
            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-orphan-") as temp:
                vault = Path(temp) / "vault"
                vault.mkdir()
                with BoundVaultRoot.open(vault) as bound:
                    publisher = ObsidianProjectionPublisher(
                        bound,
                        repository,
                        root_folder="Research",
                        now=lambda: NOW,
                    )
                    await publisher.publish(paper)
                    manifest_path = vault / "Research" / ".paper-study" / "manifest.json"
                    manifest_bytes = manifest_path.read_bytes()
                    manifest = parse_manifest(manifest_bytes)
                    entry = next(item for item in manifest.entries if item.path == paper.path)

                    async with fixture.session_factory() as session:
                        await session.execute(
                            text("DELETE FROM papers WHERE id = :paper_id"),
                            {"paper_id": "paper-1"},
                        )
                        await session.commit()
                    self.assertIsNone(await repository.find_by_target_path(paper.path))

                    planner = ObsidianCleanupPlanner(
                        bound,
                        repository,
                        root_folder="Research",
                    )
                    with (
                        patch.object(bound, "delete_managed", wraps=bound.delete_managed) as delete,
                        patch.object(repository, "upsert", wraps=repository.upsert) as upsert,
                    ):
                        inventory = await planner.classify((entry,))

                    self.assertEqual((entry,), inventory.orphaned)
                    self.assertEqual((), inventory.deletable)
                    delete.assert_not_called()
                    upsert.assert_not_awaited()
                    self.assertEqual(manifest_bytes, manifest_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
