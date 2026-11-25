"""Autenticacao humana remota, sem transportar credenciais ou cookies.

A VPS publica apenas a identidade esperada e um vinculo opaco de sessao. O agente
Windows abre o Chromium homologado, o proprio usuario autentica no SSW e o contexto
permanece exclusivamente na memoria do agente.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import unicodedata
from typing import Any


class RemoteAuthError(RuntimeError):
    pass


class RemoteAuthUnavailable(RemoteAuthError):
    pass


class RemoteAuthRateLimited(RemoteAuthError):
    pass


class RemoteAuthConflict(RemoteAuthError):
    pass


@dataclass(frozen=True)
class RemoteIdentity:
    dominio: str
    usuario: str
    unidade: str
    session_binding: str = field(repr=False)

    def as_payload(self) -> dict[str, str]:
        return {
            "dominio": self.dominio,
            "usuario": self.usuario,
            "unidade": self.unidade,
            "session_binding": self.session_binding,
        }


@dataclass(frozen=True)
class RemoteAuthResult:
    accepted: bool
    dominio: str
    usuario: str
    unidade: str
    session_binding: str = field(repr=False)


@dataclass
class _PendingRequest:
    request_id: str
    challenge: str = field(repr=False)
    session_digest: str = field(repr=False)
    identity_fingerprint: str
    identity: RemoteIdentity = field(repr=False)
    created_at: float
    expires_at: float
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    state: str = "PENDING"
    agent_id: str | None = None
    lease_token: str | None = field(default=None, repr=False)
    accepted: bool | None = None


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def validate_identity(identity: RemoteIdentity) -> RemoteIdentity:
    dominio = identity.dominio.strip().upper()
    usuario = identity.usuario.strip()
    unidade = identity.unidade.strip().upper()
    binding = identity.session_binding.strip()
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", dominio):
        raise ValueError("Dominio invalido.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", usuario):
        raise ValueError("Usuario invalido.")
    if not re.fullmatch(r"[A-Z0-9]{3,16}", unidade):
        raise ValueError("Unidade invalida.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", binding):
        raise ValueError("Vinculo de sessao invalido.")
    return RemoteIdentity(dominio, usuario, unidade, binding)


def identity_fingerprint(
    *, dominio: str, usuario: str, unidade: str, session_binding: str = "", cpf: str = ""
) -> str:
    canonical = json.dumps(
        {
            "cpf": "".join(character for character in cpf if character.isdigit()),
            "dominio": _normalized(dominio),
            "session_binding": session_binding,
            "unidade": _normalized(unidade),
            "usuario": _normalized(usuario),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def response_proof(
    secret: str,
    *,
    request_id: str,
    challenge: str,
    identity: str,
    agent_id: str,
    accepted: bool,
) -> str:
    canonical = json.dumps(
        {
            "accepted": accepted,
            "agent_id": agent_id,
            "challenge": challenge,
            "identity": identity,
            "request_id": request_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


class RemoteAuthBroker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingRequest] = {}
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()
            self._attempts.clear()

    def _cleanup(self, now: float) -> None:
        expired = [
            request_id for request_id, pending in self._pending.items()
            if pending.expires_at <= now
        ]
        for request_id in expired:
            pending = self._pending.pop(request_id)
            pending.state = "EXPIRED"
            pending.event.set()
        for session_digest, attempts in list(self._attempts.items()):
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(session_digest, None)

    def create(
        self,
        identity: RemoteIdentity,
        *,
        session_binding: str,
        ttl_seconds: int,
        per_minute: int,
        max_pending: int,
    ) -> str:
        clean = validate_identity(identity)
        if not hmac.compare_digest(clean.session_binding, session_binding):
            raise ValueError("Vinculo de sessao divergente.")
        now = time.monotonic()
        session_digest = hashlib.sha256(session_binding.encode("utf-8")).hexdigest()
        with self._lock:
            self._cleanup(now)
            attempts = self._attempts[session_digest]
            if any(
                item.session_digest == session_digest
                for item in self._pending.values()
            ):
                raise RemoteAuthConflict("Ja existe um login em andamento.")
            if len(attempts) >= per_minute:
                raise RemoteAuthRateLimited("Limite temporario de login excedido.")
            if len(self._pending) >= max_pending:
                raise RemoteAuthUnavailable("Canal de autenticacao temporariamente ocupado.")
            attempts.append(now)
            request_id = secrets.token_urlsafe(32)
            pending = _PendingRequest(
                request_id=request_id,
                challenge=secrets.token_urlsafe(32),
                session_digest=session_digest,
                identity_fingerprint=identity_fingerprint(**clean.as_payload()),
                identity=clean,
                created_at=now,
                expires_at=now + ttl_seconds,
            )
            self._pending[request_id] = pending
            return request_id

    def claim(self, *, agent_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            for pending in self._pending.values():
                if pending.state != "PENDING":
                    continue
                pending.state = "CLAIMED"
                pending.agent_id = agent_id
                pending.lease_token = secrets.token_urlsafe(32)
                return {
                    "request_id": pending.request_id,
                    "challenge": pending.challenge,
                    "lease_token": pending.lease_token,
                    "identity_fingerprint": pending.identity_fingerprint,
                    "identity": pending.identity.as_payload(),
                }
        return None

    def complete(
        self,
        *,
        request_id: str,
        lease_token: str,
        agent_id: str,
        accepted: bool,
        identity: str,
        proof: str,
        secret: str,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            pending = self._pending.get(request_id)
            if (
                pending is None
                or pending.state != "CLAIMED"
                or pending.agent_id != agent_id
                or not pending.lease_token
                or not hmac.compare_digest(pending.lease_token, lease_token)
                or not hmac.compare_digest(identity, pending.identity_fingerprint)
            ):
                raise RemoteAuthConflict("Solicitacao inexistente, expirada ou consumida.")
            expected = response_proof(
                secret,
                request_id=request_id,
                challenge=pending.challenge,
                identity=pending.identity_fingerprint,
                agent_id=agent_id,
                accepted=accepted,
            )
            if not hmac.compare_digest(expected, proof):
                raise RemoteAuthConflict("Confirmacao criptografica invalida.")
            pending.lease_token = None
            pending.accepted = accepted
            pending.state = "COMPLETED"
            pending.event.set()

    def wait_and_consume(
        self,
        request_id: str,
        *,
        session_binding: str,
        timeout_seconds: float,
    ) -> RemoteAuthResult:
        session_digest = hashlib.sha256(session_binding.encode("utf-8")).hexdigest()
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or not hmac.compare_digest(
                pending.session_digest, session_digest
            ):
                raise RemoteAuthConflict("Solicitacao nao pertence a sessao de login.")
            event = pending.event
        completed = event.wait(timeout_seconds)
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                raise RemoteAuthUnavailable("Solicitacao de autenticacao expirada.")
            if not completed or pending.state != "COMPLETED":
                pending.state = "EXPIRED"
                raise RemoteAuthUnavailable("Executor de autenticacao indisponivel.")
            identity = pending.identity
            return RemoteAuthResult(
                accepted=pending.accepted is True,
                dominio=identity.dominio,
                usuario=identity.usuario,
                unidade=identity.unidade,
                session_binding=identity.session_binding,
            )


class WindowsRemoteAuthExecutor:
    """Abre o Chromium homologado e mantem a sessao apenas no agente Windows."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def run_once(self) -> bool:
        response = self.client.remote_auth_claim()
        request = response.get("request")
        if request is None:
            return False
        if not isinstance(request, dict) or not isinstance(request.get("identity"), dict):
            raise RemoteAuthError("Contrato de autenticacao remota invalido.")
        identity_payload = request["identity"]
        if set(identity_payload) != {"dominio", "usuario", "unidade", "session_binding"}:
            raise RemoteAuthError("Contrato de identidade invalido.")
        identity = validate_identity(RemoteIdentity(**identity_payload))
        request_id = str(request.get("request_id") or "")
        challenge = str(request.get("challenge") or "")
        lease_token = str(request.get("lease_token") or "")
        expected_identity = str(request.get("identity_fingerprint") or "")
        computed_identity = identity_fingerprint(**identity.as_payload())
        if (
            len(request_id) < 32
            or len(challenge) < 32
            or len(lease_token) < 32
            or not hmac.compare_digest(expected_identity, computed_identity)
        ):
            raise RemoteAuthError("Vinculo de identidade invalido.")

        accepted = False
        try:
            from ssw.client import SSWClient

            ssw_client = SSWClient(
                dominio=identity.dominio,
                usuario=identity.usuario,
                unidade=identity.unidade,
                auth_mode="browser",
                session_binding=identity.session_binding,
            )
            ssw_client.login()
            ssw_client.open_menu()
            accepted = True
        except Exception:
            accepted = False

        self.client.remote_auth_complete(
            request_id=request_id,
            lease_token=lease_token,
            challenge=challenge,
            identity_fingerprint=expected_identity,
            accepted=accepted,
        )
        return True


broker = RemoteAuthBroker()
