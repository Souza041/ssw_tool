"""Canal efêmero de autenticação SSW entre a VPS e um agente Windows autorizado.

As credenciais existem apenas na memória do processo durante uma solicitação curta.
Este módulo não importa nem utiliza o repositório SQLite do Local Agent.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import secrets
import threading
import time
import unicodedata
import re
from typing import Any, Callable


class RemoteAuthError(RuntimeError):
    pass


class RemoteAuthUnavailable(RemoteAuthError):
    pass


class RemoteAuthRateLimited(RemoteAuthError):
    pass


class RemoteAuthConflict(RemoteAuthError):
    pass


class RemoteAuthHumanVerificationRequired(RemoteAuthError):
    pass


class RemoteAuthSSWUnavailable(RemoteAuthError):
    pass


@dataclass(frozen=True, repr=False)
class RemoteCredentials:
    dominio: str
    cpf: str
    usuario: str
    senha: str = field(repr=False)
    unidade: str

    def as_payload(self) -> dict[str, str]:
        return {
            "dominio": self.dominio,
            "cpf": self.cpf,
            "usuario": self.usuario,
            "senha": self.senha,
            "unidade": self.unidade,
        }


@dataclass(frozen=True)
class RemoteAuthResult:
    accepted: bool
    outcome: str
    dominio: str
    cpf: str = field(repr=False)
    usuario: str
    unidade: str


@dataclass
class _PendingRequest:
    request_id: str
    challenge: str = field(repr=False)
    session_digest: str = field(repr=False)
    identity_fingerprint: str
    dominio: str
    cpf: str = field(repr=False)
    usuario: str
    unidade: str
    credentials: RemoteCredentials | None = field(repr=False)
    created_at: float
    expires_at: float
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    state: str = "PENDING"
    agent_id: str | None = None
    lease_token: str | None = field(default=None, repr=False)
    accepted: bool | None = None
    outcome: str | None = None


REMOTE_AUTH_OUTCOMES = frozenset({
    "authenticated",
    "invalid_credentials",
    "human_verification_required",
    "ssw_unavailable",
})


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def identity_fingerprint(
    *, dominio: str, cpf: str, usuario: str, unidade: str
) -> str:
    canonical = json.dumps(
        {
            "cpf": "".join(character for character in cpf if character.isdigit()),
            "dominio": _normalized(dominio),
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


def validate_credentials(credentials: RemoteCredentials) -> RemoteCredentials:
    dominio = credentials.dominio.strip()
    cpf = "".join(character for character in credentials.cpf if character.isdigit())
    usuario = credentials.usuario.strip()
    unidade = credentials.unidade.strip()
    senha = credentials.senha
    if not 1 <= len(dominio) <= 32:
        raise ValueError("Domínio inválido.")
    if len(cpf) != 11:
        raise ValueError("Informe um CPF com 11 dígitos.")
    if not 1 <= len(usuario) <= 80:
        raise ValueError("Usuário inválido.")
    if not 1 <= len(unidade) <= 16:
        raise ValueError("Unidade inválida.")
    if not isinstance(senha, str) or not 1 <= len(senha) <= 256:
        raise ValueError("Senha inválida.")
    return RemoteCredentials(dominio, cpf, usuario, senha, unidade)


class RemoteAuthBroker:
    """Coordena solicitações em memória com claim e conclusão de consumo único."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingRequest] = {}
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def reset(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.credentials = None
                pending.event.set()
            self._pending.clear()
            self._attempts.clear()

    def _cleanup(self, now: float) -> None:
        expired = [
            request_id
            for request_id, pending in self._pending.items()
            if pending.expires_at <= now
        ]
        for request_id in expired:
            pending = self._pending.pop(request_id)
            pending.credentials = None
            pending.state = "EXPIRED"
            pending.event.set()
        stale_attempts: list[str] = []
        for session_digest, attempts in self._attempts.items():
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if not attempts:
                stale_attempts.append(session_digest)
        for session_digest in stale_attempts:
            self._attempts.pop(session_digest, None)

    def create(
        self,
        credentials: RemoteCredentials,
        *,
        session_binding: str,
        ttl_seconds: int,
        per_minute: int,
        max_pending: int,
    ) -> str:
        clean = validate_credentials(credentials)
        if len(session_binding) < 32:
            raise ValueError("Sessão de login inválida.")
        now = time.monotonic()
        session_digest = hashlib.sha256(session_binding.encode("utf-8")).hexdigest()
        with self._lock:
            self._cleanup(now)
            attempts = self._attempts[session_digest]
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if any(
                item.session_digest == session_digest
                for item in self._pending.values()
            ):
                raise RemoteAuthConflict("Já existe um login em andamento.")
            if len(attempts) >= per_minute:
                raise RemoteAuthRateLimited("Limite temporário de login excedido.")
            if len(self._pending) >= max_pending:
                raise RemoteAuthUnavailable("Canal de autenticação temporariamente ocupado.")
            attempts.append(now)
            request_id = secrets.token_urlsafe(32)
            pending = _PendingRequest(
                request_id=request_id,
                challenge=secrets.token_urlsafe(32),
                session_digest=session_digest,
                identity_fingerprint=identity_fingerprint(
                    dominio=clean.dominio,
                    cpf=clean.cpf,
                    usuario=clean.usuario,
                    unidade=clean.unidade,
                ),
                dominio=clean.dominio,
                cpf=clean.cpf,
                usuario=clean.usuario,
                unidade=clean.unidade,
                credentials=clean,
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
                if pending.state != "PENDING" or pending.credentials is None:
                    continue
                credentials = pending.credentials.as_payload()
                pending.credentials = None
                pending.state = "CLAIMED"
                pending.agent_id = agent_id
                pending.lease_token = secrets.token_urlsafe(32)
                return {
                    "request_id": pending.request_id,
                    "challenge": pending.challenge,
                    "lease_token": pending.lease_token,
                    "identity_fingerprint": pending.identity_fingerprint,
                    "credentials": credentials,
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
        outcome: str | None = None,
    ) -> None:
        normalized_outcome = str(
            outcome or ("authenticated" if accepted else "invalid_credentials")
        ).strip().lower()
        if (
            normalized_outcome not in REMOTE_AUTH_OUTCOMES
            or accepted != (normalized_outcome == "authenticated")
        ):
            raise RemoteAuthConflict("Resultado de autenticação inconsistente.")
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
                or identity != pending.identity_fingerprint
            ):
                raise RemoteAuthConflict("Solicitação inexistente, expirada ou consumida.")
            expected = response_proof(
                secret,
                request_id=request_id,
                challenge=pending.challenge,
                identity=pending.identity_fingerprint,
                agent_id=agent_id,
                accepted=accepted,
            )
            if not hmac.compare_digest(expected, proof):
                raise RemoteAuthConflict("Confirmação criptográfica inválida.")
            pending.lease_token = None
            pending.accepted = accepted
            pending.outcome = normalized_outcome
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
                raise RemoteAuthConflict("Solicitação não pertence à sessão de login.")
            event = pending.event
        completed = event.wait(timeout_seconds)
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                raise RemoteAuthUnavailable("Solicitação de autenticação expirada.")
            pending.credentials = None
            if not completed or pending.state != "COMPLETED":
                pending.state = "EXPIRED"
                raise RemoteAuthUnavailable("Executor de autenticação indisponível.")
            return RemoteAuthResult(
                accepted=pending.accepted is True,
                outcome=str(pending.outcome or "ssw_unavailable"),
                dominio=pending.dominio,
                cpf=pending.cpf,
                usuario=pending.usuario,
                unidade=pending.unidade,
            )


class WindowsRemoteAuthExecutor:
    """Executa somente o login SSW efêmero; não recebe tarefas arbitrárias."""

    def __init__(
        self,
        client: Any,
        outcome_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.outcome_callback = outcome_callback

    def run_once(self) -> bool:
        response = self.client.remote_auth_claim()
        request = response.get("request")
        if request is None:
            return False
        if not isinstance(request, dict) or not isinstance(
            request.get("credentials"), dict
        ):
            raise RemoteAuthError("Contrato de autenticação remota inválido.")
        credentials = request["credentials"]
        required = {"dominio", "cpf", "usuario", "senha", "unidade"}
        if set(credentials) != required or any(
            not isinstance(credentials.get(name), str) for name in required
        ):
            credentials.clear()
            raise RemoteAuthError("Contrato de credenciais inválido.")
        request_id = str(request.get("request_id") or "")
        challenge = str(request.get("challenge") or "")
        lease_token = str(request.get("lease_token") or "")
        expected_identity = str(request.get("identity_fingerprint") or "")
        computed_identity = identity_fingerprint(
            dominio=credentials["dominio"],
            cpf=credentials["cpf"],
            usuario=credentials["usuario"],
            unidade=credentials["unidade"],
        )
        if (
            len(request_id) < 32
            or len(challenge) < 32
            or len(lease_token) < 32
            or not hmac.compare_digest(expected_identity, computed_identity)
        ):
            credentials.clear()
            raise RemoteAuthError("Vínculo de identidade inválido.")

        accepted = False
        outcome = "ssw_unavailable"
        ssw_client = None
        login_response = None
        menu_response = None
        try:
            from ssw.client import SSWClient

            ssw_client = SSWClient(
                dominio=credentials["dominio"],
                cpf=credentials["cpf"],
                usuario=credentials["usuario"],
                senha=credentials["senha"],
                unidade=credentials["unidade"],
            )
            login_response = ssw_client.login()
            menu_response = ssw_client.open_menu()
            login_html = str(getattr(login_response, "text", "")).casefold()
            menu_html = str(getattr(menu_response, "text", "")).casefold()
            login_confirmed = "frmlogin" in login_html and "menu01" in login_html
            menu_confirmed = all(
                re.search(
                    rf"<input\b[^>]*(?:name|id)\s*=\s*['\"]?{field}\b",
                    menu_html,
                    re.IGNORECASE,
                )
                for field in ("f2", "f3")
            )
            if not login_confirmed or not menu_confirmed:
                raise PermissionError("Resposta SSW não confirmou a identidade.")
            accepted = True
            outcome = "authenticated"
        except PermissionError:
            outcome = "invalid_credentials"
            accepted = False
        except Exception as exc:
            from ssw.authentication import SSWHumanVerificationRequired

            if isinstance(exc, SSWHumanVerificationRequired):
                outcome = "human_verification_required"
            else:
                # Nunca propagar texto do SSW: pode conter URL, cookie ou credencial.
                outcome = "ssw_unavailable"
            accepted = False
        finally:
            credentials.clear()
            login_response = None
            menu_response = None
            if ssw_client is not None:
                ssw_client.senha = ""
                try:
                    ssw_client.session.close()
                except Exception:
                    pass

        self.client.remote_auth_complete(
            request_id=request_id,
            lease_token=lease_token,
            challenge=challenge,
            identity_fingerprint=expected_identity,
            accepted=accepted,
            outcome=outcome,
        )
        if self.outcome_callback is not None:
            self.outcome_callback(outcome)
        return True


broker = RemoteAuthBroker()

