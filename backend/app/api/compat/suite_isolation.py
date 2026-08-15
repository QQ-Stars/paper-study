from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Sequence

from backend.app.api.compat.database_identity import (
    DatabaseIdentityError,
    canonical_json_bytes,
    exclusive_write_bytes,
)


_SUITE_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SuiteIsolationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class SuiteIsolation:
    suite_key: str
    manifest_path: Path
    sandbox_root: Path
    database_path: Path
    settings_path: Path
    pdf_root: Path
    vault_root: Path
    keyring_root: Path
    denied_live_paths: tuple[Path, ...]
    deny_network: bool
    deny_providers: bool
    _live_access_count: int = field(default=0, repr=False)

    @property
    def live_access_count(self) -> int:
        return self._live_access_count

    def guard_path(self, path: str | os.PathLike[str]) -> Path:
        candidate = Path(path).resolve(strict=False)
        if any(_is_same_or_below(candidate, denied) for denied in self.denied_live_paths):
            self._deny()
        return candidate

    def guard_sqlite(self, path: str | os.PathLike[str]) -> Path:
        return self.guard_path(path)

    def guard_network(self) -> None:
        if self.deny_network:
            self._deny()

    def guard_provider(self) -> None:
        if self.deny_providers:
            self._deny()

    def _deny(self) -> None:
        self._live_access_count += 1
        raise SuiteIsolationError(
            "SUITE_LIVE_ACCESS_DENIED",
            "The suite attempted to access a denied Live resource.",
        )


def create_suite_isolation(
    *,
    run_manifest: str | os.PathLike[str],
    expected_run_manifest_sha256: str,
    suite_key: str,
    output: str | os.PathLike[str],
    deny_live_paths: Sequence[str | os.PathLike[str]],
    deny_network: bool,
    deny_providers: bool,
) -> SuiteIsolation:
    manifest = Path(run_manifest).resolve(strict=True)
    payload = manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_run_manifest_sha256:
        raise SuiteIsolationError("SUITE_RUN_MISMATCH", "The run manifest hash does not match.")
    try:
        run_document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SuiteIsolationError("SUITE_RUN_INVALID", "The run manifest is invalid.") from error
    run_root = manifest.parent
    if (
        not isinstance(run_document, dict)
        or run_document.get("manifestKind") != "evidence-run"
        or manifest.name != "evidence-run-manifest-v1.json"
        or not run_root.name.startswith("run-")
    ):
        raise SuiteIsolationError("SUITE_RUN_INVALID", "The run manifest is not canonical.")
    if not isinstance(suite_key, str) or not _SUITE_KEY.fullmatch(suite_key):
        raise SuiteIsolationError("SUITE_KEY_INVALID", "The suite key is invalid.")
    output_path = Path(output).resolve(strict=False)
    if output_path.parent.resolve(strict=True) != run_root or output_path.exists():
        raise SuiteIsolationError("SUITE_OUTPUT_INVALID", "The isolation output must be new and run-local.")
    sandbox = run_root / f"isolation-{suite_key}"
    try:
        sandbox.mkdir(mode=0o700)
    except FileExistsError as error:
        raise SuiteIsolationError("SUITE_ISOLATION_EXISTS", "The suite sandbox already exists.") from error
    database = sandbox / "app.db"
    settings = sandbox / "settings.json"
    pdf_root = sandbox / "pdfs"
    vault_root = sandbox / "vault"
    keyring_root = sandbox / "keyring"
    database.touch(exist_ok=False)
    settings.write_bytes(b"{}")
    for directory in (pdf_root, vault_root, keyring_root):
        directory.mkdir()
    denied = tuple(Path(value).resolve(strict=True) for value in deny_live_paths)
    document = {
        "schemaVersion": 1,
        "manifestKind": "suite-isolation",
        "suiteKey": suite_key,
        "runManifestPath": str(manifest),
        "runManifestSha256": expected_run_manifest_sha256,
        "sandboxRoot": str(sandbox.resolve(strict=True)),
        "databasePath": str(database.resolve(strict=True)),
        "settingsPath": str(settings.resolve(strict=True)),
        "pdfRoot": str(pdf_root.resolve(strict=True)),
        "vaultRoot": str(vault_root.resolve(strict=True)),
        "keyringRoot": str(keyring_root.resolve(strict=True)),
        "deniedLivePaths": [str(path) for path in denied],
        "denyNetwork": bool(deny_network),
        "denyProviders": bool(deny_providers),
        "liveAccessCount": 0,
    }
    try:
        exclusive_write_bytes(output_path, canonical_json_bytes(document))
    except DatabaseIdentityError as error:
        raise SuiteIsolationError(error.code, str(error)) from error
    return SuiteIsolation(
        suite_key=suite_key,
        manifest_path=output_path.resolve(strict=True),
        sandbox_root=sandbox.resolve(strict=True),
        database_path=database.resolve(strict=True),
        settings_path=settings.resolve(strict=True),
        pdf_root=pdf_root.resolve(strict=True),
        vault_root=vault_root.resolve(strict=True),
        keyring_root=keyring_root.resolve(strict=True),
        denied_live_paths=denied,
        deny_network=bool(deny_network),
        deny_providers=bool(deny_providers),
    )


def _is_same_or_below(candidate: Path, denied: Path) -> bool:
    if candidate == denied:
        return True
    if denied.is_dir():
        try:
            candidate.relative_to(denied)
            return True
        except ValueError:
            pass
    return False


__all__ = ["SuiteIsolation", "SuiteIsolationError", "create_suite_isolation"]
