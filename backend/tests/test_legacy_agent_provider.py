from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


_AGENT_MAIN = r'''
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

root = Path(os.environ["LEGACY_AGENT_TEST_ROOT"])
command = sys.argv[1]
(root / f"{command}.pid").write_text(str(os.getpid()), encoding="ascii")

if command == "stream":
    print("STAGE::first", file=sys.stderr, flush=True)
    (root / "stream.ready").touch()
    deadline = time.monotonic() + 2.0
    while not (root / "stream.release").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    sys.stdout.write(json.dumps({
        "ok": True,
        "candidates": ["real-candidate"],
        "markdown": "# real markdown",
        "mapping": {"real": "value"},
    }))
    sys.stdout.flush()
elif command in {"cancel", "timeout"}:
    print(f"STAGE::{command}", file=sys.stderr, flush=True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        time.sleep(0.05)
elif command == "fail":
    print("ERR::OCR transport failed", file=sys.stderr, flush=True)
    print(json.dumps({"ok": True, "markdown": "stale"}), flush=True)
    raise SystemExit(3)
'''


class LegacyAgentProviderTests(unittest.TestCase):
    def test_in_process_mode_streams_progress_before_command_finishes(self) -> None:
        """Compatibility mode must preserve the public incremental stream seam."""
        from agent import pipeline
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        def slow_command(*_args, **_kwargs):
            print("STAGE::first", file=sys.stderr, flush=True)
            time.sleep(0.35)
            return []

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)
            with mock.patch.object(pipeline, "search", side_effect=slow_command):
                stream = provider.stream_events(
                    "search",
                    ("--query", "fixture", "--sources", "arxiv"),
                    terminal_fields={"candidates": []},
                    stdout_array_field="candidates",
                )
                first = await asyncio.wait_for(anext(stream), timeout=0.15)
                self.assertEqual(
                    {"type": "progress", "line": "STAGE::first"},
                    first,
                )
                remaining = [event async for event in stream]
                self.assertEqual("result", remaining[-1]["type"])
                self.assertTrue(remaining[-1]["ok"])

        asyncio.run(scenario())

    def test_stream_events_is_incremental_preserves_payload_and_reaps_interruptions(
        self,
    ) -> None:
        asyncio.run(self._assert_stream_contract())

    def test_in_process_mode_applies_provider_environment_snapshot(self) -> None:
        from agent import __main__ as agent_main
        from agent import config as agent_config
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        async def scenario() -> None:
            provider = LegacyAgentProvider(
                in_process=True,
                environment={**os.environ, "DB_PATH": "from-provider.db"},
                timeout_seconds=2.0,
            )
            def fake_main() -> None:
                print(agent_config.DB_PATH, flush=True)

            with mock.patch.object(agent_main, "main", side_effect=fake_main):
                result = await provider.run("env")
            self.assertEqual(0, result.returncode)
            self.assertEqual("from-provider.db", result.stdout.strip())

        asyncio.run(scenario())

    def test_in_process_capture_does_not_swallow_other_thread_output(self) -> None:
        from agent import __main__ as agent_main
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        external = io.StringIO()
        emit = threading.Event()

        def unrelated_output() -> None:
            emit.wait()
            print("outside", flush=True)

        unrelated = threading.Thread(target=unrelated_output)
        unrelated.start()

        def fake_main() -> None:
            emit.set()
            unrelated.join()
            print("inside", flush=True)

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)
            with (
                redirect_stdout(external),
                mock.patch.object(agent_main, "main", side_effect=fake_main),
            ):
                result = await provider.run("capture")
            self.assertEqual("inside", result.stdout.strip())
            self.assertEqual("outside", external.getvalue().strip())

        asyncio.run(scenario())

    def test_in_process_stream_keeps_child_thread_progress_visible(self) -> None:
        from agent import pipeline
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        def child_progress(*_args, **_kwargs):
            child = threading.Thread(
                target=lambda: print("CHILD::progress", file=sys.stderr, flush=True)
            )
            child.start()
            child.join()
            return []

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)
            with mock.patch.object(pipeline, "search", side_effect=child_progress):
                events = [
                    event
                    async for event in provider.stream_events(
                        "search",
                        ("--query", "fixture", "--sources", "arxiv"),
                        terminal_fields={"candidates": []},
                        stdout_array_field="candidates",
                    )
                ]
            self.assertTrue(
                any(event.get("line") == "CHILD::progress" for event in events)
            )

        asyncio.run(scenario())

    def test_in_process_import_failure_is_not_reported_as_success(self) -> None:
        from agent import pipeline
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)
            with mock.patch.object(
                pipeline,
                "ingest_candidates",
                side_effect=RuntimeError("fixture import failed"),
            ):
                events = [
                    event
                    async for event in provider.stream_events(
                        "ingest-selected",
                        stdin="[]",
                        terminal_fields={"added": 0},
                    )
                ]
            self.assertEqual("result", events[-1]["type"])
            self.assertFalse(events[-1]["ok"])
            self.assertEqual("RuntimeError: fixture import failed", events[-1]["error"])

        asyncio.run(scenario())

    def test_in_process_structured_stream_without_terminal_is_failed_once(self) -> None:
        from agent import __main__ as agent_main
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)

            def fake_main() -> None:
                print(json.dumps({"type": "progress", "line": "TITLE::one"}))

            with mock.patch.object(agent_main, "main", side_effect=fake_main):
                events = [
                    event
                    async for event in provider.stream_events(
                        "title-translations",
                        terminal_fields={"items": []},
                    )
                ]

            self.assertEqual(
                [
                    {"type": "progress", "line": "TITLE::one"},
                    {
                        "type": "result",
                        "ok": False,
                        "items": [],
                        "error": "legacy agent stream ended without terminal event",
                    },
                ],
                events,
            )

        asyncio.run(scenario())

    def test_in_process_progress_redacts_header_and_json_secrets(self) -> None:
        from agent import __main__ as agent_main
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=2.0)

            def fake_main() -> None:
                print("Authorization: Bearer header-secret", file=sys.stderr, flush=True)
                print('{"apiKey":"json-secret","password":"pass-secret"}', file=sys.stderr)
                print("[]", flush=True)

            with mock.patch.object(agent_main, "main", side_effect=fake_main):
                events = [
                    event
                    async for event in provider.stream_events(
                        "search",
                        terminal_fields={"candidates": []},
                        stdout_array_field="candidates",
                    )
                ]

            rendered = "\n".join(str(event) for event in events)
            self.assertNotIn("header-secret", rendered)
            self.assertNotIn("json-secret", rendered)
            self.assertNotIn("pass-secret", rendered)
            self.assertIn("[redacted]", rendered)

        asyncio.run(scenario())

    def test_in_process_timeout_never_returns_before_the_worker_finishes(self) -> None:
        from agent import pipeline
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        finished = threading.Event()

        def slow_command(*_args, **_kwargs):
            time.sleep(0.12)
            finished.set()
            return []

        async def scenario() -> None:
            provider = LegacyAgentProvider(in_process=True, timeout_seconds=0.02)
            with mock.patch.object(pipeline, "search", side_effect=slow_command):
                events = [
                    event
                    async for event in provider.stream_events(
                        "search",
                        ("--query", "fixture", "--sources", "arxiv"),
                        terminal_fields={"candidates": []},
                        stdout_array_field="candidates",
                    )
                ]
            self.assertTrue(finished.is_set())
            self.assertEqual("result", events[-1]["type"])
            self.assertTrue(events[-1]["ok"])

        asyncio.run(scenario())

    async def _assert_stream_contract(self) -> None:
        from backend.app.providers.legacy_agent import LegacyAgentProvider

        with tempfile.TemporaryDirectory(
            prefix="study-app-legacy-agent-provider-"
        ) as temporary:
            root = Path(temporary)
            package = root / "agent"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "__main__.py").write_text(_AGENT_MAIN, encoding="utf-8")
            environment = {
                **os.environ,
                "LEGACY_AGENT_TEST_ROOT": str(root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    [str(root), os.environ.get("PYTHONPATH", "")]
                ),
            }

            provider = LegacyAgentProvider(
                executable=sys.executable,
                cwd=root,
                environment=environment,
                timeout_seconds=1.5,
            )
            stream = provider.stream_events(
                "stream",
                terminal_fields={
                    "candidates": [],
                    "markdown": "",
                    "mapping": {},
                    "summary": {"default": True},
                },
            )
            first_task = asyncio.create_task(anext(stream))
            await _wait_for_path(root / "stream.ready")
            try:
                self.assertTrue(
                    first_task.done(),
                    "stderr progress was buffered until the subprocess exited",
                )
                first = await first_task
                self.assertEqual(
                    {"type": "progress", "line": "STAGE::first"},
                    first,
                )
            finally:
                (root / "stream.release").touch()
                if not first_task.done():
                    await asyncio.wait_for(first_task, timeout=1.0)

            remaining = [event async for event in stream]
            self.assertEqual(1, len(remaining))
            terminal = remaining[0]
            self.assertEqual("result", terminal["type"])
            self.assertEqual(["real-candidate"], terminal["candidates"])
            self.assertEqual("# real markdown", terminal["markdown"])
            self.assertEqual({"real": "value"}, terminal["mapping"])
            self.assertEqual({"default": True}, terminal["summary"])

            cancel_stream = provider.stream_events("cancel", stdin="consumer input")
            cancel_progress = await asyncio.wait_for(
                anext(cancel_stream),
                timeout=1.0,
            )
            self.assertEqual("progress", cancel_progress["type"])
            cancel_pid = int((root / "cancel.pid").read_text(encoding="ascii"))
            started = time.monotonic()
            await cancel_stream.aclose()
            await _wait_for_process_exit(cancel_pid)
            self.assertLess(time.monotonic() - started, 0.75)

            timeout_provider = LegacyAgentProvider(
                executable=sys.executable,
                cwd=root,
                environment=environment,
                timeout_seconds=0.75,
            )
            timeout_events = [
                event async for event in timeout_provider.stream_events("timeout")
            ]
            timeout_pid = int((root / "timeout.pid").read_text(encoding="ascii"))
            await _wait_for_process_exit(timeout_pid)
            self.assertEqual("progress", timeout_events[0]["type"])
            self.assertEqual("result", timeout_events[-1]["type"])
            self.assertFalse(timeout_events[-1]["ok"])
            self.assertEqual("legacy agent timed out", timeout_events[-1]["error"])

            failed_events = [
                event async for event in provider.stream_events(
                    "fail",
                    terminal_fields={"markdown": ""},
                    stdout_text_field="markdown",
                )
            ]
            self.assertEqual("result", failed_events[-1]["type"])
            self.assertFalse(failed_events[-1]["ok"])
            self.assertEqual("OCR transport failed", failed_events[-1]["error"])
            self.assertEqual("stale", failed_events[-1]["markdown"])


async def _wait_for_path(path: Path, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subprocess did not create {path.name}")


async def _wait_for_process_exit(pid: int, *, timeout: float = 2.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_is_running(pid):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"subprocess {pid} remained alive")


def _process_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
