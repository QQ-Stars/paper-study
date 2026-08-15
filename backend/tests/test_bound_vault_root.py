from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class BoundVaultRootTests(unittest.TestCase):
    def test_rejects_root_and_parent_junction_symlink_swaps_while_bound(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            ObsidianVaultError,
            VaultRelativePath,
        )

        for victim_name in ("vault", "Research", "Papers"):
            with self.subTest(victim=victim_name):
                with tempfile.TemporaryDirectory(
                    prefix="study-app-p5-vault-parent-race-"
                ) as temp:
                    base = Path(temp)
                    vault = base / "vault"
                    outside = base / "outside"
                    vault.mkdir()
                    outside.mkdir()
                    sentinel = outside / "sentinel.txt"
                    sentinel.write_bytes(b"outside\n")
                    moved: Path | None = None
                    blocked = False

                    def swap(_final_path: Path) -> None:
                        nonlocal blocked, moved
                        victim = {
                            "vault": vault,
                            "Research": vault / "Research",
                            "Papers": vault / "Research" / "Papers",
                        }[victim_name]
                        moved = victim.with_name(f"{victim.name}-bound-original")
                        try:
                            os.replace(victim, moved)
                            os.symlink(outside, victim, target_is_directory=True)
                        except OSError:
                            blocked = True

                    caught: ObsidianVaultError | None = None
                    try:
                        with BoundVaultRoot.open(vault, before_publish=swap) as bound:
                            try:
                                bound.publish_new(
                                    VaultRelativePath("Research/Papers/paper-1.md"),
                                    b"managed\n",
                                )
                            except ObsidianVaultError as error:
                                caught = error
                    finally:
                        if moved is not None and moved.exists():
                            victim = {
                                "vault": vault,
                                "Research": vault / "Research",
                                "Papers": vault / "Research" / "Papers",
                            }[victim_name]
                            if victim.is_symlink():
                                victim.unlink()
                            os.replace(moved, victim)

                    self.assertTrue(blocked or caught is not None)
                    if caught is not None:
                        self.assertIn(
                            caught.code,
                            {"OBSIDIAN_ROOT_CHANGED", "OBSIDIAN_PARENT_CHANGED"},
                        )
                    self.assertEqual(b"outside\n", sentinel.read_bytes())
                    self.assertFalse((outside / "paper-1.md").exists())

    def test_all_vault_mutations_use_only_bound_handle_or_dirfd_operations(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            VaultRelativePath,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-tripwire-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()

            def forbidden(*_args: object, **_kwargs: object) -> None:
                raise AssertionError("path-only Vault mutation was used")

            with BoundVaultRoot.open(vault) as bound:
                with (
                    patch.object(Path, "mkdir", forbidden),
                    patch.object(Path, "open", forbidden),
                    patch.object(Path, "write_text", forbidden),
                    patch.object(Path, "write_bytes", forbidden),
                    patch.object(Path, "unlink", forbidden),
                    patch.object(Path, "replace", forbidden),
                    patch("builtins.open", forbidden),
                    patch("os.makedirs", forbidden),
                    patch("os.replace", forbidden),
                    patch("os.rename", forbidden),
                ):
                    published = []
                    for relative, data in (
                        ("Research/Papers/paper-1.md", b"paper\n"),
                        ("Research/Sources/paper-1.md", b"source\n"),
                        ("Research/.paper-study/manifest.json", b"{}\n"),
                        ("Research/Notes/paper-1.md", b"note\n"),
                        ("Research/.paper-study/probe", b"probe\n"),
                        ("Research/Attachments/PDF/paper-1.pdf", b"%PDF-1.4\n"),
                    ):
                        published.append(
                            bound.publish_new(VaultRelativePath(relative), data)
                        )
                    updated = bound.replace_managed(
                        VaultRelativePath("Research/Papers/paper-1.md"),
                        b"updated paper\n",
                        published[0].identity,
                    )
                    bound.delete_managed(
                        VaultRelativePath("Research/Papers/paper-1.md"),
                        updated.identity,
                    )

            self.assertFalse((vault / "Research" / "Papers" / "paper-1.md").exists())
            self.assertEqual(
                b"%PDF-1.4\n",
                (vault / "Research" / "Attachments" / "PDF" / "paper-1.pdf").read_bytes(),
            )

    def test_first_publish_is_true_no_replace_under_final_target_race(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            ObsidianVaultError,
            VaultRelativePath,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-race-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            competitor = b"user-created competitor\n"
            hit = []

            def race(final_path: Path) -> None:
                hit.append(final_path)
                final_path.write_bytes(competitor)

            with BoundVaultRoot.open(vault, before_publish=race) as bound:
                with self.assertRaises(ObsidianVaultError) as caught:
                    bound.publish_new(
                        VaultRelativePath("Research/Papers/paper-1.md"),
                        b"managed bytes\n",
                    )

            self.assertEqual("OBSIDIAN_TARGET_EXISTS", caught.exception.code)
            target = vault / "Research" / "Papers" / "paper-1.md"
            self.assertEqual([target], hit)
            self.assertEqual(competitor, target.read_bytes())
            self.assertEqual([], list(target.parent.glob(".paper-1.md.*.tmp")))

    def test_managed_replace_and_delete_require_exact_final_identity(self) -> None:
        from backend.app.infrastructure.bound_vault_root import (
            BoundVaultRoot,
            ObsidianVaultError,
            VaultRelativePath,
        )

        with tempfile.TemporaryDirectory(prefix="study-app-p5-vault-identity-") as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            with BoundVaultRoot.open(vault) as bound:
                relative = VaultRelativePath("Research/Papers/paper-1.md")
                published = bound.publish_new(relative, b"original\n")

                target = vault / "Research" / "Papers" / "paper-1.md"
                replacement = target.with_name("replacement.md")
                replacement.write_bytes(b"user replacement\n")
                os.replace(replacement, target)

                for operation in ("replace", "delete"):
                    with self.subTest(operation=operation):
                        with self.assertRaises(ObsidianVaultError) as caught:
                            if operation == "replace":
                                bound.replace_managed(
                                    relative, b"new managed\n", published.identity
                                )
                            else:
                                bound.delete_managed(relative, published.identity)
                        self.assertEqual("OBSIDIAN_TARGET_CHANGED", caught.exception.code)
                        self.assertEqual(b"user replacement\n", target.read_bytes())

                current = bound.bind_target(relative)
                updated = bound.replace_managed(relative, b"new managed\n", current)
                self.assertEqual(b"new managed\n", target.read_bytes())
                self.assertNotEqual(current.opaque_id, updated.identity.opaque_id)
                bound.delete_managed(relative, updated.identity)
                self.assertFalse(target.exists())
                self.assertEqual([], list(target.parent.glob(".*.tmp")))
                self.assertEqual([], list(target.parent.glob(".*.bak")))


if __name__ == "__main__":
    unittest.main()
