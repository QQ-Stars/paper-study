from __future__ import annotations

import base64
import inspect
from pathlib import Path
from typing import Any, Callable

from backend.app.application.ports.credential_probe import CredentialProbeResult
from backend.app.domain import Credential, CredentialKind


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


class SafeCredentialProbe:
    def __init__(
        self,
        *,
        llm_transport: Callable[[Credential, str], Any] | None = None,
        verified_ocr_transport: Callable[[Credential, bytes], Any] | None = None,
        enable_verified_ocr: bool = False,
    ) -> None:
        self._llm_transport = llm_transport
        self._verified_ocr_transport = verified_ocr_transport
        self._enable_verified_ocr = enable_verified_ocr

    async def test(
        self,
        kind: CredentialKind,
        credential: Credential | None,
    ) -> CredentialProbeResult:
        normalized = CredentialKind(kind)
        if credential is None:
            return CredentialProbeResult(
                normalized, False, "CREDENTIAL_NOT_CONFIGURED", "Credential is not configured."
            )
        if normalized is CredentialKind.LLM:
            if self._llm_transport is None:
                return CredentialProbeResult(
                    normalized, False, "CREDENTIAL_PROBE_UNAVAILABLE", "LLM probe is unavailable."
                )
            prompt = (FIXTURE_ROOT / "llm-probe.txt").read_text(encoding="utf-8")
            return await self._invoke(normalized, self._llm_transport, credential, prompt)
        if normalized is CredentialKind.OCR:
            if not self._enable_verified_ocr:
                return CredentialProbeResult(
                    normalized,
                    False,
                    "OCR_PROVIDER_CONTRACT_UNVERIFIED",
                    "OCR provider probe contract is not verified.",
                )
            if self._verified_ocr_transport is None:
                return CredentialProbeResult(
                    normalized, False, "CREDENTIAL_PROBE_UNAVAILABLE", "OCR probe is unavailable."
                )
            image = base64.b64decode(
                (FIXTURE_ROOT / "ocr-probe.png.base64").read_text(encoding="ascii").strip(),
                validate=True,
            )
            return await self._invoke(
                normalized,
                self._verified_ocr_transport,
                credential,
                image,
            )
        return CredentialProbeResult(
            normalized,
            False,
            "CREDENTIAL_PROBE_UNSUPPORTED",
            "Credential probe is unsupported for this kind.",
        )

    async def _invoke(
        self,
        kind: CredentialKind,
        transport: Callable[..., Any],
        *arguments: object,
    ) -> CredentialProbeResult:
        try:
            result = transport(*arguments)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return CredentialProbeResult(
                kind, False, "CREDENTIAL_PROBE_FAILED", "Credential probe failed."
            )
        return CredentialProbeResult(
            kind,
            bool(result),
            None if result else "CREDENTIAL_PROBE_FAILED",
            None if result else "Credential probe failed.",
        )
