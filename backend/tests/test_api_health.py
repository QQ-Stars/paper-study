from __future__ import annotations

import asyncio
from contextlib import closing
import inspect
import sqlite3
import unittest
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api.app import create_app
from backend.tests.support.p3_database import p3_database_fixture


class ApiHealthTests(unittest.TestCase):
    def test_application_container_disposes_shared_engine_once(self) -> None:
        from backend.app.bootstrap import ApplicationContainer

        async def scenario() -> None:
            calls: list[str] = []

            class Engine:
                async def dispose(self) -> None:
                    calls.append("dispose")

            container = ApplicationContainer(
                schema_revision="20260807_03",
                session_factory=SimpleNamespace(kw={"bind": Engine()}),
            )

            await container.dispose()
            await container.dispose()

            self.assertEqual(["dispose"], calls)

        asyncio.run(scenario())

    def test_import_does_not_open_database_or_start_runtime(self) -> None:
        script = r'''
import asyncio
import fastapi
import platform
import socket
import sqlite3
import subprocess
import threading

platform.uname()

def forbidden(*_args, **_kwargs):
    raise AssertionError("import attempted a runtime side effect")

class GuardedSocket(socket.socket):
    def bind(self, *_args, **_kwargs):
        forbidden()

sqlite3.connect = forbidden
socket.socket = GuardedSocket
subprocess.Popen = forbidden
threading.Thread.start = forbidden
import backend.app.api.app
print("import-ok")
'''
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        self.assertEqual(
            (completed.returncode, completed.stdout.strip()),
            (0, "import-ok"),
            completed.stderr,
        )

    def test_liveness_does_not_require_database(self) -> None:
        from backend.app.api.dependencies import ApiDependencies
        from backend.app.runtime import ApiSettings

        class Container:
            schema_revision = "20260807_03"

            async def dispose(self) -> None:
                return None

        app = create_app(
            ApiSettings.for_tests(),
            ApiDependencies(Container(), object()),
            required_schema_revision="20260807_03",
        )
        with TestClient(app) as client:
            response = client.get("/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_on_expected_head(self) -> None:
        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-ready-") as fixture:
                class Container:
                    schema_revision = "20260807_03"

                    async def dispose(self) -> None:
                        return None

                app = create_app(
                    Container(),
                    fixture.session_factory,
                    required_schema_revision="20260807_03",
                )
                with TestClient(app) as client:
                    response = client.get("/health/ready")

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json(),
                    {"status": "ready", "schemaRevision": "20260807_03"},
                )

        asyncio.run(scenario())

    def test_readiness_rejects_missing_migration_head(self) -> None:
        asyncio.run(self._assert_readiness_rejected((), actual_revision="missing"))

    def test_readiness_rejects_wrong_migration_head(self) -> None:
        asyncio.run(
            self._assert_readiness_rejected(
                ("20260807_02",),
                actual_revision="20260807_02",
            )
        )

    def test_readiness_rejects_multiple_migration_heads(self) -> None:
        asyncio.run(
            self._assert_readiness_rejected(
                ("20260807_03", "unexpected_head"),
                actual_revision="20260807_03,unexpected_head",
            )
        )

    def test_api_worker_scheduler_bootstrap_revision_matrix_fails_before_side_effects(
        self,
    ) -> None:
        from backend.app.config import DatabaseSettings
        from backend.app.domain import SchemaRevisionMismatchError
        from backend.app.runtime import (
            ProcessRuntimeSettings,
            RolePorts,
            bootstrap_process_role,
        )

        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-role-matrix-") as fixture:
                states = {
                    "missing": (),
                    "multiple": ("20260807_03", "unexpected_head"),
                    "wrong": ("20260807_02",),
                    "exact": ("20260807_03",),
                }
                for state, revisions in states.items():
                    with closing(sqlite3.connect(fixture.database_path)) as connection:
                        connection.execute("DELETE FROM alembic_version")
                        connection.executemany(
                            "INSERT INTO alembic_version(version_num) VALUES (?)",
                            ((revision,) for revision in revisions),
                        )
                        connection.commit()

                    for role in ("api", "worker", "scheduler"):
                        with self.subTest(role=role, revision_state=state):
                            reached: list[str] = []
                            ports = RolePorts(
                                api=lambda: reached.append("api"),
                                worker=lambda: reached.append("worker"),
                                scheduler=lambda: reached.append("scheduler"),
                            )
                            settings = ProcessRuntimeSettings(
                                database=DatabaseSettings(fixture.database_path),
                                process_role=role,
                            )
                            if state == "exact":
                                result = bootstrap_process_role(
                                    settings,
                                    ports,
                                    required_schema_revision="20260807_03",
                                )
                                self.assertEqual(role, result)
                                self.assertEqual([role], reached)
                            else:
                                with self.assertRaises(
                                    SchemaRevisionMismatchError
                                ) as caught:
                                    bootstrap_process_role(
                                        settings,
                                        ports,
                                        required_schema_revision="20260807_03",
                                    )
                                self.assertEqual(
                                    "SCHEMA_REVISION_MISMATCH",
                                    caught.exception.code,
                                )
                                self.assertEqual([], reached)

        asyncio.run(scenario())

    def test_default_bind_and_host_policy_are_loopback_only(self) -> None:
        from backend.app.api.middleware.local_access import (
            LocalAccessMiddleware,
            LocalAccessPolicy,
        )
        from backend.app.config import DatabaseSettings
        from backend.app.runtime import ProcessRuntimeSettings

        async def scenario() -> None:
            async with p3_database_fixture(prefix="study-app-p4-local-host-") as fixture:
                database = DatabaseSettings(fixture.database_path)
                settings = ProcessRuntimeSettings.from_environment(
                    database,
                    {"API_PROCESS_ROLE": "api"},
                )
                self.assertEqual("127.0.0.1", settings.bind_host)
                self.assertFalse(settings.allow_remote_access)
                with self.assertRaises(ValueError):
                    ProcessRuntimeSettings.from_environment(
                        database,
                        {
                            "API_PROCESS_ROLE": "api",
                            "API_BIND_HOST": "0.0.0.0",
                        },
                    )
                opted_in = ProcessRuntimeSettings.from_environment(
                    database,
                    {
                        "API_PROCESS_ROLE": "api",
                        "API_BIND_HOST": "0.0.0.0",
                        "ALLOW_REMOTE_ACCESS": "1",
                    },
                )
                self.assertTrue(opted_in.allow_remote_access)

                app = FastAPI()
                app.add_middleware(
                    LocalAccessMiddleware,
                    policy=LocalAccessPolicy("127.0.0.1", 8000),
                )

                @app.get("/health/live")
                async def health() -> dict[str, str]:
                    return {"status": "ok"}

                with TestClient(app, base_url="http://127.0.0.1:8000") as client:
                    accepted = client.get("/health/live")
                    localhost = client.get(
                        "/health/live",
                        headers={"Host": "localhost:8000"},
                    )
                    forwarded = client.get(
                        "/health/live",
                        headers={
                            "Host": "attacker.example:8000",
                            "Forwarded": "host=127.0.0.1:8000",
                            "X-Forwarded-Host": "127.0.0.1:8000",
                        },
                    )
                    ambiguous = client.get(
                        "/health/live",
                        headers={"Host": "::1:8000"},
                    )
                    userinfo = client.get(
                        "/health/live",
                        headers={"Host": "user@127.0.0.1:8000"},
                    )

                self.assertEqual(200, accepted.status_code)
                self.assertEqual(200, localhost.status_code)
                for response in (forwarded, ambiguous, userinfo):
                    self.assertEqual(400, response.status_code, response.text)
                    self.assertEqual("LOCAL_ACCESS_DENIED", response.json()["error"]["code"])

        asyncio.run(scenario())

    def test_state_changing_requests_reject_untrusted_origin(self) -> None:
        from backend.app.api.middleware.local_access import (
            LocalAccessMiddleware,
            LocalAccessPolicy,
        )

        app = FastAPI()
        app.add_middleware(
            LocalAccessMiddleware,
            policy=LocalAccessPolicy("127.0.0.1", 8000),
        )

        @app.post("/mutation")
        async def mutation() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            no_origin = client.post("/mutation")
            same_origin = client.post(
                "/mutation",
                headers={"Origin": "http://127.0.0.1:8000"},
            )
            cross_origin = client.post(
                "/mutation",
                headers={
                    "Origin": "https://attacker.example",
                    "Referer": "http://127.0.0.1:8000/",
                    "X-Forwarded-Host": "attacker.example",
                },
            )
            reordered = client.post(
                "/mutation",
                headers={"Origin": "http://localhost:8000"},
            )
            unsafe_origin = client.post(
                "/mutation",
                headers={"Origin": "http://user@127.0.0.1:8000"},
            )

        self.assertEqual(200, no_origin.status_code)
        self.assertEqual(200, same_origin.status_code)
        for response in (cross_origin, reordered, unsafe_origin):
            self.assertEqual(403, response.status_code, response.text)
            self.assertEqual("LOCAL_ORIGIN_DENIED", response.json()["error"]["code"])

    def test_factory_requires_explicit_schema_revision(self) -> None:
        parameters = inspect.signature(create_app).parameters
        self.assertEqual(["settings", "dependencies"], list(parameters)[:2])
        revision = parameters["required_schema_revision"]
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, revision.kind)
        self.assertIs(inspect.Parameter.empty, revision.default)

        container = SimpleNamespace(schema_revision="20260807_03")
        session_factory = object()

        with self.assertRaises(TypeError):
            create_app(container, session_factory)
        with self.assertRaises(TypeError):
            create_app(container, session_factory, "20260807_03")

    async def _assert_readiness_rejected(
        self,
        revisions: tuple[str, ...],
        *,
        actual_revision: str,
    ) -> None:
        async with p3_database_fixture(prefix="study-app-p4-unready-") as fixture:
            async with fixture.session_factory() as session:
                await session.execute(text("DELETE FROM alembic_version"))
                for revision in revisions:
                    await session.execute(
                        text(
                            "INSERT INTO alembic_version(version_num) VALUES (:revision)"
                        ),
                        {"revision": revision},
                    )
                await session.commit()

            class Container:
                schema_revision = "20260807_03"

                async def dispose(self) -> None:
                    return None

            app = create_app(
                Container(),
                fixture.session_factory,
                required_schema_revision="20260807_03",
            )
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/health/ready")

            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(
                response.json(),
                {
                    "error": {
                        "code": "SCHEMA_REVISION_MISMATCH",
                        "message": "The database schema revision is incompatible.",
                        "details": {
                            "expected_revision": "20260807_03",
                            "actual_revision": actual_revision,
                        },
                    }
                },
            )
            self.assertNotIn(str(fixture.database_path), response.text)
