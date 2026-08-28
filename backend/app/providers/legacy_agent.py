from __future__ import annotations

"""Controlled subprocess boundary for legacy agent commands.

Routes never call ``Popen``/``spawn`` directly.  The adapter is deliberately
small so tests can inject a fake provider without network or model access.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import io
import json
import os
from pathlib import Path
import sys
import threading
from typing import Awaitable, Callable, Mapping, Sequence, TypeVar

from backend.app.application.safe_text import redact_sensitive_text


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
        environment_provider: Callable[[], Awaitable[Mapping[str, str]]] | None = None,
    ) -> None:
        self.executable = str(executable or sys.executable)
        self.cwd = str(cwd or Path(__file__).resolve().parents[3])
        self.environment = dict(environment or os.environ)
        self.timeout_seconds = timeout_seconds
        # The local Windows runtime must not spawn a second Python process for
        # every button click.  Tests and external callers keep the historical
        # subprocess mode unless the runtime opts in explicitly.
        self.in_process = in_process
        self._environment_provider = environment_provider

    async def _effective_environment(self) -> dict[str, str]:
        if self._environment_provider is None:
            return dict(self.environment)
        return dict(await self._environment_provider())

    async def run(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        stdin: str | bytes | None = None,
    ) -> LegacyAgentResult:
        if self.in_process:
            return await self._run_in_process(
                command,
                args,
                stdin=stdin,
                environment=await self._effective_environment(),
            )
        process = await self._start(
            command,
            args,
            stdin=stdin,
            environment=await self._effective_environment(),
        )
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
        environment: Mapping[str, str] | None = None,
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
            env=dict(environment or self.environment),
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
        environment = await self._effective_environment()
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
                environment=environment,
            ):
                yield event
            return
        if command in self._STRUCTURED_STDOUT_COMMANDS:
            async for event in self._stream_stdout_json_events(
                command,
                args,
                terminal_type=terminal_type,
                terminal_fields=terminal_fields,
                environment=environment,
            ):
                yield event
            return
        process = await self._start(
            command,
            args,
            stdin=stdin,
            environment=environment,
        )
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
                    yield {"type": "progress", "line": _redact_error(line)}

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
        malformed_output = False
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
            elif stdout_text.strip():
                malformed_output = True
        except (TypeError, ValueError):
            # stdout 不是 JSON（如 explain/translate 直接输出 markdown）：
            # 整段包进指定终态字段，对齐旧 Node 行为。
            if stdout_text_field is not None and stdout_text.strip():
                payload[stdout_text_field] = stdout_text
            elif stdout_text.strip():
                malformed_output = True
        for key, value in dict(terminal_fields or {}).items():
            payload.setdefault(key, value)
        if malformed_output:
            payload["ok"] = False
            payload["error"] = "LEGACY_AGENT_MALFORMED_JSON"
        elif returncode != 0:
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
            _mark_partial_failure(payload, command=command)
            payload.setdefault("error", "")
        yield _redact_event({"type": terminal_type, **payload})

    async def _stream_stdout_json_events(
        self,
        command: str,
        args: Sequence[str],
        *,
        terminal_type: str,
        terminal_fields: Mapping[str, object] | None,
        environment: Mapping[str, str],
    ):
        """逐行读 stdout 的结构化 JSON 事件并透传（title-translations 等命令）。
        子进程未发出终止事件（崩溃/超时/被 kill）时补发失败终态。"""
        process = await self._start(
            command,
            args,
            stdin=None,
            environment=environment,
        )
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
        malformed_stdout = False
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
                    malformed_stdout = True
                    continue
                if not isinstance(event, dict):
                    malformed_stdout = True
                    continue
                if malformed_stdout:
                    continue
                if event.get("type") == terminal_type:
                    terminal_event = _redact_event(event)
                else:
                    yield _redact_event(event)
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
        if malformed_stdout:
            yield {
                "type": terminal_type,
                "ok": False,
                **fields,
                "error": "LEGACY_AGENT_MALFORMED_NDJSON",
            }
        elif terminal_event is not None:
            if timed_out or process.returncode not in (0, None):
                terminal_event = dict(terminal_event)
                terminal_event["ok"] = False
                terminal_event["error"] = (
                    "legacy agent timed out"
                    if timed_out
                    else "legacy agent failed"
                )
            yield _redact_event(terminal_event)
        else:
            returncode = 124 if timed_out else int(process.returncode or 0)
            yield {
                "type": terminal_type,
                "ok": False,
                **fields,
                "error": "legacy agent timed out"
                if timed_out
                else "legacy agent stream ended without terminal event",
            }

    async def _run_in_process(
        self,
        command: str,
        args: Sequence[str],
        *,
        stdin: str | bytes | None,
        environment: Mapping[str, str],
    ) -> LegacyAgentResult:
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[LegacyAgentResult] = loop.create_future()

        def deliver(result: LegacyAgentResult) -> None:
            if not result_future.done():
                result_future.set_result(result)

        worker = threading.Thread(
            target=_invoke_agent_main,
            args=(command, args, stdin, environment, loop, deliver),
            name="legacy-agent-run",
            daemon=True,
        )
        worker.start()
        try:
            return await asyncio.wait_for(
                asyncio.shield(result_future), self.timeout_seconds
            )
        except asyncio.TimeoutError:
            # A Python thread cannot be terminated safely. Returning a timeout
            # here would let the command keep mutating data after callers were
            # told it failed. Wait for the authoritative result instead; the
            # agent's network/model adapters retain their own bounded timeouts.
            return await asyncio.shield(result_future)

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
        environment: Mapping[str, str],
    ):
        """Run an in-process command while preserving incremental events.

        ``_run_in_process`` intentionally returns a complete result for the
        non-streaming compatibility calls.  Streaming routes need a different
        boundary: agent progress is written to stderr (and title translation
        events to stdout) before the command has finished.  The worker thread
        therefore publishes complete lines to an asyncio queue as they are
        written, while the final result is still normalized below in exactly
        the same way as the subprocess path.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
        worker = threading.Thread(
            target=_invoke_agent_main_stream,
            args=(
                command,
                args,
                stdin,
                loop,
                queue,
                command in self._STRUCTURED_STDOUT_COMMANDS,
                environment,
            ),
            name="legacy-agent-stream",
            daemon=True,
        )
        worker.start()
        terminal_event: dict[str, object] | None = None
        malformed_stdout = False
        result: LegacyAgentResult | None = None
        deadline: float | None = loop.time() + self.timeout_seconds
        while True:
            if deadline is None:
                kind, value = await queue.get()
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    deadline = None
                    continue
                try:
                    kind, value = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError:
                    # See _run_in_process: do not publish a false terminal while
                    # an unkillable worker can still write to the repository.
                    deadline = None
                    continue
            if kind == "progress":
                line = str(value).strip()
                if line:
                    yield {"type": "progress", "line": _redact_error(line)}
            elif kind == "stdout":
                line = str(value)
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except (TypeError, ValueError):
                    malformed_stdout = True
                    continue
                if not isinstance(decoded, dict):
                    malformed_stdout = True
                    continue
                if malformed_stdout:
                    continue
                event = decoded
                if event.get("type") == terminal_type:
                    terminal_event = _redact_event(event)
                else:
                    yield _redact_event(event)
            elif kind == "complete":
                result = value
                break

        if not isinstance(result, LegacyAgentResult):
            yield {
                "type": terminal_type,
                "ok": False,
                **dict(terminal_fields or {}),
                "error": "legacy agent failed",
            }
            return

        fields = dict(terminal_fields or {})
        if command in self._STRUCTURED_STDOUT_COMMANDS:
            if malformed_stdout:
                yield {
                    "type": terminal_type,
                    **fields,
                    "ok": False,
                    "error": "LEGACY_AGENT_MALFORMED_NDJSON",
                }
                return
            if terminal_event is not None:
                if result.returncode != 0:
                    terminal_event = dict(terminal_event)
                    terminal_event["ok"] = False
                    terminal_event["error"] = _friendly_error(
                        result, command=command
                    )
                yield _redact_event(terminal_event)
                return
            yield {
                "type": terminal_type,
                **fields,
                "ok": False,
                "error": (
                    _friendly_error(result, command=command)
                    if result.returncode != 0
                    else "legacy agent stream ended without terminal event"
                ),
            }
            return

        payload: dict[str, object] = {}
        raw_stdout = result.stdout or ""
        malformed_output = False
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
            elif raw_stdout.strip():
                malformed_output = True
        except (TypeError, ValueError):
            if stdout_text_field is not None and raw_stdout.strip():
                payload[stdout_text_field] = raw_stdout
            elif raw_stdout.strip():
                malformed_output = True
        for key, value in fields.items():
            payload.setdefault(key, value)
        if malformed_output:
            payload["ok"] = False
            payload["error"] = "LEGACY_AGENT_MALFORMED_JSON"
        elif result.returncode != 0:
            payload["ok"] = False
            payload["error"] = _friendly_error(result, command=command)
        else:
            payload.setdefault("ok", True)
            _mark_partial_failure(payload, command=command)
            payload.setdefault("error", "")
        yield _redact_event({"type": terminal_type, **payload})

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


class _StreamingCapture:
    """Small text stream used to forward complete lines from a worker thread."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, object]],
        kind: str,
        *,
        emit_lines: bool,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._kind = kind
        self._emit_lines = emit_lines
        self._chunks: list[str] = []
        self._pending = ""
        self._write_lock = threading.Lock()

    def write(self, value: object) -> int:
        text = str(value)
        with self._write_lock:
            self._chunks.append(text)
            if self._emit_lines and text:
                self._pending += text
                while "\n" in self._pending:
                    line, self._pending = self._pending.split("\n", 1)
                    self._publish(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"

    def finish(self) -> None:
        with self._write_lock:
            if self._emit_lines and self._pending:
                self._publish(self._pending.rstrip("\r"))
                self._pending = ""

    def getvalue(self) -> str:
        with self._write_lock:
            return "".join(self._chunks)

    def _publish(self, line: str) -> None:
        try:
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait,
                (self._kind, line),
            )
        except RuntimeError:
            # The request loop may have closed after a cancelled client.  The
            # worker still needs to release the process-wide stream lock.
            return


class _ThreadStreamRouter:
    """Capture only the command thread and preserve unrelated process output."""

    def __init__(
        self,
        capture: object,
        fallback: object,
        preexisting_threads: frozenset[int],
    ) -> None:
        self._capture = capture
        self._fallback = fallback
        self._owner = threading.get_ident()
        self._preexisting_threads = preexisting_threads

    def _target(self) -> object:
        identifier = threading.get_ident()
        command_owned = (
            identifier == self._owner
            or identifier not in self._preexisting_threads
        )
        return self._capture if command_owned else self._fallback

    def write(self, value: object) -> int:
        return int(getattr(self._target(), "write")(value))

    def flush(self) -> None:
        getattr(self._target(), "flush")()

    def __getattr__(self, name: str) -> object:
        return getattr(self._fallback, name)


@contextmanager
def _capture_agent_streams(output: object, errors: object):
    previous_stdout = sys.stdout
    previous_stderr = sys.stderr
    preexisting_threads = frozenset(
        thread.ident for thread in threading.enumerate() if thread.ident is not None
    )
    sys.stdout = _ThreadStreamRouter(  # type: ignore[assignment]
        output, previous_stdout, preexisting_threads
    )
    sys.stderr = _ThreadStreamRouter(  # type: ignore[assignment]
        errors, previous_stderr, preexisting_threads
    )
    try:
        yield
    finally:
        sys.stdout = previous_stdout
        sys.stderr = previous_stderr


def _decode_json_line(line: str) -> dict[str, object] | None:
    if not line.strip():
        return None
    try:
        value = json.loads(line)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _invoke_agent_main_stream(
    command: str,
    args: Sequence[str],
    stdin: str | bytes | None,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue[tuple[str, object]],
    emit_stdout_lines: bool,
    environment: Mapping[str, str],
) -> None:
    output = _StreamingCapture(
        loop,
        queue,
        "stdout",
        emit_lines=emit_stdout_lines,
    )
    errors = _StreamingCapture(loop, queue, "progress", emit_lines=True)
    returncode = 0
    with _IN_PROCESS_LOCK, _capture_agent_streams(output, errors):
        returncode = _run_agent_main(command, args, stdin, environment)
        output.finish()
        errors.finish()
    result = LegacyAgentResult(returncode, output.getvalue(), errors.getvalue())
    try:
        loop.call_soon_threadsafe(queue.put_nowait, ("complete", result))
    except RuntimeError:
        return


def _invoke_agent_main(
    command: str,
    args: Sequence[str],
    stdin: str | bytes | None,
    environment: Mapping[str, str],
    loop: asyncio.AbstractEventLoop,
    deliver: Callable[[LegacyAgentResult], None],
) -> LegacyAgentResult:
    """Run the legacy command dispatcher without creating a child process.

    The old command modules still provide the compatibility behavior, but
    their process boundary is the source of ``WinError 10013`` on Windows.
    Serializing this small adapter also keeps redirected stdout/stderr isolated
    while a command uses its own worker threads.
    """
    output = io.StringIO()
    errors = io.StringIO()
    with _IN_PROCESS_LOCK, _capture_agent_streams(output, errors):
        returncode = _run_agent_main(command, args, stdin, environment)
    result = LegacyAgentResult(returncode, output.getvalue(), errors.getvalue())
    try:
        loop.call_soon_threadsafe(deliver, result)
    except RuntimeError:
        pass
    return result


def _run_agent_main(
    command: str,
    args: Sequence[str],
    stdin: str | bytes | None,
    environment: Mapping[str, str],
) -> int:
    """Invoke the legacy dispatcher under the caller's redirected streams."""
    input_text = (
        stdin.decode("utf-8", errors="replace")
        if isinstance(stdin, bytes)
        else (stdin or "")
    )
    previous_argv = sys.argv
    previous_stdin = sys.stdin
    returncode = 0
    try:
        # Refresh config under the supplied environment snapshot, then restore
        # the process environment before the potentially long-running command.
        # Agent modules read these config globals during execution.
        previous_environment = dict(os.environ)
        os.environ.clear()
        os.environ.update(environment)
        try:
            from agent import config as agent_config

            importlib.reload(agent_config)
        finally:
            os.environ.clear()
            os.environ.update(previous_environment)

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
    return returncode


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
            return _redact_error(line.split("::", 1)[1].strip())
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
            return _redact_error(line.split("::", 1)[1].strip())
        if line.startswith("ERROR::"):
            return _redact_error(line.split("::", 1)[1].strip())
    return "legacy agent timed out" if timed_out else "legacy agent failed"


def _redact_error(value: str) -> str:
    return redact_sensitive_text(value, limit=300)


def _redact_event(value: dict[str, object]) -> dict[str, object]:
    """Recursively redact provider-controlled strings before NDJSON exposure.

    Structured commands can place exception details inside nested summaries or
    failure arrays, bypassing the stderr-only redaction path.  Preserve the
    event shape and all ordinary text while removing credential-shaped values.
    """

    def redact(item: object) -> object:
        if isinstance(item, str):
            return redact_sensitive_text(item)
        if isinstance(item, Mapping):
            return {key: redact(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [redact(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(redact(nested) for nested in item)
        return item

    return {key: redact(item) for key, item in value.items()}


def _mark_partial_failure(payload: dict[str, object], *, command: str) -> None:
    """Make real batch failures visible without relabeling coverage gaps.

    ``citegraph.failed`` counts papers that Semantic Scholar could not resolve.
    The graph is still rebuilt atomically when at least one paper resolves, so
    those unmatched records are an expected partial-coverage result rather than
    a failed command.
    """
    if payload.get("ok") is not True:
        return
    if command == "citegraph":
        return
    failed = payload.get("failed")
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        # Batch commands return their authoritative status inside ``summary``
        # when the stdout object is adapted through ``stdout_object_field``.
        # Do not let the adapter's optimistic top-level ``ok`` mask a disabled
        # provider, an empty run, or another explicit nested failure.
        if summary.get("ok") is False:
            payload["ok"] = False
            nested_error = summary.get("error")
            if isinstance(nested_error, str) and nested_error.strip():
                payload["error"] = _redact_error(nested_error)
            else:
                payload.setdefault("error", "批量任务失败，请查看进度详情")
            return
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
