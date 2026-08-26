from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.application.settings import (
    ObsidianSettings,
    SettingsService,
    SettingsValidationError,
)
from backend.app.domain import CredentialKind, CredentialStatus


class _CredentialService:
    async def status(self, kind: CredentialKind) -> CredentialStatus:
        return CredentialStatus(
            kind=kind, has_key=False, key_tail=None, environment_managed=False
        )


class ObsidianSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_unwritable_vault_fail_before_enqueue(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            ObsidianVaultError,
        )
        from backend.app.providers.obsidian_vault import probe_obsidian_vault
        from backend.app.workers.obsidian import (
            ObsidianJobService,
            ObsidianVaultAccessError,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-access-") as temp:
            root = Path(temp)
            missing = root / "missing"
            self.assertEqual(
                "OBSIDIAN_VAULT_NOT_FOUND",
                probe_obsidian_vault(str(missing)).code,
            )
            self.assertFalse(missing.exists())

            ordinary_file = root / "not-a-vault"
            ordinary_file.write_bytes(b"sentinel\n")
            self.assertEqual(
                "OBSIDIAN_VAULT_NOT_DIRECTORY",
                probe_obsidian_vault(str(ordinary_file)).code,
            )

            physical = root / "physical"
            physical.mkdir()
            link = root / "linked-vault"
            try:
                link.symlink_to(physical, target_is_directory=True)
            except OSError:
                pass
            else:
                self.assertEqual(
                    "OBSIDIAN_PATH_ESCAPE",
                    probe_obsidian_vault(str(link)).code,
                )

            vault = root / "vault"
            vault.mkdir()
            with patch.object(
                BoundVaultRoot,
                "publish_new",
                side_effect=PermissionError("denied"),
            ):
                self.assertEqual(
                    "OBSIDIAN_VAULT_NOT_WRITABLE",
                    probe_obsidian_vault(str(vault)).code,
                )
            with patch.object(
                BoundVaultRoot,
                "open",
                side_effect=ObsidianVaultError(
                    "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
                    "unsupported",
                ),
            ):
                self.assertEqual(
                    "OBSIDIAN_ATOMIC_PRIMITIVE_UNAVAILABLE",
                    probe_obsidian_vault(str(vault)).code,
                )

            before = tuple(vault.iterdir())
            self.assertTrue(probe_obsidian_vault(str(vault)).ok)
            self.assertEqual(before, tuple(vault.iterdir()))

            settings = self._service(
                root / "settings.json",
                root,
                {
                    "OBSIDIAN_ENABLED": "1",
                    "OBSIDIAN_VAULT_PATH": str(missing.resolve()),
                },
            )
            work_calls: list[str] = []
            query_calls: list[str] = []

            def work_factory():
                work_calls.append("work")
                raise AssertionError("queue/ledger access occurred before Vault guard")

            class Queries:
                async def list_papers(self):
                    query_calls.append("library")
                    return []

            jobs = ObsidianJobService(
                work_factory,
                settings_service=settings,
                library_queries=Queries(),
            )
            operations = (
                jobs.test_access(),
                jobs.enqueue_export("paper-1", dry_run=False),
                jobs.enqueue_sync(
                    dry_run=False,
                    apply_cleanup=False,
                    cleanup_plan_sha=None,
                ),
            )
            for operation in operations:
                with self.assertRaises(ObsidianVaultAccessError) as caught:
                    await operation
                self.assertEqual("OBSIDIAN_VAULT_NOT_FOUND", caught.exception.code)
            self.assertEqual([], work_calls)
            self.assertEqual([], query_calls)

    async def test_eight_fields_have_exact_priority_defaults_and_validation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-p5-settings-") as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            defaults = self._service(settings_path, root, {})
            self.assertEqual(
                ObsidianSettings(
                    enabled=False,
                    vault_path="",
                    root_folder="Research",
                    pdf_mode="none",
                    export_source=True,
                    export_explainer=True,
                    export_translation=True,
                    auto_export=False,
                ),
                await defaults.obsidian(),
            )
            self.assertFalse(settings_path.exists())

            file_vault = str((root / "file-vault").resolve())
            env_vault = str((root / "env-vault").resolve())
            settings_path.write_text(
                json.dumps(
                    {
                        "obsidianEnabled": False,
                        "obsidianVaultPath": file_vault,
                        "obsidianRootFolder": "FileRoot",
                        "obsidianPdfMode": "reference",
                        "obsidianExportSource": False,
                        "obsidianExportExplainer": False,
                        "obsidianExportTranslation": False,
                        "obsidianAutoExport": False,
                    }
                ),
                encoding="utf-8",
            )
            from_file = self._service(settings_path, root, {})
            self.assertEqual(file_vault, (await from_file.obsidian()).vault_path)

            environment = {
                "OBSIDIAN_ENABLED": "1",
                "OBSIDIAN_VAULT_PATH": env_vault,
                "OBSIDIAN_ROOT_FOLDER": "Env/Nested",
                "OBSIDIAN_PDF_MODE": "copy",
                "OBSIDIAN_EXPORT_SOURCE": "1",
                "OBSIDIAN_EXPORT_EXPLAINER": "1",
                "OBSIDIAN_EXPORT_TRANSLATION": "1",
                "OBSIDIAN_AUTO_EXPORT": "1",
            }
            effective = await self._service(
                settings_path, root, environment
            ).obsidian()
            self.assertEqual(
                ObsidianSettings(
                    enabled=True,
                    vault_path=env_vault,
                    root_folder="Env/Nested",
                    pdf_mode="copy",
                    export_source=True,
                    export_explainer=True,
                    export_translation=True,
                    auto_export=True,
                ),
                effective,
            )

            for invalid in ("", "true", "yes", "2"):
                with self.subTest(environment_boolean=invalid):
                    service = self._service(
                        settings_path, root, {"OBSIDIAN_ENABLED": invalid}
                    )
                    with self.assertRaises(SettingsValidationError):
                        await service.obsidian()

            invalid_documents = (
                {"obsidianEnabled": 1},
                {"obsidianPdfMode": "embed"},
                {"obsidianVaultPath": "relative/vault"},
                {"obsidianRootFolder": ""},
                {"obsidianRootFolder": "."},
                {"obsidianRootFolder": "Research/../Other"},
                {"obsidianRootFolder": "Research\\Other"},
                {"obsidianRootFolder": "Research//Nested"},
                {"obsidianRootFolder": "C:/Research"},
                {"obsidianRootFolder": "Research/\x00bad"},
            )
            for document in invalid_documents:
                with self.subTest(document=document):
                    settings_path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(SettingsValidationError):
                        await self._service(settings_path, root, {}).obsidian()

            settings_path.write_text("{}", encoding="utf-8")
            authoritative = self._service(
                settings_path, root, {"OBSIDIAN_PDF_MODE": "none"}
            )
            with self.assertRaises(SettingsValidationError):
                await authoritative.update({"obsidianPdfMode": "invalid"})

    async def test_obsidian_save_preserves_credentials_unknown_fields_and_never_moves_pdf(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="study-app-p5-settings-save-") as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            pdf_root = root / "pdfs"
            pdf_root.mkdir()
            pdf_path = pdf_root / "paper-1.pdf"
            pdf_bytes = b"%PDF-1.4\nP5 settings sentinel\n"
            pdf_path.write_bytes(pdf_bytes)
            vault = root / "vault"
            initial = {
                "apiKey": "llm-secret",
                "ocrApiKey": "ocr-secret",
                "embedApiKey": "embed-secret",
                "s2ApiKey": "s2-secret",
                "obsidianEnabled": False,
                "obsidianVaultPath": str(vault.resolve()),
                "obsidianRootFolder": "Research",
                "obsidianPdfMode": "none",
                "obsidianExportSource": True,
                "obsidianExportExplainer": True,
                "obsidianExportTranslation": True,
                "obsidianAutoExport": False,
                "pdfDir": str(pdf_root.resolve()),
                "unknownNested": {"preserve": [1, 2, 3]},
            }
            settings_path.write_text(json.dumps(initial), encoding="utf-8")
            service = self._service(settings_path, root, {})

            await service.update(
                {
                    "obsidianEnabled": True,
                    "obsidianRootFolder": "Research/Papers",
                    "obsidianPdfMode": "reference",
                    "obsidianAutoExport": True,
                }
            )

            persisted = json.loads(settings_path.read_text(encoding="utf-8"))
            for key in ("apiKey", "ocrApiKey", "embedApiKey", "s2ApiKey"):
                self.assertEqual(initial[key], persisted[key])
            self.assertEqual(initial["unknownNested"], persisted["unknownNested"])
            self.assertEqual(pdf_bytes, pdf_path.read_bytes())
            self.assertFalse(vault.exists())
            self.assertEqual([pdf_path], list(pdf_root.iterdir()))

    @staticmethod
    def _service(
        settings_path: Path,
        root: Path,
        environment: dict[str, str],
    ) -> SettingsService:
        return SettingsService(
            settings_path=settings_path,
            root=root,
            credential_service=_CredentialService(),
            environment_snapshot=environment,
            default_dirs={
                "pdfDir": root / "pdfs",
                "explainerDir": root / "explainers",
                "translationDir": root / "translations",
                "ocrMarkdownDir": root / "ocr_markdown",
            },
        )


if __name__ == "__main__":
    unittest.main()
