from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class _Authority:
    host: str
    port: int


class LocalAccessPolicy:
    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        *,
        additional_hosts: tuple[str, ...] = (),
        loopback_port_forwarding: bool = False,
        loopback_forwarder_hosts: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(bind_port, int) or isinstance(bind_port, bool):
            raise ValueError("bind_port must be an integer")
        if not 1 <= bind_port <= 65535:
            raise ValueError("bind_port is out of range")
        normalized_bind = _normalize_host_name(bind_host)
        self._bind_port = bind_port
        self._loopback_port_forwarding = loopback_port_forwarding
        if loopback_port_forwarding:
            try:
                is_unspecified = ipaddress.ip_address(normalized_bind).is_unspecified
            except ValueError:
                is_unspecified = False
            if not is_unspecified or additional_hosts:
                raise ValueError(
                    "loopback port forwarding requires only a wildcard listener"
                )
            self._allowed_hosts: set[str] = set()
            self._allowed_client_hosts = {"127.0.0.1", "::1"}
            for host in loopback_forwarder_hosts:
                address = ipaddress.ip_address(host)
                if address.is_unspecified or address.is_multicast:
                    raise ValueError("loopback forwarder source is invalid")
                self._allowed_client_hosts.add(str(address))
        else:
            if loopback_forwarder_hosts:
                raise ValueError("forwarder sources require loopback port forwarding")
            self._allowed_client_hosts = set()
            self._allowed_hosts = {normalized_bind}
            if _is_loopback(normalized_bind):
                self._allowed_hosts.add("localhost")
            self._allowed_hosts.update(
                _normalize_host_name(host) for host in additional_hosts
            )

    def validate_client(self, client: object) -> None:
        if not self._loopback_port_forwarding:
            return
        if not isinstance(client, (tuple, list)) or len(client) != 2:
            raise ValueError("connection source is unavailable")
        try:
            host = str(ipaddress.ip_address(str(client[0])))
        except ValueError:
            raise ValueError("connection source is invalid") from None
        if host not in self._allowed_client_hosts:
            raise ValueError("connection source is not the loopback forwarder")

    def request_authority(self, raw_host: str, *, scheme: str) -> _Authority:
        authority = _parse_authority(raw_host, scheme=scheme)
        if self._loopback_port_forwarding:
            if not _is_loopback(authority.host):
                raise ValueError("Host is outside the loopback port forward")
            return authority
        if (
            authority.host not in self._allowed_hosts
            or authority.port != self._bind_port
        ):
            raise ValueError("Host is outside the configured listener")
        return authority

    def origin_matches(
        self,
        raw_origin: str,
        *,
        request_scheme: str,
        request_authority: _Authority,
    ) -> bool:
        if not _is_clean_header_value(raw_origin):
            return False
        try:
            parsed = urlsplit(raw_origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.scheme != request_scheme
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                return False
            authority = _parse_authority(parsed.netloc, scheme=parsed.scheme)
        except (ValueError, UnicodeError):
            return False
        return authority == request_authority


class LocalAccessMiddleware:
    def __init__(self, app: Any, *, policy: LocalAccessPolicy) -> None:
        self._app = app
        self._policy = policy

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        headers = _headers(scope)
        hosts = headers.get("host", ())
        try:
            self._policy.validate_client(scope.get("client"))
            if len(hosts) != 1:
                raise ValueError("exactly one Host header is required")
            authority = self._policy.request_authority(
                hosts[0],
                scheme=str(scope.get("scheme", "http")).lower(),
            )
        except (ValueError, UnicodeError):
            await _deny(
                scope,
                receive,
                send,
                status_code=400,
                code="LOCAL_ACCESS_DENIED",
                message="The request Host is not allowed.",
            )
            return

        if str(scope.get("method", "GET")).upper() in _STATE_CHANGING_METHODS:
            origins = headers.get("origin", ())
            if len(origins) > 1 or (
                origins
                and not self._policy.origin_matches(
                    origins[0],
                    request_scheme=str(scope.get("scheme", "http")).lower(),
                    request_authority=authority,
                )
            ):
                await _deny(
                    scope,
                    receive,
                    send,
                    status_code=403,
                    code="LOCAL_ORIGIN_DENIED",
                    message="The request Origin is not allowed.",
                )
                return

        await self._app(scope, receive, send)


def _headers(scope: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    collected: dict[str, list[str]] = {}
    for raw_name, raw_value in scope.get("headers", ()):
        name = bytes(raw_name).decode("ascii", "strict").lower()
        value = bytes(raw_value).decode("latin-1", "strict")
        collected.setdefault(name, []).append(value)
    return {name: tuple(values) for name, values in collected.items()}


def _parse_authority(raw_value: str, *, scheme: str) -> _Authority:
    if not _is_clean_header_value(raw_value):
        raise ValueError("authority contains unsafe characters")
    if any(character in raw_value for character in "/\\?#,@"):
        raise ValueError("authority is ambiguous")

    host: str
    port_text: str | None
    if raw_value.startswith("["):
        close = raw_value.find("]")
        if close <= 1 or raw_value.find("]", close + 1) != -1:
            raise ValueError("IPv6 authority is malformed")
        host = _normalize_host_name(raw_value[1:close])
        suffix = raw_value[close + 1 :]
        if suffix and not suffix.startswith(":"):
            raise ValueError("IPv6 authority suffix is malformed")
        port_text = suffix[1:] if suffix else None
        if not isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
            raise ValueError("brackets require IPv6")
    else:
        if raw_value.count(":") > 1:
            raise ValueError("IPv6 authority must be bracketed")
        if ":" in raw_value:
            raw_host, port_text = raw_value.rsplit(":", 1)
        else:
            raw_host, port_text = raw_value, None
        host = _normalize_host_name(raw_host)

    if port_text is None:
        port = 443 if scheme == "https" else 80
    else:
        if not port_text.isascii() or not port_text.isdecimal():
            raise ValueError("authority port is invalid")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError("authority port is out of range")
    return _Authority(host=host, port=port)


def _normalize_host_name(value: str) -> str:
    if not value or value != value.strip() or not value.isascii():
        raise ValueError("host name is invalid")
    lowered = value.lower()
    if lowered.endswith(".") or "@" in lowered:
        raise ValueError("host name is invalid")
    try:
        return str(ipaddress.ip_address(lowered))
    except ValueError:
        labels = lowered.split(".")
        if any(
            not label
            or len(label) > 63
            or not label.replace("-", "").isalnum()
            or label.startswith("-")
            or label.endswith("-")
            for label in labels
        ):
            raise ValueError("host name is invalid") from None
        return lowered


def _is_clean_header_value(value: str) -> bool:
    return bool(value) and value == value.strip() and all(
        32 < ord(character) < 127 for character in value
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _deny(
    scope: dict[str, Any],
    receive: Any,
    send: Any,
    *,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": {}}},
    )
    await response(scope, receive, send)
