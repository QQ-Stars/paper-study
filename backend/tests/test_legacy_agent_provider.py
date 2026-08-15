from __future__ import annotations

import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


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
'''


class LegacyAgentProviderTests(unittest.TestCase):
    def test_stream_events_is_incremental_preserves_payload_and_reaps_interruptions(
        self,
    ) -> None:
        asyncio.run(self._assert_stream_contract())

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
