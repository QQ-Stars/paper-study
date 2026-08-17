from __future__ import annotations

"""Controlled subprocess boundary for legacy agent commands.

Routes never call ``Popen``/``spawn`` directly.  The adapter is deliberately
small so tests can inject a fake provider without network or model access.
"""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Awaitable, Mapping, Sequence, TypeVar


@dataclass(frozen=True, slots=True)
class LegacyAgentResult:
    returncode: int
    stdout: str
    stderr: str


class LegacyAgentProvider:
    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        cwd: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 900.0,
    ) -> None:
        self.executable = str(executable or sys.executable)
        self.cwd = str(cwd or Path(__file__).resolve().parents[3])
        self.environment = dict(environment or os.environ)
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        stdin: str | bytes | None = None,
    ) -> LegacyAgentResult:
        process = await self._start(command, args, stdin=stdin)
        input_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        communication = asyncio.create_task(process.communicate(input_bytes))
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communication), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            await _finish_process(process, (communication,), kill=True)
            return LegacyAgentResult(124, "", "legacy agent timed out")
        except BaseException:
            await _finish_process(process, (communication,), kill=True)
            raise
        return LegacyAgentResult(
            int(process.returncode or 0),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _start(
        self,
        command: str,
        args: Sequence[str],
        *,
        stdin: str | bytes | None,
    ) -> asyncio.subprocess.Process:
        argv = [
            self.executable,
            "-m",
            "agent",
            command,
            *(str(item) for item in args),
        ]
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.cwd,
            env=self.environment,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def run_job(self, job_id: int) -> LegacyAgentResult:
        return await self.run("run-job", ("--id", str(job_id)))

    async def stream_events(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        terminal_type: str = "result",
        terminal_fields: Mapping[str, object] | None = None,
        stdin: str | bytes | None = None,
        stdout_array_field: str | None = None,
    ):
        process = await self._start(command, args, stdin=stdin)
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(process.stdout.read())
        progress: asyncio.Queue[bytes | None] = asyncio.Queue()
        stderr_task = asyncio.create_task(_pump_stderr(process.stderr, progress))
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        input_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        timed_out = False
        stdout = b""
        try:
            if process.stdin is not None:
                if input_bytes:
                    process.stdin.write(input_bytes)
                    await _wait_before_deadline(process.stdin.drain(), deadline)
                process.stdin.close()
                await _wait_before_deadline(process.stdin.wait_closed(), deadline)

            while True:
                line_bytes = await _wait_before_deadline(progress.get(), deadline)
                if line_bytes is None:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.strip():
                    yield {"type": "progress", "line": line}

            await _wait_before_deadline(process.wait(), deadline)
            stdout = await _wait_before_deadline(
                asyncio.shield(stdout_task),
                deadline,
            )
            await stderr_task
        except asyncio.TimeoutError:
            timed_out = True
            await _finish_process(
                process,
                (stdout_task, stderr_task),
                kill=True,
            )
        except BaseException:
            await _finish_process(
                process,
                (stdout_task, stderr_task),
                kill=True,
            )
            raise
        else:
            await _finish_process(
                process,
                (stdout_task, stderr_task),
                kill=False,
            )

        returncode = 124 if timed_out else int(process.returncode or 0)
        payload: dict[str, object] = {}
        try:
            stdout_text = stdout.decode("utf-8", errors="replace")
            decoded = json.loads(stdout_text) if stdout_text.strip() else {}
            if isinstance(decoded, dict):
                payload.update(decoded)
            elif isinstance(decoded, list) and stdout_array_field is not None:
                # Some agent commands (e.g. `search`) emit a bare JSON array;
                # wrap it into the expected terminal field for the frontend.
                payload[stdout_array_field] = decoded
        except (TypeError, ValueError):
            pass
        for key, value in dict(terminal_fields or {}).items():
            payload.setdefault(key, value)
        payload.setdefault("ok", returncode == 0)
        payload.setdefault(
            "error",
            "legacy agent timed out"
            if timed_out
            else ("" if returncode == 0 else "legacy agent failed"),
        )
        yield {"type": terminal_type, **payload}

    async def confirm_candidates(
        self,
        job_id: int,
        candidates: Sequence[Mapping[str, object]],
        *,
        deep: bool = False,
        download_pdf: bool = True,
    ) -> dict[str, object]:
        args: list[str] = []
        if deep:
            args.append("--deep")
        if not download_pdf:
            args.append("--no-pdf")
        result = await self.run(
            "ingest-selected", args, stdin=__import__("json").dumps(list(candidates), ensure_ascii=False)
        )
        added = 0
        for line in result.stderr.splitlines():
            if line.startswith("INGESTED::"):
                try:
                    added = int(line.split("::", 1)[1])
                except ValueError:
                    pass
        return {
            "ok": result.returncode == 0,
            "added": added,
            "error": "" if result.returncode == 0 else "legacy agent failed",
            "jobId": job_id,
        }


async def _pump_stderr(
    stream: asyncio.StreamReader,
    progress: asyncio.Queue[bytes | None],
) -> None:
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            await progress.put(line)
    finally:
        await progress.put(None)


_T = TypeVar("_T")


async def _wait_before_deadline(
    awaitable: Awaitable[_T],
    deadline: float,
) -> _T:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.TimeoutError
    return await asyncio.wait_for(awaitable, timeout=remaining)


async def _finish_process(
    process: asyncio.subprocess.Process,
    tasks: Sequence[asyncio.Task[object]],
    *,
    kill: bool,
) -> None:
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
    if kill and process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    if process.returncode is None:
        await process.wait()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if process.stdin is not None:
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


__all__ = ["LegacyAgentProvider", "LegacyAgentResult"]
