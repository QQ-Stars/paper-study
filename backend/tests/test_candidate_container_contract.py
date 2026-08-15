from __future__ import annotations

import asyncio
from contextlib import closing
import http.client
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.tests.support.p4_identity import p4_identity_fixture, sha256_file


_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_PROFILE = "p4-candidate"
_CANDIDATE_SERVICES = {
    "candidate-api": "api",
    "candidate-worker": "worker",
    "candidate-scheduler": "scheduler",
}


class CandidateApiPortForwardingContractTests(unittest.TestCase):
    def test_random_loopback_publish_is_reachable_without_remote_host_access(
        self,
    ) -> None:
        compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            """      API_BIND_HOST: 0.0.0.0
      API_BIND_PORT: "8000"
      API_LOOPBACK_PORT_FORWARDING: "1"
      API_LOOPBACK_FORWARDER_HOSTS: default-gateway
      ALLOW_REMOTE_ACCESS: "0"
""",
            compose,
        )
        self.assertIn(
            """        target: 8000
        published: "0"
        protocol: tcp
        host_ip: 127.0.0.1
""",
            compose,
        )

        from backend.app.api.app import create_app
        from backend.app.api.dependencies import ApiDependencies
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import ProcessRuntimeSettings

        with tempfile.TemporaryDirectory(
            prefix="study-app-p4-forwarded-host-"
        ) as temporary:
            database_path = Path(temporary) / "candidate.db"
            database_path.touch()
            runtime = ProcessRuntimeSettings.from_environment(
                DatabaseSettings(database_path),
                {
                    "API_PROCESS_ROLE": "api",
                    "API_BIND_HOST": "0.0.0.0",
                    "API_BIND_PORT": "8000",
                    "API_LOOPBACK_PORT_FORWARDING": "1",
                    "API_LOOPBACK_FORWARDER_HOSTS": "172.20.0.1",
                    "ALLOW_REMOTE_ACCESS": "0",
                },
            )

        settings = runtime.api_settings()
        self.assertEqual("0.0.0.0", settings.bind_host)
        self.assertTrue(settings.loopback_port_forwarding)
        self.assertFalse(settings.allow_remote_access)

        class Container:
            schema_revision = "20260807_03"

            async def dispose(self) -> None:
                return None

        app = create_app(
            settings,
            ApiDependencies(Container(), object()),
            required_schema_revision="20260807_03",
        )
        with TestClient(
            app,
            base_url="http://127.0.0.1:49152",
            client=("172.20.0.1", 49152),
        ) as client:
            ipv4 = client.get("/health/live")
            localhost = client.get(
                "/health/live",
                headers={"Host": "localhost:53217"},
            )
            lan = client.get(
                "/health/live",
                headers={"Host": "192.168.1.25:49152"},
            )
            container_name = client.get(
                "/health/live",
                headers={"Host": "candidate-api:8000"},
            )
            forwarded = client.get(
                "/health/live",
                headers={
                    "Host": "attacker.example:49152",
                    "Forwarded": "host=127.0.0.1:49152",
                    "X-Forwarded-Host": "127.0.0.1:49152",
                },
            )
        with TestClient(
            app,
            base_url="http://127.0.0.1:49152",
            client=("172.20.0.3", 49152),
        ) as peer:
            peer_read = peer.get(
                "/health/live",
                headers={"Host": "127.0.0.1:49152"},
            )
            peer_write = peer.post(
                "/api/progress",
                headers={"Host": "127.0.0.1:49152"},
                json={"id": "paper-1", "status": "已理解"},
            )

        self.assertEqual(200, ipv4.status_code, ipv4.text)
        self.assertEqual(200, localhost.status_code, localhost.text)
        for response in (lan, container_name, forwarded):
            self.assertEqual(400, response.status_code, response.text)
            self.assertEqual(
                "LOCAL_ACCESS_DENIED",
                response.json()["error"]["code"],
            )
        for response in (peer_read, peer_write):
            self.assertEqual(400, response.status_code, response.text)
            self.assertEqual("LOCAL_ACCESS_DENIED", response.json()["error"]["code"])


class ContainerIdentityRebindTests(unittest.TestCase):
    def test_runtime_identity_rebinds_mounted_evidence_without_live_database_access(
        self,
    ) -> None:
        from backend.app.api.compat.database_identity import (
            ContainerDatabaseIdentityService,
            DatabaseEvidenceIdentityService,
            verify_database_evidence_identity_subject,
        )
        from backend.app.cli.runtime_owner import RuntimeOwnerService
        from backend.app.infrastructure.database_backup import create_verified_backup
        from backend.app.providers.runtime_lease import (
            ProcessEvidence,
            RuntimeProcessSnapshot,
        )

        with p4_identity_fixture() as fixture:
            identity_service = DatabaseEvidenceIdentityService()
            live_identity = fixture.root / "live-database-identity-v1.json"
            live = identity_service.create_live_database_identity(
                database=fixture.database_path,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                output=live_identity,
            )
            backup = create_verified_backup(
                fixture.database_path,
                fixture.root / "candidate-backups",
                label="container-rebind",
            )
            shutil.copyfile(backup.backup_path, fixture.candidate_database_path)
            host_identity = fixture.root / "host-candidate-identity.json"
            host = identity_service.create_descendant_database_identity(
                database=fixture.candidate_database_path,
                subject_kind="p4_candidate",
                parent_database_identity_manifest=live_identity,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                output=host_identity,
            )
            owner_marker = fixture.root / "production-owner.json"
            owner_process = ProcessEvidence(
                pid=os.getpid(),
                executable_path=Path(sys.executable).resolve(),
                entrypoint_path=fixture.entrypoint_path,
                cwd=fixture.entrypoint_path.parent,
                argv=("node", str(fixture.entrypoint_path)),
                listener_host="127.0.0.1",
                listener_port=43123,
                database_paths=(fixture.database_path,),
                process_role="node",
                environment="live",
            )
            owner = RuntimeOwnerService(
                type(
                    "Inspector",
                    (),
                    {
                        "snapshot": lambda _self: RuntimeProcessSnapshot(
                            node_processes=(owner_process,),
                        )
                    },
                )()
            ).initialize_node_owner(
                database_identity_manifest=live_identity,
                p0_origin_receipt=fixture.receipt_path,
                expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                origin_backup=fixture.origin_backup_path,
                origin_manifest=fixture.origin_manifest_path,
                runtime_namespace="production",
                expected_entrypoint_path=fixture.entrypoint_path,
                owner_marker=owner_marker,
            )

            mounted = fixture.root / "container-mount"
            mounted.mkdir()
            runtime_database = mounted / "data-app.db"
            mounted_parent = mounted / "parent-live-identity.json"
            mounted_receipt = mounted / "p0-origin-receipt-v1.json"
            runtime_identity = mounted / "runtime-database-identity.json"
            shutil.copyfile(backup.backup_path, runtime_database)
            shutil.copyfile(live_identity, mounted_parent)
            shutil.copyfile(fixture.receipt_path, mounted_receipt)

            service = ContainerDatabaseIdentityService()
            verified = service.ensure_runtime_identity(
                database=runtime_database,
                host_database_identity_manifest=host_identity,
                parent_database_identity_manifest=mounted_parent,
                origin_receipt=mounted_receipt,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                owner=owner,
                output=runtime_identity,
            )
            manifest = verified.manifest
            self.assertEqual("container_runtime_rebind", verified.verification_mode)
            self.assertEqual(runtime_database.resolve(), manifest.database_path)
            self.assertEqual(mounted_parent.resolve(), manifest.parent_database_identity_manifest_path)
            self.assertEqual(mounted_receipt.resolve(), manifest.origin_receipt_path)
            self.assertEqual(live.subject_database_id, manifest.parent_subject_database_id)
            self.assertEqual(host.database_lineage_id, manifest.database_lineage_id)
            self.assertNotEqual(host.subject_database_id, manifest.subject_database_id)
            verify_database_evidence_identity_subject(
                database=runtime_database,
                identity=manifest,
            )

            before = (
                runtime_identity.read_bytes(),
                sha256_file(runtime_identity),
                runtime_identity.stat().st_mtime_ns,
            )
            reused = service.ensure_runtime_identity(
                database=runtime_database,
                host_database_identity_manifest=host_identity,
                parent_database_identity_manifest=mounted_parent,
                origin_receipt=mounted_receipt,
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
                owner=owner,
                output=runtime_identity,
            )
            self.assertEqual(manifest.subject_database_id, reused.subject_database_id)
            self.assertEqual(
                before,
                (
                    runtime_identity.read_bytes(),
                    sha256_file(runtime_identity),
                    runtime_identity.stat().st_mtime_ns,
                ),
            )
            unmounted_live = fixture.root / "unmounted-live.db"
            fixture.database_path.rename(unmounted_live)
            from backend.app.config import DatabaseSettings
            from backend.app.runtime import CandidateRuntimeGuard

            accepted = CandidateRuntimeGuard().validate_role(
                reused,
                database=DatabaseSettings(runtime_database),
                environment="candidate",
                runtime_namespace="p4-container-rebind",
                role="api",
                parent_backup=backup.backup_path,
                parent_manifest=backup.manifest_path,
            )
            self.assertEqual(manifest.subject_database_id, accepted.subject_database_id)

            invalid_lease_root = mounted / "lease-root-is-a-file"
            invalid_lease_root.write_text("sentinel\n", encoding="utf-8")
            from backend.app.cli.candidate_runtime import run as run_candidate

            stderr = StringIO()
            result = asyncio.run(
                run_candidate(
                    ["--role", "api"],
                    environment={
                        "API_PROCESS_ROLE": "api",
                        "RUNTIME_ENVIRONMENT": "candidate",
                        "RUNTIME_NAMESPACE": "p4-container-rebind",
                        "DB_PATH": str(runtime_database),
                        "DATABASE_IDENTITY_MANIFEST": str(runtime_identity),
                        "CANDIDATE_HOST_IDENTITY_MANIFEST": str(host_identity),
                        "CANDIDATE_PARENT_IDENTITY_MANIFEST": str(mounted_parent),
                        "CANDIDATE_ORIGIN_RECEIPT": str(mounted_receipt),
                        "CANDIDATE_PARENT_BACKUP": str(backup.backup_path),
                        "CANDIDATE_PARENT_MANIFEST": str(backup.manifest_path),
                        "PRODUCTION_OWNER_MARKER": str(owner_marker),
                        "RUNTIME_LEASE_DIR": str(invalid_lease_root),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                    },
                    stderr=stderr,
                )
            )
            self.assertEqual(2, result)
            self.assertEqual(
                "RUNTIME_LEASE_DIR_INVALID",
                json.loads(stderr.getvalue())["error"]["code"],
            )


class CandidateContainerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        configured = os.environ.get("P4_DOCKER_EXE")
        docker = configured or shutil.which("docker")
        if docker is None or not Path(docker).is_file():
            self.fail(
                "Docker CLI is required for the resolved P4 container contract; "
                "this gate must not be skipped."
            )
        self.docker = str(Path(docker).resolve())
        self._temporary = tempfile.TemporaryDirectory(
            prefix="study-app-p4-compose-"
        )
        self.addCleanup(self._temporary.cleanup)
        self.fixture_root = Path(self._temporary.name).resolve()
        self.docker_config = self.fixture_root / "docker-config"
        self.candidate_data = self.fixture_root / "candidate-data"
        self.candidate_runtime = self.fixture_root / "candidate-runtime"
        self.candidate_identity = self.fixture_root / "candidate-identity-v1.json"
        self.candidate_parent_identity = self.fixture_root / "parent-identity-v1.json"
        self.candidate_origin_receipt = self.fixture_root / "p0-origin-receipt-v1.json"
        self.candidate_parent_backup = self.fixture_root / "candidate-parent.sqlite3"
        self.candidate_parent_manifest = self.fixture_root / "candidate-parent.json"
        self.owner_marker = self.fixture_root / "production-owner.json"
        self.docker_config.mkdir()
        self.candidate_data.mkdir()
        self.candidate_runtime.mkdir()
        self.candidate_identity.write_text("{}\n", encoding="utf-8")
        self.candidate_parent_identity.write_text("{}\n", encoding="utf-8")
        self.candidate_origin_receipt.write_text("{}\n", encoding="utf-8")
        self.candidate_parent_backup.write_bytes(b"candidate parent backup")
        self.candidate_parent_manifest.write_text("{}\n", encoding="utf-8")
        self.owner_marker.write_text("{}\n", encoding="utf-8")

    def test_resolved_default_compose_keeps_node_as_live_owner(self) -> None:
        services = self._resolved_services(profile=None)

        self.assertEqual({"app"}, set(services))
        app = services["app"]
        self.assertEqual(["node", "server.js"], app.get("command"))
        self.assertEqual("frozen-node", app.get("build", {}).get("target"))
        self.assertEqual("/app/data/app.db", app["environment"]["DB_PATH"])
        self.assertEqual("legacy", app["environment"]["API_BACKEND_MODE"])
        self.assertNotIn("profiles", app)

    def test_resolved_p4_candidate_profile_is_isolated_role_scoped_and_loopback_only(
        self,
    ) -> None:
        services = self._resolved_services(profile=_CANDIDATE_PROFILE)
        candidates = {
            name: services[name]
            for name in services
            if name in _CANDIDATE_SERVICES
        }

        self.assertEqual(set(_CANDIDATE_SERVICES), set(candidates))
        namespaces: set[str] = set()
        database_paths: set[str] = set()
        identity_paths: set[str] = set()
        for service_name, role in _CANDIDATE_SERVICES.items():
            service = candidates[service_name]
            self.assertEqual([_CANDIDATE_PROFILE], service.get("profiles"))
            environment = service["environment"]
            self.assertEqual("candidate", environment.get("RUNTIME_ENVIRONMENT"))
            self.assertEqual(role, environment.get("API_PROCESS_ROLE"))
            self.assertNotEqual("production", environment.get("RUNTIME_NAMESPACE"))
            namespaces.add(environment["RUNTIME_NAMESPACE"])
            database_paths.add(environment["DB_PATH"])
            identity_paths.add(environment["DATABASE_IDENTITY_MANIFEST"])
            self.assertEqual(
                "/candidate/evidence/host-database-identity-v1.json",
                environment.get("CANDIDATE_HOST_IDENTITY_MANIFEST"),
            )
            self.assertEqual(
                "/candidate/evidence/parent-database-identity-v1.json",
                environment.get("CANDIDATE_PARENT_IDENTITY_MANIFEST"),
            )
            self.assertEqual(
                "/candidate/evidence/p0-origin-receipt-v1.json",
                environment.get("CANDIDATE_ORIGIN_RECEIPT"),
            )
            self.assertEqual(
                "/candidate/evidence/parent-backup.sqlite3",
                environment.get("CANDIDATE_PARENT_BACKUP"),
            )
            self.assertEqual(
                "/candidate/evidence/parent-manifest.json",
                environment.get("CANDIDATE_PARENT_MANIFEST"),
            )
            self._assert_candidate_mounts_are_isolated(service)
            serialized = json.dumps(service, sort_keys=True).lower()
            self.assertNotIn('"runtime_environment": "live"', serialized)
            self.assertNotIn('"runtime_namespace": "production"', serialized)

        self.assertEqual({"p4-contract-test"}, namespaces)
        self.assertEqual({"/candidate/data/app.db"}, database_paths)
        self.assertEqual(
            {"/candidate/runtime/database-identity-v1.json"},
            identity_paths,
        )

        api = candidates["candidate-api"]
        self.assertEqual("0.0.0.0", api["environment"].get("API_BIND_HOST"))
        self.assertEqual("8000", api["environment"].get("API_BIND_PORT"))
        self.assertEqual(
            "1",
            api["environment"].get("API_LOOPBACK_PORT_FORWARDING"),
        )
        self.assertEqual("0", api["environment"].get("ALLOW_REMOTE_ACCESS"))
        self.assertEqual(
            [
                {
                    "mode": "ingress",
                    "target": 8000,
                    "published": "0",
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                }
            ],
            api.get("ports"),
        )
        self.assertNotIn("ports", candidates["candidate-worker"])
        self.assertNotIn("ports", candidates["candidate-scheduler"])

    def test_resolved_candidate_build_targets_exist_and_match_role_commands(
        self,
    ) -> None:
        services = self._resolved_services(profile=_CANDIDATE_PROFILE)
        self.assertEqual(
            "frozen-node",
            services.get("app", {}).get("build", {}).get("target"),
        )
        for service_name, role in _CANDIDATE_SERVICES.items():
            service = services[service_name]
            self.assertEqual("fastapi-candidate", service["build"].get("target"))
            command = service.get("command")
            self.assertIsInstance(command, list)
            self.assertIn(role, " ".join(command))
            self.assertEqual(role, service["environment"].get("API_PROCESS_ROLE"))

        self._buildkit_check("frozen-node")
        self._buildkit_check("fastapi-candidate")

    def test_resolved_candidate_commands_start_real_isolated_roles(self) -> None:
        from backend.app.api.compat.database_identity import (
            DatabaseEvidenceIdentityService,
        )
        from backend.app.infrastructure.database_backup import create_verified_backup

        async def scenario() -> None:
            services = self._resolved_services(profile=_CANDIDATE_PROFILE)
            with p4_identity_fixture() as fixture:
                with closing(sqlite3.connect(fixture.database_path)) as connection:
                    connection.execute("UPDATE job_schedules SET enabled = 0")
                    connection.commit()
                identity_service = DatabaseEvidenceIdentityService()
                live_identity = fixture.root / "live-database-identity-v1.json"
                identity_service.create_live_database_identity(
                    database=fixture.database_path,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                    output=live_identity,
                )
                backup = create_verified_backup(
                    fixture.database_path,
                    fixture.root / "candidate-backups",
                    label="candidate-startup",
                )
                shutil.copyfile(backup.backup_path, fixture.candidate_database_path)
                candidate_identity = fixture.root / "candidate-database-identity-v1.json"
                identity_service.create_descendant_database_identity(
                    database=fixture.candidate_database_path,
                    subject_kind="p4_candidate",
                    parent_database_identity_manifest=live_identity,
                    parent_backup=backup.backup_path,
                    parent_manifest=backup.manifest_path,
                    output=candidate_identity,
                )
                from backend.app.cli.runtime_owner import RuntimeOwnerService
                from backend.app.providers.runtime_lease import (
                    ProcessEvidence,
                    RuntimeProcessSnapshot,
                )

                owner_process = ProcessEvidence(
                    pid=os.getpid(),
                    executable_path=Path(sys.executable).resolve(),
                    entrypoint_path=fixture.entrypoint_path,
                    cwd=fixture.entrypoint_path.parent,
                    argv=("node", str(fixture.entrypoint_path)),
                    listener_host="127.0.0.1",
                    listener_port=43123,
                    database_paths=(fixture.database_path,),
                    process_role="node",
                    environment="live",
                )
                owner_marker = fixture.root / "production-owner.json"
                RuntimeOwnerService(
                    type(
                        "Inspector",
                        (),
                        {
                            "snapshot": lambda _self: RuntimeProcessSnapshot(
                                node_processes=(owner_process,),
                            )
                        },
                    )()
                ).initialize_node_owner(
                    database_identity_manifest=live_identity,
                    p0_origin_receipt=fixture.receipt_path,
                    expected_p0_origin_receipt_sha256=fixture.receipt_file_sha256,
                    origin_backup=fixture.origin_backup_path,
                    origin_manifest=fixture.origin_manifest_path,
                    runtime_namespace="production",
                    expected_entrypoint_path=fixture.entrypoint_path,
                    owner_marker=owner_marker,
                )
                runtime_root = fixture.root / "candidate-runtime"
                runtime_root.mkdir()
                port = _available_loopback_port()
                environment = os.environ.copy()
                environment.update(
                    {
                        "RUNTIME_ENVIRONMENT": "candidate",
                        "RUNTIME_NAMESPACE": "p4-startup-tracer",
                        "DB_PATH": str(fixture.candidate_database_path),
                        "DATABASE_IDENTITY_MANIFEST": str(candidate_identity),
                        "CANDIDATE_PARENT_BACKUP": str(backup.backup_path),
                        "CANDIDATE_PARENT_MANIFEST": str(backup.manifest_path),
                        "PRODUCTION_OWNER_MARKER": str(owner_marker),
                        "RUNTIME_LEASE_DIR": str(runtime_root / "leases"),
                        "REQUIRED_SCHEMA_REVISION": "20260807_03",
                        "PROCESSING_CURSOR_SECRET": "p4-startup-tracer-cursor-secret-0001",
                        "API_BIND_HOST": "127.0.0.1",
                        "API_BIND_PORT": str(port),
                        "ALLOW_REMOTE_ACCESS": "0",
                        "OCR_ENABLED": "0",
                        "CANDIDATE_CONTROL_STDIN": "1",
                    }
                )

                for service_name, role in _CANDIDATE_SERVICES.items():
                    with self.subTest(role=role):
                        role_environment = dict(environment)
                        role_environment["API_PROCESS_ROLE"] = role
                        command = _host_python_command(
                            services[service_name]["command"],
                            port=port,
                        )
                        process = await asyncio.create_subprocess_exec(
                            *command,
                            cwd=_ROOT,
                            env=role_environment,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            if role == "api":
                                await _wait_for_api(process, port)
                            await _wait_for_role_lease(process, runtime_root, role)
                        finally:
                            await _stop_process(process)
                        self.assertEqual([], list(runtime_root.rglob("*.json")))

        asyncio.run(scenario())

    def _resolved_services(self, *, profile: str | None) -> dict[str, object]:
        command = [self.docker, "compose"]
        if profile is not None:
            command.extend(["--profile", profile])
        command.extend(["config", "--format", "json"])
        environment = os.environ.copy()
        environment.update(
            {
                "DOCKER_CONFIG": str(self.docker_config),
                "P4_CANDIDATE_DB_DIR": str(self.candidate_data),
                "P4_CANDIDATE_IDENTITY_MANIFEST": str(self.candidate_identity),
                "P4_CANDIDATE_PARENT_IDENTITY_MANIFEST": str(
                    self.candidate_parent_identity
                ),
                "P4_CANDIDATE_ORIGIN_RECEIPT": str(self.candidate_origin_receipt),
                "P4_CANDIDATE_PARENT_BACKUP": str(self.candidate_parent_backup),
                "P4_CANDIDATE_PARENT_MANIFEST": str(self.candidate_parent_manifest),
                "P4_CANDIDATE_RUNTIME_DIR": str(self.candidate_runtime),
                "P4_CANDIDATE_RUNTIME_NAMESPACE": "p4-contract-test",
                "P4_PRODUCTION_OWNER_MARKER": str(self.owner_marker),
            }
        )
        completed = subprocess.run(
            command,
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                "docker compose could not resolve the P4 contract "
                f"(exit {completed.returncode}): {completed.stderr.strip()}"
            )
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"docker compose returned invalid JSON: {error}")
        services = document.get("services")
        self.assertIsInstance(services, dict)
        return services

    def _assert_candidate_mounts_are_isolated(self, service: dict[str, object]) -> None:
        volumes = service.get("volumes")
        self.assertIsInstance(volumes, list)
        by_target = {volume["target"]: volume for volume in volumes}
        self.assertEqual(
            str(self.candidate_data).casefold(),
            str(by_target["/candidate/data"]["source"]).casefold(),
        )
        self.assertFalse(by_target["/candidate/data"].get("read_only", False))
        self.assertEqual(
            str(self.candidate_runtime).casefold(),
            str(by_target["/candidate/runtime"]["source"]).casefold(),
        )
        self.assertFalse(by_target["/candidate/runtime"].get("read_only", False))
        self.assertEqual(
            str(self.candidate_identity).casefold(),
            str(
                by_target["/candidate/evidence/host-database-identity-v1.json"][
                    "source"
                ]
            ).casefold(),
        )
        self.assertTrue(
            by_target["/candidate/evidence/host-database-identity-v1.json"].get(
                "read_only", False
            )
        )
        for target, source in (
            (
                "/candidate/evidence/parent-database-identity-v1.json",
                self.candidate_parent_identity,
            ),
            (
                "/candidate/evidence/p0-origin-receipt-v1.json",
                self.candidate_origin_receipt,
            ),
            ("/candidate/evidence/parent-backup.sqlite3", self.candidate_parent_backup),
            ("/candidate/evidence/parent-manifest.json", self.candidate_parent_manifest),
        ):
            self.assertEqual(
                str(source).casefold(),
                str(by_target[target]["source"]).casefold(),
            )
            self.assertTrue(by_target[target].get("read_only", False))
        self.assertEqual(
            str(self.owner_marker).casefold(),
            str(by_target["/production-evidence/production-owner.json"]["source"]).casefold(),
        )
        self.assertTrue(
            by_target["/production-evidence/production-owner.json"].get(
                "read_only", False
            )
        )
        forbidden_targets = {
            "/app/data",
            "/app/data/app.db",
            "/app/data/compatibility/runtime",
        }
        self.assertTrue(forbidden_targets.isdisjoint(by_target))

    def _buildkit_check(self, target: str) -> None:
        environment = os.environ.copy()
        environment["DOCKER_CONFIG"] = str(self.docker_config)
        command = [
            self.docker,
            "buildx",
            "build",
            "--check",
        ]
        local_node_image = environment.get("P4_BUILDKIT_NODE_IMAGE")
        if local_node_image:
            command.extend(["--build-arg", f"NODE_IMAGE={local_node_image}"])
        command.extend(
            [
                "--target",
                target,
                "--file",
                str(_ROOT / "Dockerfile"),
                str(_ROOT),
            ]
        )
        completed = subprocess.run(
            command,
            cwd=_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                f"BuildKit rejected Dockerfile target {target!r} "
                f"(exit {completed.returncode}): {completed.stderr.strip()}"
            )
def _available_loopback_port() -> int:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _host_python_command(command: object, *, port: int) -> list[str]:
    if not isinstance(command, list) or len(command) < 3:
        raise AssertionError(f"candidate command is not executable: {command!r}")
    resolved = [sys.executable, *(str(value) for value in command[1:])]
    if "--port" in resolved:
        resolved[resolved.index("--port") + 1] = str(port)
    return resolved


async def _wait_for_api(process: asyncio.subprocess.Process, port: int) -> None:
    for _attempt in range(100):
        await _raise_if_exited(process, role="api")
        try:
            status, body = await asyncio.to_thread(_read_health, port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        if (status, body) == (200, '{"status":"ok"}'):
            return
        raise AssertionError(f"candidate API health mismatch: {status} {body!r}")
    raise AssertionError("candidate API did not become healthy")


def _read_health(port: int) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.25)
    try:
        connection.request("GET", "/health/live", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


async def _wait_for_role_lease(
    process: asyncio.subprocess.Process,
    runtime_root: Path,
    role: str,
) -> None:
    for _attempt in range(100):
        await _raise_if_exited(process, role=role)
        leases = list(runtime_root.rglob("*.json"))
        if len(leases) == 1:
            document = json.loads(leases[0].read_text(encoding="utf-8"))
            if document.get("role") == role:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"candidate {role} did not publish its role lease")


async def _raise_if_exited(
    process: asyncio.subprocess.Process,
    *,
    role: str,
) -> None:
    if process.returncode is None:
        return
    stdout, stderr = await process.communicate()
    raise AssertionError(
        f"candidate {role} exited during startup with {process.returncode}: "
        f"stdout={stdout.decode('utf-8', errors='replace')!r} "
        f"stderr={stderr.decode('utf-8', errors='replace')!r}"
    )


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        if process.stdin is not None:
            process.stdin.write(b"\n")
            await process.stdin.drain()
            process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)
    if process.stdout is not None:
        await process.stdout.read()
    if process.stderr is not None:
        await process.stderr.read()


if __name__ == "__main__":
    unittest.main()
