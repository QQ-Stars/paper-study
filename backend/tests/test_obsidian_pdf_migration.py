from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from backend.app.application.obsidian_pdf_migration import (
    ObsidianPdfMigration,
    PdfMigrationError,
)
from backend.app.cli.obsidian_pdf_migration import run as run_cli
from backend.app.infrastructure.bound_vault_root import BoundVaultRoot
from backend.app.providers.obsidian_vault import parse_manifest
from backend.app.providers.pdf_files import PdfFiles
from backend.tests.support.p3_database import p3_database_fixture


NOW = datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc)


class _MigrationState:
    def __init__(self, papers: list[dict[str, object]]) -> None:
        self.papers = {str(row["id"]): dict(row) for row in papers}
        self.ledgers: dict[str, object] = {}
        self.mutations: list[str] = []
        self.event_sink: list[str] | None = None

    def _record(self, value: str) -> None:
        self.mutations.append(value)
        if self.event_sink is not None:
            self.event_sink.append(value)

    async def list_papers_for_pdf_migration(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.papers.values()]

    async def get_paper_pdf_path(self, paper_id: str) -> str | None:
        value = self.papers[paper_id].get("pdf_path")
        return str(value) if value is not None else None

    async def compare_and_set_paper_pdf_path(
        self,
        paper_id: str,
        *,
        expected: str | None,
        replacement: str | None,
    ) -> bool:
        self._record(f"db:{paper_id}")
        if self.papers[paper_id].get("pdf_path") != expected:
            return False
        self.papers[paper_id]["pdf_path"] = replacement
        return True

    async def find_by_target_path(self, target_path: str):
        return self.ledgers.get(target_path)

    async def upsert(self, projection):
        self._record(f"ledger:{projection.paper_id}")
        self.ledgers[projection.target_path] = projection
        return projection

    async def restore_projection(self, *, expected, prior) -> bool:
        self._record(f"ledger-restore:{expected.paper_id}")
        current = self.ledgers.get(expected.target_path)
        if current != expected:
            return False
        if prior is None:
            self.ledgers.pop(expected.target_path, None)
        else:
            self.ledgers[expected.target_path] = prior
        return True


class _GuardedPdfFiles(PdfFiles):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.materialize_calls = 0
        self.ocr_calls = 0
        self.queue_calls = 0

    def materialize(self, *_args: object, **_kwargs: object) -> None:
        self.materialize_calls += 1
        raise AssertionError("PDF migration must not materialize a source")

    def ocr(self, *_args: object, **_kwargs: object) -> None:
        self.ocr_calls += 1
        raise AssertionError("PDF migration must not call OCR")

    def enqueue(self, *_args: object, **_kwargs: object) -> None:
        self.queue_calls += 1
        raise AssertionError("PDF migration must not enqueue work")


class ObsidianPdfMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_cli_plan_composes_from_explicit_temporary_paths(self) -> None:
        async with p3_database_fixture(
            prefix="study-app-p5-pdf-migration-cli-"
        ) as fixture:
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                pdf_directory = base / "pdfs"
                pdf_directory.mkdir()
                for paper_id in ("paper-1", "paper-2"):
                    (pdf_directory / f"{paper_id}.pdf").write_bytes(
                        f"%PDF-1.7\n{paper_id}\n%%EOF\n".encode("ascii")
                    )
                vault = base / "vault"
                vault.mkdir()
                settings_path = base / "settings.json"
                settings_path.write_text(
                    json.dumps(
                        {
                            "obsidianVaultPath": str(vault),
                            "obsidianRootFolder": "Research",
                            "pdfDir": str(pdf_directory),
                        }
                    ),
                    encoding="utf-8",
                )
                stdout = StringIO()

                exit_code = await run_cli(
                    ["plan"],
                    environment={
                        "DB_PATH": str(fixture.database_path),
                        "SETTINGS_PATH": str(settings_path),
                    },
                    stdout=stdout,
                )

                plan = json.loads(stdout.getvalue())
                self.assertEqual(0, exit_code)
                self.assertEqual(1, plan["schemaVersion"])
                self.assertEqual(
                    ["paper-1", "paper-2"],
                    [item["paperId"] for item in plan["items"]],
                )
                self.assertEqual(64, len(plan["settingsFingerprint"]))
                self.assertFalse((vault / "Research").exists())

    async def test_plan_is_canonical_and_dry_run_has_zero_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "source pdfs"
            pdf_directory.mkdir()
            paper_b = pdf_directory / "paper-b.pdf"
            paper_a = pdf_directory / "paper-a.pdf"
            paper_b.write_bytes(b"%PDF-1.7\npaper-b\n%%EOF\n")
            paper_a.write_bytes(b"%PDF-1.7\npaper-a\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            state = _MigrationState(
                [
                    {"id": "paper-b", "pdf_path": str(paper_b)},
                    {"id": "paper-a", "pdf_path": str(paper_a)},
                ]
            )
            pdf_files = _GuardedPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )

            with BoundVaultRoot.open(vault) as root:
                migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                )
                first = await migration.plan()
                second = await migration.plan()
                stdout = StringIO()
                exit_code = await run_cli(
                    ["plan"],
                    migration=migration,
                    stdout=stdout,
                )

            self.assertEqual(0, exit_code)
            self.assertEqual(first.canonical_bytes, second.canonical_bytes)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(
                hashlib.sha256(first.canonical_bytes).hexdigest(),
                first.sha256,
            )
            self.assertEqual(first.canonical_bytes.decode("utf-8"), stdout.getvalue())
            self.assertEqual(
                first.canonical_bytes,
                (
                    json.dumps(
                        first.document,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8"),
            )
            self.assertEqual(
                ["paper-a", "paper-b"],
                [item["paperId"] for item in first.document["items"]],
            )
            self.assertEqual(
                [
                    "Attachments/PDF/paper-a.pdf",
                    "Attachments/PDF/paper-b.pdf",
                ],
                [item["targetPath"] for item in first.document["items"]],
            )
            for item in first.document["items"]:
                self.assertEqual(False, item["targetPrior"]["exists"])
                self.assertIsNone(item["targetPrior"]["identity"])
                self.assertIsNone(item["targetPrior"]["sha256"])
                self.assertIsNone(item["priorLedger"])
                self.assertIsNone(item["priorLedgerHash"])
                self.assertIsNone(item["priorManifestEntry"])
                self.assertIsNone(item["priorManifestEntryHash"])
                self.assertEqual(64, len(item["sourceSha256"]))
                self.assertTrue(Path(item["sourcePath"]).is_absolute())
            self.assertEqual([], state.mutations)
            self.assertEqual(
                (0, 0, 0),
                (
                    pdf_files.materialize_calls,
                    pdf_files.ocr_calls,
                    pdf_files.queue_calls,
                ),
            )
            self.assertFalse((vault / "Research").exists())

    async def test_prepare_publishes_exclusive_fsynced_intent_before_any_app_mutation(
        self,
    ) -> None:
        class BarrierCrash(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-1.pdf"
            source.write_bytes(b"%PDF-1.7\nprepare\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            intents = base / "intents"
            intents.mkdir()
            state = _MigrationState(
                [{"id": "paper-1", "pdf_path": str(source)}]
            )
            pdf_files = _GuardedPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )

            with BoundVaultRoot.open(vault) as root:
                for barrier_name in (
                    "intent_before_open",
                    "intent_before_write",
                    "intent_before_flush",
                    "intent_before_file_fsync",
                    "intent_before_parent_fsync",
                ):
                    with self.subTest(barrier=barrier_name):
                        intent_path = intents / f"{barrier_name}.json"

                        def crash(name: str, _context: dict[str, object]) -> None:
                            if name == barrier_name:
                                raise BarrierCrash(name)

                        migration = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                            barrier=crash,
                        )
                        plan = await migration.plan()
                        with self.assertRaises(BarrierCrash):
                            await migration.prepare(
                                confirm_plan_sha=plan.sha256,
                                intent_output=intent_path,
                            )
                        self.assertFalse(intent_path.exists())
                        self.assertEqual([], state.mutations)
                        self.assertFalse((vault / "Research").exists())

                migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                )
                plan = await migration.plan()
                competitor = intents / "competitor.json"
                competitor_bytes = b"competitor-owned\n"
                competitor.write_bytes(competitor_bytes)
                with self.assertRaises(PdfMigrationError) as occupied:
                    await migration.prepare(
                        confirm_plan_sha=plan.sha256,
                        intent_output=competitor,
                    )
                self.assertEqual("OBSIDIAN_INTENT_EXISTS", occupied.exception.code)
                self.assertEqual(competitor_bytes, competitor.read_bytes())

                with self.assertRaises(PdfMigrationError) as wrong_plan:
                    await migration.prepare(
                        confirm_plan_sha="0" * 64,
                        intent_output=intents / "wrong-plan.json",
                    )
                self.assertEqual(
                    "OBSIDIAN_MIGRATION_PLAN_SHA_MISMATCH",
                    wrong_plan.exception.code,
                )
                self.assertFalse((intents / "wrong-plan.json").exists())

                intent_path = intents / "migration-intent.json"
                stdout = StringIO()
                exit_code = await run_cli(
                    [
                        "prepare",
                        "--confirm-plan-sha",
                        plan.sha256,
                        "--intent-output",
                        str(intent_path),
                    ],
                    migration=migration,
                    stdout=stdout,
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertEqual(str(intent_path.resolve()), result["intentPath"])
            intent_bytes = intent_path.read_bytes()
            self.assertEqual(
                hashlib.sha256(intent_bytes).hexdigest(),
                result["intentSha256"],
            )
            intent = json.loads(intent_bytes)
            self.assertEqual(
                {
                    "createdAt",
                    "items",
                    "planSha256",
                    "receipt",
                    "schemaVersion",
                    "settingsFingerprint",
                    "state",
                    "updatedAt",
                },
                set(intent),
            )
            self.assertEqual(1, intent["schemaVersion"])
            self.assertEqual("prepared", intent["state"])
            self.assertEqual(plan.sha256, intent["planSha256"])
            self.assertIsNone(intent["receipt"])
            self.assertEqual(1, len(intent["items"]))
            self.assertEqual(
                {
                    "checkpoints",
                    "expectedPost",
                    "paperId",
                    "phase",
                    "prior",
                    "sequence",
                    "source",
                    "target",
                },
                set(intent["items"][0]),
            )
            self.assertEqual("prepared", intent["items"][0]["phase"])
            self.assertEqual([], state.mutations)
            self.assertFalse((vault / "Research").exists())

    async def test_prepare_binds_intent_parent_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-1.pdf"
            source.write_bytes(b"%PDF-1.7\nbound-intent-parent\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            intent_parent = base / "intents"
            intent_parent.mkdir()
            moved_parent = base / "intents-before-swap"
            target = intent_parent / "migration-intent.json"
            state = _MigrationState(
                [{"id": "paper-1", "pdf_path": str(source)}]
            )

            def swap_parent(name: str, _context: dict[str, object]) -> None:
                if name != "intent_before_open":
                    return
                try:
                    intent_parent.rename(moved_parent)
                except OSError as error:
                    raise PdfMigrationError(
                        "OBSIDIAN_INTENT_PARENT_CHANGED",
                        "The bound MigrationIntent parent rejected replacement.",
                    ) from error
                intent_parent.mkdir()

            with BoundVaultRoot.open(vault) as root:
                migration = ObsidianPdfMigration(
                    pdf_files=_GuardedPdfFiles(
                        root=base,
                        default_directory=pdf_directory,
                    ),
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                    barrier=swap_parent,
                )
                plan = await migration.plan()
                with self.assertRaises(PdfMigrationError):
                    await migration.prepare(
                        confirm_plan_sha=plan.sha256,
                        intent_output=target,
                    )

            self.assertFalse(target.exists())
            self.assertFalse((moved_parent / target.name).exists())
            self.assertEqual([], state.mutations)
            self.assertFalse((vault / "Research").exists())

    async def test_apply_copies_then_checkpoints_db_ledger_and_manifest_in_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "renamed-by-title.pdf"
            source_bytes = b"%PDF-1.7\nordered-apply\n%%EOF\n"
            source.write_bytes(source_bytes)
            vault = base / "vault"
            vault.mkdir()
            intents = base / "intents"
            intents.mkdir()
            state = _MigrationState(
                [{"id": "Raw.Paper-7", "pdf_path": str(source)}]
            )
            pdf_files = _GuardedPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )

            with BoundVaultRoot.open(vault) as root:
                prepare_migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                )
                plan = await prepare_migration.plan()
                intent_path = intents / "apply-intent.json"
                prepared = await prepare_migration.prepare(
                    confirm_plan_sha=plan.sha256,
                    intent_output=intent_path,
                )

                events: list[str] = []
                state.event_sink = events

                def record(name: str, context: dict[str, object]) -> None:
                    if name == "intent_checkpoint":
                        events.append(f"checkpoint:{context['phase']}")
                    elif name.startswith("apply_"):
                        events.append(name)

                apply_migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                    barrier=record,
                )
                stdout = StringIO()
                exit_code = await run_cli(
                    [
                        "apply",
                        "--intent",
                        str(prepared.intent_path),
                        "--confirm-intent-sha",
                        prepared.intent_sha256,
                    ],
                    migration=apply_migration,
                    stdout=stdout,
                )

            self.assertEqual(0, exit_code)
            result = json.loads(stdout.getvalue())
            self.assertEqual(str(intent_path.resolve()), result["intentPath"])
            self.assertEqual(
                hashlib.sha256(intent_path.read_bytes()).hexdigest(),
                result["intentSha256"],
            )
            self.assertEqual(
                [
                    "apply_target_published",
                    "checkpoint:target_published",
                    "db:Raw.Paper-7",
                    "checkpoint:db_updated",
                    "ledger:Raw.Paper-7",
                    "checkpoint:ledger_updated",
                    "apply_manifest_updated",
                    "checkpoint:manifest_updated",
                    "checkpoint:item_sealed",
                ],
                events,
            )
            target = (
                vault
                / "Research"
                / "Attachments"
                / "PDF"
                / "Raw.Paper-7.pdf"
            )
            self.assertEqual(source_bytes, target.read_bytes())
            self.assertEqual(source_bytes, source.read_bytes())
            self.assertEqual(
                str(target.resolve()),
                state.papers["Raw.Paper-7"]["pdf_path"],
            )
            ledger = state.ledgers["Attachments/PDF/Raw.Paper-7.pdf"]
            self.assertEqual("Raw.Paper-7", ledger.paper_id)
            self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), ledger.source_hash)
            manifest = parse_manifest(
                (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
            )
            self.assertEqual(1, len(manifest.entries))
            self.assertEqual("pdf-copy", manifest.entries[0].kind)
            intent = json.loads(intent_path.read_bytes())
            self.assertEqual("item_sealed", intent["items"][0]["phase"])
            self.assertTrue(all(intent["items"][0]["checkpoints"].values()))

    async def test_recovery_resumes_each_batch_boundary_from_exact_intent(self) -> None:
        class Crash(RuntimeError):
            pass

        scenarios = (
            ("apply_target_published", "paper-1"),
            ("after_db_update_before_checkpoint", "paper-2"),
            ("after_ledger_update_before_checkpoint", "paper-2"),
            ("apply_manifest_updated", "paper-2"),
            ("between_items", "paper-1"),
        )
        for crash_name, crash_paper in scenarios:
            with self.subTest(boundary=crash_name):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    pdf_directory = base / "pdfs"
                    pdf_directory.mkdir()
                    rows: list[dict[str, object]] = []
                    original_sources: dict[str, bytes] = {}
                    for paper_id in ("paper-3", "paper-1", "paper-2"):
                        source = pdf_directory / f"{paper_id}.pdf"
                        data = f"%PDF-1.7\n{paper_id}\n%%EOF\n".encode()
                        source.write_bytes(data)
                        original_sources[paper_id] = data
                        rows.append({"id": paper_id, "pdf_path": str(source)})
                    vault = base / "vault"
                    vault.mkdir()
                    intents = base / "intents"
                    intents.mkdir()
                    state = _MigrationState(rows)
                    pdf_files = _GuardedPdfFiles(
                        root=base,
                        default_directory=pdf_directory,
                    )
                    events: list[str] = []

                    with BoundVaultRoot.open(vault) as root:
                        prepare_migration = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                        )
                        plan = await prepare_migration.plan()
                        prepared = await prepare_migration.prepare(
                            confirm_plan_sha=plan.sha256,
                            intent_output=intents / "batch.json",
                        )

                        def crash(
                            name: str,
                            context: dict[str, object],
                        ) -> None:
                            if name.startswith("apply_") or name.startswith("after_"):
                                events.append(f"{name}:{context.get('paperId')}")
                            if name == "between_items":
                                events.append(f"{name}:{context.get('paperId')}")
                            if name == crash_name and context.get("paperId") == crash_paper:
                                raise Crash(name)

                        crashing = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                            barrier=crash,
                        )
                        with self.assertRaises(Crash):
                            await crashing.apply(
                                intent=prepared.intent_path,
                                confirm_intent_sha=prepared.intent_sha256,
                            )

                        current_sha = hashlib.sha256(
                            prepared.intent_path.read_bytes()
                        ).hexdigest()
                        mutations_before_rejection = list(state.mutations)
                        recovering = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                            barrier=lambda name, context: events.append(
                                f"{name}:{context.get('paperId')}"
                            )
                            if name.startswith("apply_")
                            else None,
                        )
                        with self.assertRaises(PdfMigrationError):
                            await recovering.apply(
                                intent=prepared.intent_path,
                                confirm_intent_sha="0" * 64,
                            )
                        self.assertEqual(
                            mutations_before_rejection,
                            state.mutations,
                        )

                        alias = prepared.intent_path.parent / ".." / "intents" / prepared.intent_path.name
                        with self.assertRaises(PdfMigrationError):
                            await recovering.apply(
                                intent=str(alias),
                                confirm_intent_sha=current_sha,
                            )
                        self.assertEqual(
                            mutations_before_rejection,
                            state.mutations,
                        )

                        result = await recovering.apply(
                            intent=prepared.intent_path,
                            confirm_intent_sha=current_sha,
                        )

                    final_intent = json.loads(prepared.intent_path.read_bytes())
                    self.assertTrue(
                        all(item["phase"] == "item_sealed" for item in final_intent["items"])
                    )
                    self.assertEqual(3, len(state.ledgers))
                    manifest = parse_manifest(
                        (vault / "Research" / ".paper-study" / "manifest.json").read_bytes()
                    )
                    self.assertEqual(3, len(manifest.entries))
                    for paper_id, source_bytes in original_sources.items():
                        target = (
                            vault
                            / "Research"
                            / "Attachments"
                            / "PDF"
                            / f"{paper_id}.pdf"
                        )
                        self.assertEqual(source_bytes, target.read_bytes())
                        self.assertEqual(
                            source_bytes,
                            (pdf_directory / f"{paper_id}.pdf").read_bytes(),
                        )
                        self.assertEqual(str(target.resolve()), state.papers[paper_id]["pdf_path"])
                        self.assertEqual(
                            1,
                            sum(event == f"apply_target_published:{paper_id}" for event in events),
                        )
                        self.assertEqual(
                            1,
                            sum(value == f"db:{paper_id}" for value in state.mutations),
                        )
                        self.assertEqual(
                            1,
                            sum(value == f"ledger:{paper_id}" for value in state.mutations),
                        )
                    mutations_before_noop = list(state.mutations)
                    with BoundVaultRoot.open(vault) as reopened_root:
                        no_op = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=reopened_root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                        )
                        repeated = await no_op.apply(
                            intent=result.intent_path,
                            confirm_intent_sha=result.intent_sha256,
                        )
                    self.assertEqual(result.intent_sha256, repeated.intent_sha256)
                    self.assertEqual(mutations_before_noop, state.mutations)

    async def test_completed_intent_is_sealed_as_verifiable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            rows: list[dict[str, object]] = []
            for paper_id in ("paper-2", "paper-1"):
                source = pdf_directory / f"{paper_id}.pdf"
                source.write_bytes(f"%PDF-1.7\n{paper_id}\n%%EOF\n".encode())
                rows.append({"id": paper_id, "pdf_path": str(source)})
            vault = base / "vault"
            vault.mkdir()
            intents = base / "intents"
            intents.mkdir()
            state = _MigrationState(rows)
            pdf_files = _GuardedPdfFiles(
                root=base,
                default_directory=pdf_directory,
            )

            with BoundVaultRoot.open(vault) as root:
                migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                )
                plan = await migration.plan()
                prepared = await migration.prepare(
                    confirm_plan_sha=plan.sha256,
                    intent_output=intents / "sealed.json",
                )
                result = await migration.apply(
                    intent=prepared.intent_path,
                    confirm_intent_sha=prepared.intent_sha256,
                )
                mutations_before_repeat = list(state.mutations)
                repeated = await migration.apply(
                    intent=result.intent_path,
                    confirm_intent_sha=result.intent_sha256,
                )

            self.assertEqual("sealed", result.state)
            self.assertEqual(result.intent_sha256, repeated.intent_sha256)
            self.assertEqual(mutations_before_repeat, state.mutations)
            self.assertEqual([prepared.intent_path.name], [path.name for path in intents.iterdir()])
            intent_bytes = prepared.intent_path.read_bytes()
            self.assertEqual(hashlib.sha256(intent_bytes).hexdigest(), result.intent_sha256)
            intent = json.loads(intent_bytes)
            self.assertEqual("sealed", intent["state"])
            self.assertTrue(all(item["phase"] == "item_sealed" for item in intent["items"]))
            receipt = intent["receipt"]
            self.assertEqual(
                {
                    "finalDbHash",
                    "finalItemHashes",
                    "finalLedgerHash",
                    "finalManifestHash",
                    "planSha256",
                    "sealedAt",
                    "sourcePreserved",
                },
                set(receipt),
            )
            self.assertEqual(plan.sha256, receipt["planSha256"])
            self.assertEqual(True, receipt["sourcePreserved"])
            self.assertEqual(["paper-1", "paper-2"], [row["paperId"] for row in receipt["finalItemHashes"]])
            self.assertTrue(all(len(row["sha256"]) == 64 for row in receipt["finalItemHashes"]))
            manifest_bytes = (
                vault / "Research" / ".paper-study" / "manifest.json"
            ).read_bytes()
            self.assertEqual(
                hashlib.sha256(manifest_bytes).hexdigest(),
                receipt["finalManifestHash"],
            )
            self.assertEqual(64, len(receipt["finalDbHash"]))
            self.assertEqual(64, len(receipt["finalLedgerHash"]))

    async def test_rollback_uses_exact_intent_and_restores_batch_prior_state_idempotently(
        self,
    ) -> None:
        class Crash(RuntimeError):
            pass

        for partial in (False, True):
            with self.subTest(partial=partial):
                with tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    pdf_directory = base / "pdfs"
                    pdf_directory.mkdir()
                    rows: list[dict[str, object]] = []
                    original_paths: dict[str, str] = {}
                    original_bytes: dict[str, bytes] = {}
                    for paper_id in ("paper-2", "paper-1"):
                        source = pdf_directory / f"{paper_id}.pdf"
                        data = f"%PDF-1.7\nrollback-{paper_id}\n%%EOF\n".encode()
                        source.write_bytes(data)
                        rows.append({"id": paper_id, "pdf_path": str(source)})
                        original_paths[paper_id] = str(source)
                        original_bytes[paper_id] = data
                    vault = base / "vault"
                    vault.mkdir()
                    intents = base / "intents"
                    intents.mkdir()
                    state = _MigrationState(rows)
                    pdf_files = _GuardedPdfFiles(
                        root=base,
                        default_directory=pdf_directory,
                    )

                    with BoundVaultRoot.open(vault) as root:
                        migration = ObsidianPdfMigration(
                            pdf_files=pdf_files,
                            root=root,
                            repository=state,
                            root_folder="Research",
                            settings_fingerprint="f" * 64,
                            clock=lambda: NOW,
                        )
                        plan = await migration.plan()
                        prepared = await migration.prepare(
                            confirm_plan_sha=plan.sha256,
                            intent_output=intents / "rollback.json",
                        )
                        if partial:
                            def crash(name: str, context: dict[str, object]) -> None:
                                if (
                                    name == "after_db_update_before_checkpoint"
                                    and context.get("paperId") == "paper-2"
                                ):
                                    raise Crash(name)

                            crashing = ObsidianPdfMigration(
                                pdf_files=pdf_files,
                                root=root,
                                repository=state,
                                root_folder="Research",
                                settings_fingerprint="f" * 64,
                                clock=lambda: NOW,
                                barrier=crash,
                            )
                            with self.assertRaises(Crash):
                                await crashing.apply(
                                    intent=prepared.intent_path,
                                    confirm_intent_sha=prepared.intent_sha256,
                                )
                            current_sha = hashlib.sha256(
                                prepared.intent_path.read_bytes()
                            ).hexdigest()
                        else:
                            applied = await migration.apply(
                                intent=prepared.intent_path,
                                confirm_intent_sha=prepared.intent_sha256,
                            )
                            current_sha = applied.intent_sha256

                        before_rejected = list(state.mutations)
                        with self.assertRaises(PdfMigrationError):
                            await migration.rollback(
                                intent=prepared.intent_path,
                                confirm_intent_sha="0" * 64,
                            )
                        self.assertEqual(before_rejected, state.mutations)
                        alias = prepared.intent_path.parent / ".." / "intents" / prepared.intent_path.name
                        with self.assertRaises(PdfMigrationError):
                            await migration.rollback(
                                intent=alias,
                                confirm_intent_sha=current_sha,
                            )
                        self.assertEqual(before_rejected, state.mutations)

                        stdout = StringIO()
                        exit_code = await run_cli(
                            [
                                "rollback",
                                "--intent",
                                str(prepared.intent_path),
                                "--confirm-intent-sha",
                                current_sha,
                            ],
                            migration=migration,
                            stdout=stdout,
                        )
                        result = json.loads(stdout.getvalue())
                        mutations_before_repeat = list(state.mutations)
                        repeated = await migration.rollback(
                            intent=prepared.intent_path,
                            confirm_intent_sha=result["intentSha256"],
                        )

                    self.assertEqual(0, exit_code)
                    self.assertEqual("rolled_back", result["state"])
                    self.assertEqual(result["intentSha256"], repeated.intent_sha256)
                    self.assertEqual(mutations_before_repeat, state.mutations)
                    self.assertEqual({}, state.ledgers)
                    self.assertFalse(
                        (vault / "Research" / ".paper-study" / "manifest.json").exists()
                    )
                    for paper_id in ("paper-1", "paper-2"):
                        self.assertEqual(original_paths[paper_id], state.papers[paper_id]["pdf_path"])
                        self.assertEqual(
                            original_bytes[paper_id],
                            (pdf_directory / f"{paper_id}.pdf").read_bytes(),
                        )
                        self.assertFalse(
                            (
                                vault
                                / "Research"
                                / "Attachments"
                                / "PDF"
                                / f"{paper_id}.pdf"
                            ).exists()
                        )
                    rolled_back = json.loads(prepared.intent_path.read_bytes())
                    self.assertEqual("rolled_back", rolled_back["state"])
                    self.assertEqual(True, rolled_back["receipt"]["sourcePreserved"])

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            pdf_directory = base / "pdfs"
            pdf_directory.mkdir()
            source = pdf_directory / "paper-1.pdf"
            source.write_bytes(b"%PDF-1.7\ntamper-source\n%%EOF\n")
            vault = base / "vault"
            vault.mkdir()
            intents = base / "intents"
            intents.mkdir()
            state = _MigrationState([{"id": "paper-1", "pdf_path": str(source)}])
            pdf_files = _GuardedPdfFiles(root=base, default_directory=pdf_directory)
            with BoundVaultRoot.open(vault) as root:
                migration = ObsidianPdfMigration(
                    pdf_files=pdf_files,
                    root=root,
                    repository=state,
                    root_folder="Research",
                    settings_fingerprint="f" * 64,
                    clock=lambda: NOW,
                )
                plan = await migration.plan()
                prepared = await migration.prepare(
                    confirm_plan_sha=plan.sha256,
                    intent_output=intents / "tamper.json",
                )
                applied = await migration.apply(
                    intent=prepared.intent_path,
                    confirm_intent_sha=prepared.intent_sha256,
                )
                target = vault / "Research" / "Attachments" / "PDF" / "paper-1.pdf"
                target.write_bytes(b"user-tamper")
                before_conflict = list(state.mutations)
                with self.assertRaises(PdfMigrationError):
                    await migration.rollback(
                        intent=applied.intent_path,
                        confirm_intent_sha=applied.intent_sha256,
                    )
            self.assertEqual(b"user-tamper", target.read_bytes())
            self.assertEqual(before_conflict, state.mutations)


if __name__ == "__main__":
    unittest.main()
