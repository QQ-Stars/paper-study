from __future__ import annotations

"""Controlled subprocess boundary for legacy agent commands.

Routes never call ``Popen``/``spawn`` directly.  The adapter is deliberately
small so tests can inject a fake provider without network or model access.
"""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib
import io
import json
import os
from pathlib import Path
import sys
import threading
from typing import Awaitable, Mapping, Sequence, TypeVar


@dataclass(frozen=True, slots=True)
class LegacyAgentResult:
    returncode: int
    stdout: str
    stderr: str


class LegacyAgentProvider:
    # 这些命令的 stdout 是逐行结构化 JSON 事件流（progress… + 终止 result），
    # 需逐行解析透传，不能走“stderr=进度行、stdout=单个终止 JSON”的默认通道。
    _STRUCTURED_STDOUT_COMMANDS = frozenset({"title-translations"})

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        cwd: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 900.0,
        in_process: bool = False,
    ) -> None:
        self.executable = str(executable or sys.executable)
        self.cwd = str(cwd or Path(__file__).resolve().parents[3])
        self.environment = dict(environment or os.environ)
        self.timeout_seconds = timeout_seconds
        # The local Windows runtime must not spawn a second Python process for
        # every button click.  Tests and external callers keep the historical
        # subprocess mode unless the runtime opts in explicitly.
        self.in_process = in_process

    async def run(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        stdin: str | bytes | None = None,
    ) -> LegacyAgentResult:
        if self.in_process:
            return await self._run_in_process(command, args, stdin=stdin)
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
        stdout_text_field: str | None = None,
        stdout_object_field: str | None = None,
    ):
        if self.in_process:
            async for event in self._stream_in_process_events(
                command,
                args,
                terminal_type=terminal_type,
                terminal_fields=terminal_fields,
                stdin=stdin,
                stdout_array_field=stdout_array_field,
                stdout_text_field=stdout_text_field,
                stdout_object_field=stdout_object_field,
            ):
                yield event
            return
        if command in self._STRUCTURED_STDOUT_COMMANDS:
            async for event in self._stream_stdout_json_events(
                command,
                args,
                terminal_type=terminal_type,
                terminal_fields=terminal_fields,
            ):
                yield event
            return
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
        stderr_lines: list[str] = []
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
                    stderr_lines.append(line)
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
                if stdout_object_field is not None:
                    # 整个 stdout JSON 对象包进指定终态字段（如 explain-batch
                    # 的 summary，对齐旧 Node 行为）。
                    payload[stdout_object_field] = decoded
                else:
                    payload.update(decoded)
            elif isinstance(decoded, list) and stdout_array_field is not None:
                # Some agent commands (e.g. `search`) emit a bare JSON array;
                # wrap it into the expected terminal field for the frontend.
                payload[stdout_array_field] = decoded
            elif stdout_text_field is not None and stdout_text.strip():
                # 非 JSON stdout：整段文本包进指定字段（如 explain/translate
                # 的 markdown，对齐旧 Node 行为）。
                payload[stdout_text_field] = stdout_text
        except (TypeError, ValueError):
            # stdout 不是 JSON（如 explain/translate 直接输出 markdown）：
            # 整段包进指定终态字段，对齐旧 Node 行为。
            if stdout_text_field is not None and stdout_text.strip():
                payload[stdout_text_field] = stdout_text
        for key, value in dict(terminal_fields or {}).items():
            payload.setdefault(key, value)
        if returncode != 0:
            # A command may have emitted a stale/optimistic ``ok: true`` before
            # failing. The process exit status is authoritative.
            payload["ok"] = False
            payload["error"] = _friendly_error(
                LegacyAgentResult(returncode, stdout_text, "\n".join(stderr_lines)),
                command=command,
                timed_out=timed_out,
            )
        else:
            payload.setdefault("ok", True)
            _mark_partial_failure(payload)
            payload.setdefault("error", "")
        yield {"type": terminal_type, **payload}

    async def _stream_stdout_json_events(
        self,
        command: str,
        args: Sequence[str],
        *,
        terminal_type: str,
        terminal_fields: Mapping[str, object] | None,
    ):
        """逐行读 stdout 的结构化 JSON 事件并透传（title-translations 等命令）。
        子进程未发出终止事件（崩溃/超时/被 kill）时补发失败终态。"""
        process = await self._start(command, args, stdin=None)
        assert process.stdout is not None
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        fields = dict(terminal_fields or {})
        drain_task = (
            asyncio.create_task(process.stderr.read())
            if process.stderr is not None
            else None
        )
        background = tuple(task for task in (drain_task,) if task is not None)
        terminal_event: dict[str, object] | None = None
        timed_out = False
        try:
            while True:
                line_bytes = await _wait_before_deadline(
                    process.stdout.readline(), deadline
                )
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    if event.get("type") == terminal_type:
                        terminal_event = event
                    else:
                        yield event
            await _wait_before_deadline(process.wait(), deadline)
            if drain_task is not None:
                await drain_task
        except asyncio.TimeoutError:
            timed_out = True
            await _finish_process(process, background, kill=True)
        except BaseException:
            await _finish_process(process, background, kill=True)
            raise
        else:
            await _finish_process(process, background, kill=False)
        if terminal_event is not None:
            if timed_out or process.returncode not in (0, None):
                terminal_event = dict(terminal_event)
                terminal_event["ok"] = False
                terminal_event["error"] = (
                    "legacy agent timed out"
                    if timed_out
                    else "legacy agent failed"
                )
            yield terminal_event
        else:
            returncode = 124 if timed_out else int(process.returncode or 0)
            yield {
                "type": terminal_type,
                "ok": False,
                **fields,
                "error": "legacy agent timed out"
                if timed_out
                else ("" if returncode == 0 else "legacy agent failed"),
            }

    async def _run_in_process(
        self,
        command: str,
        args: Sequence[str],
        *,
        stdin: str | bytes | None,
    ) -> LegacyAgentResult:
        task = asyncio.create_task(
            asyncio.to_thread(_invoke_agent_main, command, args, stdin)
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.timeout_seconds)
        except asyncio.TimeoutError:
            return LegacyAgentResult(124, "", "legacy agent timed out")

    async def _stream_in_process_events(
        self,
        command: str,
        args: Sequence[str],
        *,
        terminal_type: str,
        terminal_fields: Mapping[str, object] | None,
        stdin: str | bytes | None,
        stdout_array_field: str | None,
        stdout_text_field: str | None,
        stdout_object_field: str | None,
    ):
        result = await self._run_in_process(command, args, stdin=stdin)
        for line in result.stderr.splitlines():
            if line.strip():
                yield {"type": "progress", "line": line}

        fields = dict(terminal_fields or {})
        if command in self._STRUCTURED_STDOUT_COMMANDS:
            terminal_event: dict[str, object] | None = None
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == terminal_type:
                    terminal_event = event
                else:
                    yield event
            if terminal_event is not None:
                if result.returncode != 0:
                    terminal_event = dict(terminal_event)
                    terminal_event["ok"] = False
                    terminal_event["error"] = _friendly_error(
                        result, command=command
                    )
                yield terminal_event
                return
            fallback = {"type": terminal_type, **fields}
            fallback["ok"] = result.returncode == 0
            fallback["error"] = "" if result.returncode == 0 else _friendly_error(
                result, command=command
            )
            yield fallback
            return

        payload: dict[str, object] = {}
        raw_stdout = result.stdout or ""
        try:
            parsed = json.loads(raw_stdout) if raw_stdout.strip() else {}
            if isinstance(parsed, dict):
                if stdout_object_field is not None:
                    payload[stdout_object_field] = parsed
                else:
                    payload.update(parsed)
            elif isinstance(parsed, list) and stdout_array_field is not None:
                payload[stdout_array_field] = parsed
            elif stdout_text_field is not None and raw_stdout.strip():
                payload[stdout_text_field] = raw_stdout
        except (TypeError, ValueError):
            if stdout_text_field is not None and raw_stdout.strip():
                payload[stdout_text_field] = raw_stdout
        for key, value in fields.items():
            payload.setdefault(key, value)
        if result.returncode != 0:
            payload["ok"] = False
            payload["error"] = _friendly_error(result, command=command)
        else:
            payload.setdefault("ok", True)
            _mark_partial_failure(payload)
            payload.setdefault("error", "")
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


_IN_PROCESS_LOCK = threading.Lock()


def _invoke_agent_main(
    command: str,
    args: Sequence[str],
    stdin: str | bytes | None,
) -> LegacyAgentResult:
    """Run the legacy command dispatcher without creating a child process.

    The old command modules still provide the compatibility behavior, but
    their process boundary is the source of ``WinError 10013`` on Windows.
    Serializing this small adapter also keeps redirected stdout/stderr isolated
    while a command uses its own worker threads.
    """
    output = io.StringIO()
    errors = io.StringIO()
    input_text = (
        stdin.decode("utf-8", errors="replace")
        if isinstance(stdin, bytes)
        else (stdin or "")
    )
    previous_argv = sys.argv
    previous_stdin = sys.stdin
    returncode = 0
    with _IN_PROCESS_LOCK, redirect_stdout(output), redirect_stderr(errors):
        try:
            # Settings are written by the running FastAPI process. Refresh the
            # legacy module's config before each command so toggling OCR/local
            # extraction in the settings page takes effect without a restart.
            from agent import config as agent_config

            importlib.reload(agent_config)
            from agent.__main__ import main

            sys.argv = ["agent", command, *(str(item) for item in args)]
            sys.stdin = io.StringIO(input_text)
            main()
        except SystemExit as error:
            value = error.code
            returncode = value if isinstance(value, int) else 1
        except BaseException as error:
            returncode = 1
            print(f"ERROR::{type(error).__name__}: {error}", file=sys.stderr)
        finally:
            sys.argv = previous_argv
            sys.stdin = previous_stdin
    return LegacyAgentResult(returncode, output.getvalue(), errors.getvalue())


def _friendly_error(
    result: LegacyAgentResult,
    *,
    command: str | None = None,
    timed_out: bool = False,
) -> str:
    if result.returncode == 124:
        return "legacy agent timed out"
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("OCRERR::"):
            return line.split("::", 1)[1].strip()
    for line in lines:
        if "WinError 10013" in line or "Connection error" in line:
            if command in {"ocr-md", "ocr-md-batch"}:
                return "OCR/模型连接失败，请检查 OCR 地址、密钥或网络权限"
            if command in {
                "search",
                "ingest",
                "run-job",
                "verify-venue",
                "recommend",
                "citegraph",
            }:
                return "检索数据源连接失败，请检查网络权限、数据源配置或 API 密钥"
            return "模型连接失败，请检查 API 地址、密钥或网络权限"
    for line in reversed(lines):
        if line.startswith("ERR::"):
            return line.split("::", 1)[1].strip()
        if line.startswith("ERROR::"):
            return line.split("::", 1)[1].strip()
    return "legacy agent timed out" if timed_out else "legacy agent failed"


def _mark_partial_failure(payload: dict[str, object]) -> None:
    """Make batch failures visible to the NDJSON console instead of hiding them."""
    if payload.get("ok") is not True:
        return
    failed = payload.get("failed")
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        failed = summary.get("failed")
    has_failures = (
        isinstance(failed, (list, tuple, set)) and bool(failed)
    ) or (isinstance(failed, int) and failed > 0)
    if has_failures:
        payload["ok"] = False
        payload.setdefault("error", "部分任务失败，请查看进度详情")


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
