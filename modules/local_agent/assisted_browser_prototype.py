"""Protótipo isolado de autenticação humana e consulta SSW no mesmo navegador.

Não possui integração HTTP, runtime de navegador ou fila do Local Agent. O contrato deliberadamente
não oferece acesso ao estado interno do navegador: o contexto é um identificador opaco e
a única operação permitida é uma sondagem sintética, somente leitura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import secrets
import threading
import time
import unicodedata
from typing import Protocol


READONLY_PROBE = "ssw.readonly.session_probe"


class AssistedFlowError(RuntimeError):
    pass


class AssistedState(str, Enum):
    PENDING = "pending"
    AWAITING_HUMAN = "awaiting_human"
    AUTHENTICATED_CONTEXT = "authenticated_context"
    EXECUTING_READONLY = "executing_readonly"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OpaqueBrowserContext:
    context_id: str
    interactive_principal: str


@dataclass(frozen=True)
class BrowserAuthenticatedIdentity:
    username: str
    unit: str


class AssistedBrowserDriver(Protocol):
    """Porta mínima para um futuro componente interativo autorizado pelo SSW."""

    def open_official_login(self, *, interactive_principal: str) -> OpaqueBrowserContext: ...

    def wait_for_human_authentication(
        self, context: OpaqueBrowserContext
    ) -> BrowserAuthenticatedIdentity: ...

    def readonly_session_probe(self, context: OpaqueBrowserContext) -> dict[str, object]: ...

    def close(self, context: OpaqueBrowserContext) -> None: ...


@dataclass
class AssistedRequest:
    request_id: str
    session_digest: str = field(repr=False)
    nexus_subject: str
    operation: str
    created_at: float
    expires_at: float
    state: AssistedState = AssistedState.PENDING
    consumed: bool = False
    audit: list[dict[str, str]] = field(default_factory=list)
    result: dict[str, object] | None = None


def _subject(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if not 1 <= len(normalized) <= 80:
        raise ValueError("Identidade interativa inválida.")
    return normalized


def _binding_digest(value: str) -> str:
    if not isinstance(value, str) or len(value) < 32:
        raise ValueError("Vínculo da sessão Nexus inválido.")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_probe_result(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "menu_available", "unit_visible", "read_only",
    }:
        raise AssistedFlowError("Resultado da sondagem fora do contrato.")
    if type(value["menu_available"]) is not bool or value["read_only"] is not True:
        raise AssistedFlowError("Sondagem não confirmou modo somente leitura.")
    unit = str(value["unit_visible"] or "").strip()
    if len(unit) > 16:
        raise AssistedFlowError("Unidade retornada fora do limite.")
    return {
        "menu_available": value["menu_available"],
        "unit_visible": unit,
        "read_only": True,
    }


class AssistedBrowserPrototype:
    """Broker efêmero e de consumo único para testes do desenho interativo."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: dict[str, AssistedRequest] = {}

    @staticmethod
    def _event(request: AssistedRequest, event: str) -> None:
        request.audit.append({
            "request_id": request.request_id,
            "subject": request.nexus_subject,
            "operation": request.operation,
            "event": event,
        })

    def create(
        self,
        *,
        nexus_subject: str,
        session_binding: str,
        operation: str = READONLY_PROBE,
        ttl_seconds: int = 120,
        now: float | None = None,
    ) -> str:
        if operation != READONLY_PROBE:
            raise AssistedFlowError("Operação não autorizada no protótipo.")
        if not 30 <= ttl_seconds <= 300:
            raise ValueError("Expiração fora do limite do protótipo.")
        subject = _subject(nexus_subject)
        digest = _binding_digest(session_binding)
        instant = time.monotonic() if now is None else now
        with self._lock:
            if any(
                item.session_digest == digest
                and item.state not in {
                    AssistedState.COMPLETED, AssistedState.FAILED, AssistedState.EXPIRED,
                }
                for item in self._requests.values()
            ):
                raise AssistedFlowError("Já existe autenticação assistida nesta sessão.")
            request_id = secrets.token_urlsafe(32)
            request = AssistedRequest(
                request_id=request_id,
                session_digest=digest,
                nexus_subject=subject,
                operation=operation,
                created_at=instant,
                expires_at=instant + ttl_seconds,
            )
            self._event(request, "created")
            self._requests[request_id] = request
            return request_id

    def run_readonly_simulation(
        self,
        request_id: str,
        *,
        session_binding: str,
        interactive_principal: str,
        driver: AssistedBrowserDriver,
        now: float | None = None,
    ) -> dict[str, object]:
        instant = time.monotonic() if now is None else now
        digest = _binding_digest(session_binding)
        principal = _subject(interactive_principal)
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.consumed:
                raise AssistedFlowError("Solicitação inexistente ou já consumida.")
            if request.state is not AssistedState.PENDING:
                raise AssistedFlowError("Solicitação já está em processamento.")
            if not secrets.compare_digest(request.session_digest, digest):
                raise AssistedFlowError("Solicitação não pertence à sessão Nexus.")
            if instant >= request.expires_at:
                request.state = AssistedState.EXPIRED
                request.consumed = True
                self._event(request, "expired")
                raise AssistedFlowError("Solicitação de autenticação assistida expirada.")
            if not secrets.compare_digest(request.nexus_subject, principal):
                request.state = AssistedState.FAILED
                request.consumed = True
                self._event(request, "interactive_principal_mismatch")
                raise AssistedFlowError("Identidade interativa diverge da sessão Nexus.")
            request.state = AssistedState.AWAITING_HUMAN
            self._event(request, "human_authentication_started")

        context: OpaqueBrowserContext | None = None
        try:
            context = driver.open_official_login(interactive_principal=principal)
            if _subject(context.interactive_principal) != principal:
                raise AssistedFlowError("Contexto interativo pertence a outro funcionário.")
            identity = driver.wait_for_human_authentication(context)
            if _subject(identity.username) != request.nexus_subject:
                raise AssistedFlowError("Identidade confirmada pelo SSW diverge do Nexus.")
            with self._lock:
                request.state = AssistedState.AUTHENTICATED_CONTEXT
                self._event(request, "authenticated_in_same_context")
                request.state = AssistedState.EXECUTING_READONLY
                self._event(request, "readonly_probe_started")
            probe = _safe_probe_result(driver.readonly_session_probe(context))
            if probe["unit_visible"] and probe["unit_visible"].casefold() != identity.unit.casefold():
                raise AssistedFlowError("Unidade da sondagem diverge da identidade autenticada.")
            result = {
                "request_id": request.request_id,
                "subject": request.nexus_subject,
                "operation": request.operation,
                "state": AssistedState.COMPLETED.value,
                "probe": probe,
            }
            with self._lock:
                request.result = result
                request.state = AssistedState.COMPLETED
                request.consumed = True
                self._event(request, "completed")
            return result
        except BaseException:
            with self._lock:
                if request.state not in {AssistedState.COMPLETED, AssistedState.EXPIRED}:
                    request.state = AssistedState.FAILED
                    request.consumed = True
                    self._event(request, "failed_closed")
            raise
        finally:
            if context is not None:
                driver.close(context)

    def status(self, request_id: str, *, session_binding: str) -> dict[str, object]:
        digest = _binding_digest(session_binding)
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or not secrets.compare_digest(request.session_digest, digest):
                raise AssistedFlowError("Solicitação não pertence à sessão Nexus.")
            return {
                "request_id": request.request_id,
                "subject": request.nexus_subject,
                "operation": request.operation,
                "state": request.state.value,
                "consumed": request.consumed,
                "audit": [dict(item) for item in request.audit],
                "result": dict(request.result) if request.result else None,
            }

