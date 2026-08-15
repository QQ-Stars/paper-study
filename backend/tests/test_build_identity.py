from __future__ import annotations

from pathlib import Path
import json
import subprocess
import tempfile
import time
import unittest


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)


class BuildIdentityTests(unittest.TestCase):
    def test_source_tree_hash_covers_tracked_untracked_and_modified_bytes(self) -> None:
        from backend.app.api.compat.build_identity import compute_source_tree_hash

        with tempfile.TemporaryDirectory(prefix="study-app-p6-build-source-") as raw:
            root = Path(raw)
            _git(root, "init", "--quiet")
            tracked = root / "tracked.txt"
            tracked.write_text("tracked-v1\n", encoding="utf-8")
            _git(root, "add", "--", "tracked.txt")

            baseline = compute_source_tree_hash(root)
            self.assertEqual(baseline, compute_source_tree_hash(root))

            ignored = root / "data" / "compatibility" / "runtime" / "evidence.json"
            ignored.parent.mkdir(parents=True)
            ignored.write_text("runtime-only\n", encoding="utf-8")
            self.assertEqual(baseline, compute_source_tree_hash(root))

            tracked.write_text("tracked-v2\n", encoding="utf-8")
            modified = compute_source_tree_hash(root)
            self.assertNotEqual(baseline, modified)

            tracked.write_text("tracked-v1\n", encoding="utf-8")
            untracked = root / "new-source.py"
            untracked.write_text("VALUE = 1\n", encoding="utf-8")
            with_untracked = compute_source_tree_hash(root)
            self.assertNotEqual(baseline, with_untracked)

            untracked.unlink()
            _git(root, "update-index", "--chmod=+x", "tracked.txt")
            executable = compute_source_tree_hash(root)
            self.assertNotEqual(baseline, executable)

    def test_build_artifact_hash_covers_exact_deployed_outputs(self) -> None:
        from backend.app.api.compat.build_identity import compute_build_artifact_hash

        with tempfile.TemporaryDirectory(prefix="study-app-p6-build-artifacts-") as raw:
            root = Path(raw)
            python_bundle = root / "study_app.whl"
            python_bundle.write_bytes(b"wheel-v1")
            frontend_root = root / "frontend"
            assets = frontend_root / "assets"
            assets.mkdir(parents=True)
            script = assets / "index.js"
            style = assets / "index.css"
            script.write_bytes(b"script-v1")
            style.write_bytes(b"style-v1")
            manifest = frontend_root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "index.html": {
                            "file": "assets/index.js",
                            "css": ["assets/index.css"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            compose = root / "compose.json"
            compose.write_text('{"services":{"api":{"image":"candidate"}}}', encoding="utf-8")
            digests = {"candidate": f"sha256:{'1' * 64}"}

            def identity() -> str:
                return compute_build_artifact_hash(
                    python_artifacts=(python_bundle,),
                    frontend_root=frontend_root,
                    frontend_manifest=manifest,
                    resolved_compose=compose,
                    image_digests=digests,
                )

            baseline = identity()
            python_bundle.write_bytes(b"wheel-v2")
            self.assertNotEqual(baseline, identity())
            python_bundle.write_bytes(b"wheel-v1")
            script.write_bytes(b"script-v2")
            self.assertNotEqual(baseline, identity())
            script.write_bytes(b"script-v1")
            compose.write_text('{"services":{"api":{"image":"changed"}}}', encoding="utf-8")
            self.assertNotEqual(baseline, identity())
            compose.write_text('{"services":{"api":{"image":"candidate"}}}', encoding="utf-8")
            digests["candidate"] = f"sha256:{'2' * 64}"
            self.assertNotEqual(baseline, identity())

    def test_freeze_uses_unique_content_addressed_path_and_never_overwrites(self) -> None:
        from backend.app.api.compat.build_identity import freeze_build_identity

        with tempfile.TemporaryDirectory(prefix="study-app-p6-freeze-") as raw:
            root = Path(raw)
            repository = root / "repository"
            repository.mkdir()
            _git(repository, "init", "--quiet")
            _git(repository, "config", "user.name", "Build Identity Test")
            _git(repository, "config", "user.email", "build@example.test")
            source = repository / "app.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            _git(repository, "add", "--", "app.py")
            _git(repository, "commit", "--quiet", "-m", "fixture")

            python_bundle = root / "study_app.whl"
            python_bundle.write_bytes(b"wheel-v1")
            frontend_root = root / "frontend"
            frontend_root.mkdir()
            asset = frontend_root / "index.js"
            asset.write_bytes(b"frontend-v1")
            manifest = frontend_root / "manifest.json"
            manifest.write_text(
                json.dumps({"index.html": {"file": "index.js"}}),
                encoding="utf-8",
            )
            compose = root / "compose.json"
            compose.write_text("{}", encoding="utf-8")
            identity_root = root / "identities"
            identity_root.mkdir()
            arguments = {
                "repository": repository,
                "build_identity_directory": identity_root,
                "python_artifacts": (python_bundle,),
                "frontend_root": frontend_root,
                "frontend_manifest": manifest,
                "resolved_compose": compose,
                "image_digests": {"candidate": f"sha256:{'a' * 64}"},
            }

            first = freeze_build_identity(**arguments)
            self.assertEqual(
                f"frozen-build-identity-{first.build_id}.json",
                first.manifest_path.name,
            )
            original = first.manifest_path.read_bytes()
            original_mtime = first.manifest_path.stat().st_mtime_ns
            time.sleep(0.01)
            repeated = freeze_build_identity(**arguments)
            self.assertEqual(first, repeated)
            self.assertEqual(original, first.manifest_path.read_bytes())
            self.assertEqual(original_mtime, first.manifest_path.stat().st_mtime_ns)

            source.write_text("VALUE = 2\n", encoding="utf-8")
            changed = freeze_build_identity(**arguments)
            self.assertNotEqual(first.build_id, changed.build_id)
            self.assertNotEqual(first.manifest_path, changed.manifest_path)
            self.assertEqual(original, first.manifest_path.read_bytes())

    def test_freeze_and_verify_identity_cli_require_typed_build_manifest_and_detect_drift(self) -> None:
        from backend.app.api.compat.build_identity import (
            BuildIdentityError,
            freeze_build_identity,
            verify_build_identity,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p6-verify-build-") as raw:
            root = Path(raw)
            repository = root / "repository"
            repository.mkdir()
            _git(repository, "init", "--quiet")
            _git(repository, "config", "user.name", "Build Verify Test")
            _git(repository, "config", "user.email", "verify@example.test")
            source = repository / "app.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            _git(repository, "add", "--", "app.py")
            _git(repository, "commit", "--quiet", "-m", "fixture")
            bundle = root / "study_app.whl"
            bundle.write_bytes(b"wheel-v1")
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.js").write_bytes(b"frontend-v1")
            frontend_manifest = frontend / "manifest.json"
            frontend_manifest.write_text(
                json.dumps({"index.html": {"file": "index.js"}}), encoding="utf-8"
            )
            compose = root / "compose.json"
            compose.write_text("{}", encoding="utf-8")
            identity_root = root / "identities"
            identity_root.mkdir()
            inputs = {
                "repository": repository,
                "python_artifacts": (bundle,),
                "frontend_root": frontend,
                "frontend_manifest": frontend_manifest,
                "resolved_compose": compose,
                "image_digests": {"candidate": f"sha256:{'a' * 64}"},
            }
            frozen = freeze_build_identity(
                build_identity_directory=identity_root,
                **inputs,
            )
            verified = verify_build_identity(
                build_identity_manifest=frozen.manifest_path,
                **inputs,
            )
            self.assertEqual(frozen, verified)

            source.write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaises(BuildIdentityError) as source_drift:
                verify_build_identity(build_identity_manifest=frozen.manifest_path, **inputs)
            self.assertEqual("BUILD_IDENTITY_DRIFT", source_drift.exception.code)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            bundle.write_bytes(b"wheel-v2")
            with self.assertRaises(BuildIdentityError) as artifact_drift:
                verify_build_identity(build_identity_manifest=frozen.manifest_path, **inputs)
            self.assertEqual("BUILD_IDENTITY_DRIFT", artifact_drift.exception.code)

            database_manifest = root / "database-identity.json"
            database_manifest.write_text(
                json.dumps({"schemaVersion": 1, "manifestKind": "database"}),
                encoding="utf-8",
            )
            with self.assertRaises(BuildIdentityError):
                verify_build_identity(build_identity_manifest=database_manifest, **inputs)


if __name__ == "__main__":
    unittest.main()
