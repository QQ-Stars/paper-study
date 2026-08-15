from __future__ import annotations

"""Safe React/legacy asset adapter matching the Node routing contract."""

from pathlib import Path
import json
import os
import re
from typing import Awaitable, Callable
from urllib.parse import unquote

from fastapi import APIRouter, Request
from starlette.responses import FileResponse, RedirectResponse, Response


_CSP = (
    "default-src 'self'; script-src 'self'; worker-src 'self'; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'"
)
_MIME = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".cjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_STATIC_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE")


def create_frontend_assets_router(
    *,
    react_root: Path | str | None = None,
    legacy_root: Path | str | None = None,
    root: Path | str | None = None,
    ui_entry: str | None = None,
) -> APIRouter:
    handler = create_frontend_assets_handler(
        react_root=react_root,
        legacy_root=legacy_root,
        root=root,
        ui_entry=ui_entry,
    )
    router = APIRouter()

    @router.api_route("/{path:path}", methods=list(_STATIC_METHODS))
    async def frontend_asset(request: Request, path: str) -> Response:
        return await handler(request, path)

    return router


def create_frontend_assets_handler(
    *,
    react_root: Path | str | None = None,
    legacy_root: Path | str | None = None,
    root: Path | str | None = None,
    ui_entry: str | None = None,
) -> Callable[[Request, str], Awaitable[Response]]:
    repository_root = Path(root or Path(__file__).resolve().parents[4]).resolve()
    react = Path(react_root or repository_root / "frontend" / "dist").resolve()
    legacy = Path(legacy_root or repository_root / "public").resolve()
    react_index = react / "index.html"
    legacy_index = legacy / "index.html"
    configured_entry = os.environ.get("UI_ENTRY") if ui_entry is None else ui_entry
    requested = configured_entry if configured_entry in {"react", "legacy"} else "react"
    react_available = react_index.is_file()
    root_entry = requested if requested == "legacy" or react_available else "legacy"
    immutable_assets = _read_immutable_assets(react) if react_available else frozenset()

    async def frontend_asset(request: Request, path: str) -> Response:
        raw = _raw_path(request, path)
        if request.method not in {"GET", "HEAD"}:
            return _plain("method not allowed", 405, {"Allow": "GET, HEAD"})
        result = _resolve(raw, react, react_index, legacy, legacy_index, root_entry, react_available)
        if result[0] == "redirect":
            return RedirectResponse(result[1], status_code=302, headers={"Cache-Control": "no-store"})
        kind, target = result
        if kind == "forbidden":
            return _plain("forbidden", 403)
        if kind == "unavailable":
            return _plain(
                "React workspace unavailable; build frontend and restart the server",
                503,
                {"Retry-After": "0"},
            )
        if kind == "not-found" or target is None:
            return _plain("not found", 404)
        flavor = "react" if target.is_relative_to(react) else "legacy"
        html = target.suffix.lower() == ".html"
        relative = target.relative_to(react).as_posix() if flavor == "react" else ""
        headers = {
            "Cache-Control": (
                "public,max-age=31536000,immutable"
                if flavor == "react" and not html and relative in immutable_assets
                else "no-cache"
            ),
            "Content-Type": _MIME.get(
                target.suffix.lower(), "application/octet-stream"
            ),
        }
        if flavor == "react" and html:
            headers["Content-Security-Policy"] = _CSP
        return FileResponse(
            target,
            headers=headers,
        )

    return frontend_asset


def _resolve(
    raw: str,
    react: Path,
    react_index: Path,
    legacy: Path,
    legacy_index: Path,
    root_entry: str,
    react_available: bool,
) -> tuple[str, Path | str | None]:
    if not raw.startswith("/") or "\x00" in raw or "\\" in raw:
        return "forbidden", None
    if "%2f" in raw.lower() or "%5c" in raw.lower():
        return "forbidden", None
    index = 0
    while True:
        index = raw.find("%", index)
        if index < 0:
            break
        if _PERCENT_ESCAPE.match(raw, index) is None:
            return "forbidden", None
        index += 3
    try:
        decoded = unquote(raw, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return "forbidden", None
    if "\x00" in decoded or "\\" in decoded:
        return "forbidden", None
    if any(segment in {".", ".."} for segment in raw.split("/") + decoded.split("/")):
        return "forbidden", None
    if decoded == "/":
        return ("redirect", "/workspace/") if root_entry == "react" else _file_or_missing(legacy_index, legacy)
    if decoded == "/workspace":
        return "redirect", "/workspace/"
    if decoded == "/legacy":
        return "redirect", "/legacy/"
    if decoded == "/api" or decoded.startswith("/api/") or decoded == "/pdfbytes" or decoded.startswith("/papers"):
        return "not-found", None
    if decoded.startswith("/workspace") and decoded != "/workspace" and not decoded.startswith("/workspace/"):
        return "forbidden", None
    if decoded.startswith("/legacy") and decoded != "/legacy" and not decoded.startswith("/legacy/"):
        return "forbidden", None
    if decoded.startswith("/workspace/"):
        if not react_available:
            return "unavailable", None
        relative = decoded[len("/workspace/") :]
        if relative == "":
            return _file_or_missing(react_index, react)
        target = _safe_target(react, relative)
        if target is None:
            return "forbidden", None
        if target.is_file():
            return "react-file", target
        if relative == "assets" or relative.startswith("assets/"):
            return "not-found", None
        return _file_or_missing(react_index, react)
    if decoded.startswith("/legacy/"):
        relative = decoded[len("/legacy/") :]
        if relative == "":
            return _file_or_missing(legacy_index, legacy)
        target = _safe_target(legacy, relative)
        if target is None:
            return "forbidden", None
        return _file_or_missing(target, legacy)
    target = _safe_target(legacy, decoded[1:])
    if target is None:
        return "forbidden", None
    return _file_or_missing(target, legacy)


def _file_or_missing(target: Path, root: Path) -> tuple[str, Path | None]:
    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        return "forbidden", None
    return ("legacy-file", resolved) if resolved.is_file() else ("not-found", None)


def _safe_target(root: Path, relative: str) -> Path | None:
    if not relative or Path(relative).is_absolute() or ":" in relative.split("/")[0]:
        return None
    target = (root / relative).resolve(strict=False)
    try:
        target.relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        return None
    return target


def _raw_path(request: Request, fallback: str) -> str:
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        try:
            rendered = raw.decode("ascii")
        except UnicodeDecodeError:
            rendered = fallback
        return rendered.split("?", 1)[0].split("#", 1)[0]
    return "/" + fallback


def _read_immutable_assets(react: Path) -> frozenset[str]:
    manifest = _safe_target(react, ".vite/manifest.json")
    if manifest is None or not manifest.is_file():
        return frozenset()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    assets: set[str] = set()
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        candidates = [record.get("file")]
        for field in ("css", "assets"):
            values = record.get(field)
            if isinstance(values, list):
                candidates.extend(values)
        for value in candidates:
            if not isinstance(value, str):
                continue
            portable = value.replace("\\", "/")
            target = _safe_target(react, portable)
            if target is not None and target.is_file():
                assets.add(portable)
    return frozenset(assets)


def _plain(content: str, status: int, extra: dict[str, str] | None = None) -> Response:
    headers = {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}
    headers.update(extra or {})
    return Response(content, status_code=status, headers=headers)


__all__ = ["create_frontend_assets_handler", "create_frontend_assets_router"]
