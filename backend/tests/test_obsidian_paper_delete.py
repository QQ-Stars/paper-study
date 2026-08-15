from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import text

from backend.app.application.obsidian_projection import ExportOptions, build_projection_plan
from backend.tests.test_obsidian_layout import _snapshot
from backend.tests.support.p3_database import p3_database_fixture


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class ObsidianPaperDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_delete_cascades_only_ledger_and_leaves_vault_orphan(self) -> None:
        from backend.app.application.paper_library import PaperLibrary
        from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
        from backend.app.providers.obsidian_vault import (
            ObsidianCleanupPlanner,
            ObsidianProjectionPublisher,
            parse_manifest,
        )
        from backend.app.repositories.obsidian_exports import (
            SqlAlchemyObsidianExportsRepository,
        )
        from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork

        async with p3_database_fixture(
            prefix="study-app-p5-paper-delete-"
        ) as fixture:
            repository = SqlAlchemyObsidianExportsRepository(fixture.session_factory)
            paper = next(
                item
                for item in build_projection_plan(
                    _snapshot(paper_id="paper-1"), ExportOptions()
                ).files
                if item.kind == "paper"
            )
            with tempfile.TemporaryDirectory(prefix="study-app-p5-delete-vault-") as temp:
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
                before = _tree(vault)
                manifest_path = vault / "Research" / ".paper-study" / "manifest.json"
                manifest = parse_manifest(manifest_path.read_bytes())
                entry = next(item for item in manifest.entries if item.path == paper.path)

                class PdfFiles:
                    calls = []

                    async def delete_for_paper(self, paper_id: str) -> None:
                        self.calls.append(paper_id)

                pdf_files = PdfFiles()
                library = PaperLibrary(
                    lambda: SqlAlchemyUnitOfWork(fixture.session_factory),
                    pdf_files=pdf_files,
                )
                with patch.object(
                    BoundVaultRoot,
                    "open",
                    side_effect=AssertionError("Paper delete attempted Vault I/O"),
                ) as vault_open:
                    await library.delete("paper-1")
                vault_open.assert_not_called()

                async with fixture.session_factory() as session:
                    paper_count = (
                        await session.execute(
                            text("SELECT count(*) FROM papers WHERE id='paper-1'")
                        )
                    ).scalar_one()
                    ledger_count = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM obsidian_exports WHERE paper_id='paper-1'"
                            )
                        )
                    ).scalar_one()
                self.assertEqual((0, 0), (paper_count, ledger_count))
                self.assertEqual(["paper-1"], pdf_files.calls)
                self.assertEqual(before, _tree(vault))

                with BoundVaultRoot.open(vault) as bound:
                    inventory = await ObsidianCleanupPlanner(
                        bound,
                        repository,
                        root_folder="Research",
                    ).classify((entry,))
                self.assertEqual((entry,), inventory.orphaned)
                self.assertEqual((), inventory.deletable)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
