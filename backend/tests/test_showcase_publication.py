from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app.api.app import create_app
from backend.app.api.dependencies import ApiDependencies
from backend.app.application.reproductions import ReproductionWorkspace
from backend.app.config import DatabaseSettings
from backend.app.infrastructure.database import create_async_session_factory
from backend.app.repositories.unit_of_work import SqlAlchemyUnitOfWork
from backend.app.runtime import ApiSettings
from backend.tests.support.p1_database import create_legacy_database, run_alembic


_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0dIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _Application:
    schema_revision = "20260830_01"

    def __init__(self, session_factory, artifact_root: Path, showcase_root: Path) -> None:
        self.session_factory = session_factory
        self.reproduction_workspace = ReproductionWorkspace(
            lambda: SqlAlchemyUnitOfWork(session_factory),
            artifact_root=artifact_root,
            showcase_root=showcase_root,
        )

    async def dispose(self) -> None:
        await self.session_factory.kw["bind"].dispose()


class ShowcasePublicationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="study-app-showcase-")
        self.root = Path(self._temp.name)
        self.database_path = self.root / "database" / "app.db"
        create_legacy_database(self.database_path)
        run_alembic(self.database_path, "20260830_01")
        self.session_factory = create_async_session_factory(DatabaseSettings(self.database_path))
        self.showcase_root = self.root / "paper-showcase"
        self.application = _Application(
            self.session_factory,
            self.root / "artifacts",
            self.showcase_root,
        )
        self.client_context = TestClient(
            create_app(
                settings=ApiSettings.for_tests(),
                dependencies=ApiDependencies(self.application, self.session_factory),
                required_schema_revision="20260830_01",
            )
        )
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self._temp.cleanup()

    def _completed_project(self) -> dict[str, object]:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Vision Transformer Baseline", "tags": ["vision"]},
        )
        self.assertEqual(201, response.status_code, response.text)
        project = response.json()
        response = self.client.patch(
            f"/api/v2/reproductions/{project['id']}",
            json={"status": "completed", "expectedRevision": project["revision"]},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _publication(self, project_id: str) -> dict[str, object]:
        response = self.client.get(f"/api/v2/reproductions/{project_id}/publication")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _save_publication(self, project_id: str, publication: dict[str, object], **values: object) -> dict[str, object]:
        response = self.client.put(
            f"/api/v2/reproductions/{project_id}/publication",
            json={"expectedRevision": publication["revision"], **values},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_only_completed_approved_and_validated_content_can_publish(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={"paperId": "paper-1", "name": "Incomplete project"},
        )
        project = response.json()
        publication = self._publication(project["id"])
        publication = self._save_publication(
            project["id"],
            publication,
            decision="approved",
            publicTitle="Incomplete project",
            publicSummary="A draft summary.",
            aggregateConclusion="partial",
        )
        response = self.client.post(
            f"/api/v2/reproductions/{project['id']}/publication/validate"
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()["valid"])
        self.assertIn("复现流程必须处于", response.json()["errors"][0])
        publication = response.json()["publication"]
        response = self.client.post(
            f"/api/v2/reproductions/{project['id']}/publication/publish",
            json={"expectedRevision": publication["revision"]},
        )
        self.assertEqual(422, response.status_code, response.text)
        self.assertEqual("PUBLICATION_VALIDATION_FAILED", response.json()["error"]["code"])

    def test_publish_rewrites_private_artifacts_and_revoke_removes_only_managed_files(self) -> None:
        project = self._completed_project()
        project_id = str(project["id"])
        listing = self.client.get("/api/v2/reproductions")
        self.assertEqual(200, listing.status_code, listing.text)
        self.assertFalse(listing.json()["items"][0]["isPublished"])
        upload = self.client.post(
            f"/api/v2/reproductions/{project_id}/artifacts",
            data={"kind": "image"},
            files={"file": ("metric.png", _PNG, "image/png")},
        )
        self.assertEqual(201, upload.status_code, upload.text)
        artifact = upload.json()
        document = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "expectedRevision": project["document"]["revision"],
                "content": (
                    "# 实验结果\n\n"
                    f"![Metric](/api/v2/reproductions/{project_id}/artifacts/{artifact['id']}/download)\n"
                ),
            },
        )
        self.assertEqual(200, document.status_code, document.text)
        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="vit-baseline",
            publicTitle="ViT Baseline Reproduction",
            publicSummary="A completed baseline reproduction with a documented metric comparison.",
            aggregateConclusion="partial",
            publicArtifactIds=[artifact["id"]],
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertEqual(200, checked.status_code, checked.text)
        self.assertTrue(checked.json()["valid"], checked.text)
        publication = checked.json()["publication"]
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": publication["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        self.assertEqual("/reproductions/vit-baseline/", published.json()["url"])
        listing = self.client.get("/api/v2/reproductions")
        self.assertEqual(200, listing.status_code, listing.text)
        self.assertTrue(listing.json()["items"][0]["isPublished"])
        post = self.showcase_root / "source" / "_posts" / "reproductions" / "vit-baseline.md"
        image = (
            self.showcase_root
            / "source"
            / "images"
            / "reproductions"
            / f"vit-baseline--{artifact['id']}.png"
        )
        self.assertTrue(post.is_file())
        self.assertTrue(image.is_file())
        content = post.read_text(encoding="utf-8")
        self.assertNotIn("/api/v2/reproductions/", content)
        self.assertIn(
            f"/images/reproductions/vit-baseline--{artifact['id']}.png",
            content,
        )
        self.assertFalse(
            (self.showcase_root / "source" / "images" / "reproductions" / "vit-baseline").exists()
        )
        self.assertIn('layout: "post"', content)
        self.assertIn(
            'description: "A completed baseline reproduction with a documented metric comparison."',
            content,
        )
        self.assertNotIn(
            'subtitle: "A completed baseline reproduction with a documented metric comparison."',
            content,
        )
        self.assertIn('  - "论文复现"', content)
        self.assertNotIn('category_bar:', content)
        manifest = json.loads((self.showcase_root / ".showcase" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("vit-baseline", manifest["projects"][project_id]["slug"])

        publication = published.json()["publication"]
        revoked = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/revoke",
            json={"expectedRevision": publication["revision"]},
        )
        self.assertEqual(200, revoked.status_code, revoked.text)
        self.assertEqual("revoked", revoked.json()["publication"]["status"])
        listing = self.client.get("/api/v2/reproductions")
        self.assertEqual(200, listing.status_code, listing.text)
        self.assertFalse(listing.json()["items"][0]["isPublished"])
        self.assertFalse(post.exists())
        self.assertFalse(image.exists())

    def test_publication_becomes_stale_after_document_change_and_slug_is_immutable_after_publish(self) -> None:
        project = self._completed_project()
        project_id = str(project["id"])
        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="stale-check",
            publicTitle="Stale check",
            publicSummary="Completed result with an explicit conclusion.",
            aggregateConclusion="reproduced",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertTrue(checked.json()["valid"], checked.text)
        publication = checked.json()["publication"]
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": publication["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        publication = published.json()["publication"]
        changed = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={"content": "# Revised\n\nA public update.", "expectedRevision": project["document"]["revision"]},
        )
        self.assertEqual(200, changed.status_code, changed.text)
        publication = self._publication(project_id)
        self.assertEqual("stale", publication["status"])
        listing = self.client.get("/api/v2/reproductions")
        self.assertEqual(200, listing.status_code, listing.text)
        self.assertTrue(listing.json()["items"][0]["isPublished"])
        rejected = self.client.put(
            f"/api/v2/reproductions/{project_id}/publication",
            json={"expectedRevision": publication["revision"], "stableSlug": "different-slug"},
        )
        self.assertEqual(422, rejected.status_code, rejected.text)

    def test_stale_validation_keeps_the_live_export_revocable_and_slug_locked(self) -> None:
        project = self._completed_project()
        project_id = str(project["id"])
        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="managed-stale-export",
            publicTitle="Managed stale export",
            publicSummary="A previously published snapshot must remain traceable until revoked.",
            aggregateConclusion="partial",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertTrue(checked.json()["valid"], checked.text)
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": checked.json()["publication"]["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        published_state = published.json()["publication"]
        post = (
            self.showcase_root
            / "source"
            / "_posts"
            / "reproductions"
            / "managed-stale-export.md"
        )
        self.assertTrue(post.is_file())

        changed = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "expectedRevision": project["document"]["revision"],
                "content": "# Unsafe update\n\n<script>alert(1)</script>",
            },
        )
        self.assertEqual(200, changed.status_code, changed.text)
        stale = self._publication(project_id)
        self.assertEqual("stale", stale["status"])
        self.assertEqual(published_state["contentHash"], stale["contentHash"])

        failed_validation = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/validate"
        )
        self.assertEqual(200, failed_validation.status_code, failed_validation.text)
        self.assertFalse(failed_validation.json()["valid"])
        stale = failed_validation.json()["publication"]
        self.assertEqual("stale", stale["status"])
        self.assertEqual(published_state["contentHash"], stale["contentHash"])
        self.assertEqual(published_state["lastExportedAt"], stale["lastExportedAt"])
        self.assertTrue(post.is_file())

        revoked = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/revoke",
            json={"expectedRevision": stale["revision"]},
        )
        self.assertEqual(200, revoked.status_code, revoked.text)
        revoked_state = revoked.json()["publication"]
        self.assertEqual("revoked", revoked_state["status"])
        self.assertIsNone(revoked_state["contentHash"])
        self.assertEqual(published_state["lastExportedAt"], revoked_state["lastExportedAt"])
        self.assertFalse(post.exists())

        rejected = self.client.put(
            f"/api/v2/reproductions/{project_id}/publication",
            json={
                "expectedRevision": revoked_state["revision"],
                "stableSlug": "renamed-after-revoke",
            },
        )
        self.assertEqual(422, rejected.status_code, rejected.text)

    def test_stale_project_mutations_do_not_revoke_live_export(self) -> None:
        project = self._completed_project()
        project_id = str(project["id"])
        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="stale-project-mutation",
            publicTitle="Stale project mutation",
            publicSummary="A completed result that must remain public after rejected retries.",
            aggregateConclusion="partial",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertTrue(checked.json()["valid"], checked.text)
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": checked.json()["publication"]["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        post = (
            self.showcase_root
            / "source"
            / "_posts"
            / "reproductions"
            / "stale-project-mutation.md"
        )
        self.assertTrue(post.is_file())

        stale_revision = int(project["revision"]) - 1
        patch_response = self.client.patch(
            f"/api/v2/reproductions/{project_id}",
            json={"status": "planned", "expectedRevision": stale_revision},
        )
        self.assertEqual(409, patch_response.status_code, patch_response.text)
        self.assertEqual("REPRODUCTION_CONFLICT", patch_response.json()["error"]["code"])
        self.assertTrue(post.is_file())

        archive_response = self.client.post(
            f"/api/v2/reproductions/{project_id}/archive",
            json={"expectedRevision": stale_revision},
        )
        self.assertEqual(409, archive_response.status_code, archive_response.text)
        self.assertEqual("REPRODUCTION_CONFLICT", archive_response.json()["error"]["code"])
        self.assertTrue(post.is_file())

        current = self.client.get(f"/api/v2/reproductions/{project_id}")
        self.assertEqual(200, current.status_code, current.text)
        self.assertEqual("completed", current.json()["status"])
        self.assertEqual("published", self._publication(project_id)["status"])

    def test_validation_blocks_private_paths_scripts_and_unapproved_attachment_references(self) -> None:
        project = self._completed_project()
        project_id = str(project["id"])
        document = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "expectedRevision": project["document"]["revision"],
                "content": "# Notes\n\n<script>alert(1)</script>\n\nC:\\Users\\HP\\secret.txt\n\nAPI_KEY=not-public",
            },
        )
        self.assertEqual(200, document.status_code, document.text)
        publication = self._publication(project_id)
        self._save_publication(
            project_id,
            publication,
            decision="approved",
            publicTitle="Unsafe export",
            publicSummary="A summary.",
            aggregateConclusion="not_reproduced",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertEqual(200, checked.status_code, checked.text)
        errors = "\n".join(checked.json()["errors"])
        self.assertIn("不允许公开", errors)
        self.assertIn("本地绝对路径", errors)

    def test_article_publication_does_not_require_reproduction_conclusion(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={
                "projectKind": "article",
                "paperId": None,
                "name": "A field note",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        project = response.json()
        project_id = str(project["id"])

        completed = self.client.patch(
            f"/api/v2/reproductions/{project_id}",
            json={"status": "completed", "expectedRevision": project["revision"]},
        )
        self.assertEqual(200, completed.status_code, completed.text)
        project = completed.json()
        document = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "expectedRevision": project["document"]["revision"],
                "content": "# A field note\n\nThis is an independent article.",
            },
        )
        self.assertEqual(200, document.status_code, document.text)

        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="field-note",
            publicTitle="A field note",
            publicSummary="A short article without an experiment conclusion.",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertEqual(200, checked.status_code, checked.text)
        self.assertTrue(checked.json()["valid"], checked.text)
        publication = checked.json()["publication"]
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": publication["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        self.assertEqual("/articles/field-note/", published.json()["url"])

        post = self.showcase_root / "source" / "_posts" / "articles" / "field-note.md"
        self.assertTrue(post.is_file())
        content = post.read_text(encoding="utf-8")
        self.assertIn('type: "article"', content)
        self.assertIn('content_type: "article"', content)
        self.assertIn('layout: "post"', content)
        self.assertIn('description: "A short article without an experiment conclusion."', content)
        self.assertNotIn('subtitle: "A short article without an experiment conclusion."', content)
        self.assertIn('  - "文章"', content)
        self.assertNotIn("复现结论", content)
        self.assertNotIn("aggregate_conclusion", content)

    def test_article_with_optional_paper_exports_paper_metadata_for_catalog(self) -> None:
        response = self.client.post(
            "/api/v2/reproductions",
            json={
                "projectKind": "article",
                "paperId": "paper-1",
                "name": "Reading notes",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        project = response.json()
        project_id = str(project["id"])
        completed = self.client.patch(
            f"/api/v2/reproductions/{project_id}",
            json={"status": "completed", "expectedRevision": project["revision"]},
        )
        self.assertEqual(200, completed.status_code, completed.text)
        project = completed.json()
        document = self.client.put(
            f"/api/v2/reproductions/{project_id}/document",
            json={
                "expectedRevision": project["document"]["revision"],
                "content": "# Reading notes\n\nA public note.",
            },
        )
        self.assertEqual(200, document.status_code, document.text)
        publication = self._publication(project_id)
        publication = self._save_publication(
            project_id,
            publication,
            decision="approved",
            stableSlug="reading-notes",
            publicTitle="Reading notes",
            publicSummary="A note linked to a paper.",
        )
        checked = self.client.post(f"/api/v2/reproductions/{project_id}/publication/validate")
        self.assertTrue(checked.json()["valid"], checked.text)
        published = self.client.post(
            f"/api/v2/reproductions/{project_id}/publication/publish",
            json={"expectedRevision": checked.json()["publication"]["revision"]},
        )
        self.assertEqual(200, published.status_code, published.text)
        content = (
            self.showcase_root / "source" / "_posts" / "articles" / "reading-notes.md"
        ).read_text(encoding="utf-8")
        self.assertIn('paper_id: "paper-1"', content)
        self.assertIn('paper_title: "', content)
        self.assertIn('paper_year: "', content)
