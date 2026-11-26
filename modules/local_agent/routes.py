from __future__ import annotations

import hmac
import hashlib
import secrets
from typing import Any

from fastapi import Form, APIRouter, Header, HTTPException, Path as ApiPath, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from modules.local_agent.bootstrap import (
    canonical_ping_body,
    sign_request,
    validate_freshness,
    validate_nonce,
    verify_signature,
)

from modules.local_agent.config import (
    AGENT_ID_RE,
    LocalAgentConfigurationError,
    ServerSettings,
    load_server_settings,
)
from modules.local_agent.repository import (
    BootstrapRateLimitError,
    BootstrapReplayError,
    LocalAgentRepository,
)
from modules.local_agent.remote_auth import RemoteAuthConflict, broker
from web.admin_access import is_admin
from web.session_store import obter_client


router = APIRouter(tags=["Nexus Local Agent"])


class AgentSessionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_binding: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    state: str = Field(pattern=r"^(authenticated|busy|reauthentication_required)$")
    operation: str = Field(default="", max_length=120)
    observed_unit: str = Field(default="", pattern=r"^$|^[A-Z0-9]{3,16}$")


class ClaimRequest(BaseModel):
    capabilities: list[str] = Field(min_length=1, max_length=100)
    sessions: list[AgentSessionReport] = Field(default_factory=list, max_length=20)


class LeaseRequest(BaseModel):
    job_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    lease_token: str = Field(min_length=32, max_length=512)


class HeartbeatRequest(LeaseRequest):
    progress_current: int = Field(default=0, ge=0)
    progress_total: int = Field(default=0, ge=0)


class LogEntry(BaseModel):
    level: str = Field(default="INFO", max_length=10)
    message: str = Field(min_length=1)


class LogsRequest(LeaseRequest):
    entries: list[LogEntry] = Field(min_length=1, max_length=100)


class CompleteRequest(LeaseRequest):
    succeeded: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = Field(default=None, max_length=2048)


class RemoteAuthCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=32, max_length=128)
    lease_token: str = Field(min_length=32, max_length=128)
    accepted: bool
    identity_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


class EnqueueRequest(BaseModel):
    task: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=100, ge=0, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=10)


class BootstrapPingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="homologacao", min_length=1, max_length=200)


class PairingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dominio: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    usuario: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")


class PairingRedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9A-Fa-f]{12}-[0-9A-Fa-f]{12}$")
    machine_name: str = Field(min_length=1, max_length=64)


def _settings() -> ServerSettings:
    try:
        settings = load_server_settings()
    except LocalAgentConfigurationError:
        raise HTTPException(status_code=503, detail="Local Agent com configuração inválida.") from None
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="Nexus Local Agent desativado.")
    return settings


def _repository(settings: ServerSettings) -> LocalAgentRepository:
    return LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
    )


def _agent(
    settings: ServerSettings,
    repository: LocalAgentRepository,
    agent_id: str,
    authorization: str,
) -> tuple[str, frozenset[str]]:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise HTTPException(status_code=401, detail="Credenciais do agente inválidas.")
    prefix = "Bearer "
    token = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    expected = settings.tokens.get(agent_id, "")
    if token and expected and hmac.compare_digest(token, expected):
        return agent_id, settings.agent_tasks.get(agent_id, frozenset())
    dynamic_tasks = repository.authenticate_dynamic_agent(agent_id=agent_id, token=token)
    if not token or dynamic_tasks is None:
        raise HTTPException(status_code=401, detail="Credenciais do agente inválidas ou revogadas.")
    return agent_id, dynamic_tasks


def _agent_context(
    agent_id: str,
    authorization: str,
) -> tuple[ServerSettings, LocalAgentRepository, str, frozenset[str]]:
    settings = _settings()
    repository = _repository(settings)
    authenticated_id, tasks = _agent(settings, repository, agent_id, authorization)
    return settings, repository, authenticated_id, tasks


def _bootstrap_context(request: Request):
    settings = _settings()
    if not settings.bootstrap_enabled:
        raise HTTPException(status_code=503, detail="Bootstrap operacional desativado.")
    remote = str(request.client.host if request.client else "unknown")[:128]
    return settings, _repository(settings), remote


def _safe_bootstrap_identity(value: str, fallback: str) -> str:
    if 1 <= len(value) <= 128 and value.isascii():
        return value
    return f"{fallback}-{hashlib.sha256(value.encode('utf-8', 'ignore')).hexdigest()[:24]}"


def _audit_bootstrap_rejection(
    repository: LocalAgentRepository,
    settings: ServerSettings,
    *,
    key_id: str,
    nonce: str,
    remote: str,
    outcome: str,
) -> None:
    recorded = repository.audit_bootstrap_rejection(
        key_id=_safe_bootstrap_identity(key_id, "invalid-key"),
        nonce=_safe_bootstrap_identity(nonce, "invalid-nonce"),
        remote_address=remote,
        outcome=outcome,
        per_minute=settings.bootstrap_per_minute,
        per_hour=settings.bootstrap_per_hour,
    )
    if recorded == "RATE_LIMITED":
        raise HTTPException(status_code=429, detail="Limite do bootstrap excedido.")
    if recorded == "REPLAY":
        raise HTTPException(status_code=409, detail="Requisição de bootstrap repetida.")


def _verify_bootstrap(
    repository: LocalAgentRepository,
    settings: ServerSettings,
    *,
    key_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    method: str,
    path: str,
    body: bytes,
    remote: str,
) -> None:
    try:
        validate_freshness(
            timestamp, max_skew_seconds=settings.bootstrap_max_skew_seconds
        )
        validate_nonce(nonce)
    except ValueError:
        _audit_bootstrap_rejection(
            repository, settings, key_id=key_id, nonce=nonce,
            remote=remote, outcome="INVALID_REQUEST",
        )
        raise HTTPException(status_code=401, detail="Autenticação do bootstrap inválida.") from None
    expected = sign_request(
        settings.bootstrap_secret,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    )
    if key_id != settings.bootstrap_key_id or not verify_signature(expected, signature):
        _audit_bootstrap_rejection(
            repository, settings, key_id=key_id, nonce=nonce,
            remote=remote, outcome="INVALID_SIGNATURE",
        )
        raise HTTPException(status_code=401, detail="Autenticação do bootstrap inválida.")


def _admin(request: Request) -> tuple[ServerSettings, LocalAgentRepository]:
    if obter_client(request) is None:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores.")
    settings = _settings()
    return settings, _repository(settings)


def _csrf(request: Request, supplied: str) -> None:
    expected = str(request.session.get("local_agent_csrf") or "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Token CSRF inválido.")


def _lease_error(call):
    try:
        return call()
    except LookupError:
        raise HTTPException(status_code=409, detail="Lease inexistente, expirado ou substituído.") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/api/local-agent/v1/claim")
def claim(
    body: ClaimRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    settings, repository, agent_id, authorized_tasks = _agent_context(x_nexus_agent_id, authorization)
    capabilities = (
        set(body.capabilities)
        & set(settings.allowed_tasks)
        & set(authorized_tasks)
    )
    if not capabilities:
        raise HTTPException(status_code=422, detail="Executor sem capacidades autorizadas.")
    job = _lease_error(lambda: repository.claim(
        agent_id=agent_id,
        capabilities=capabilities,
        lease_seconds=settings.lease_seconds,
        sessions=[item.model_dump() for item in body.sessions],
    ))
    return {"job": job, "lease_seconds": settings.lease_seconds}


@router.post("/api/local-agent/v1/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    settings, repository, agent_id, _ = _agent_context(x_nexus_agent_id, authorization)
    return _lease_error(lambda: repository.heartbeat(
        job_id=body.job_id,
        agent_id=agent_id,
        lease_token=body.lease_token,
        lease_seconds=settings.lease_seconds,
        progress_current=body.progress_current,
        progress_total=body.progress_total,
    ))


@router.post("/api/local-agent/v1/logs")
def logs(
    body: LogsRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    settings, repository, agent_id, _ = _agent_context(x_nexus_agent_id, authorization)
    accepted = _lease_error(lambda: repository.append_logs(
        job_id=body.job_id,
        agent_id=agent_id,
        lease_token=body.lease_token,
        entries=[entry.model_dump() for entry in body.entries],
    ))
    return {"accepted": accepted}


@router.post("/api/local-agent/v1/complete")
def complete(
    body: CompleteRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    settings, repository, agent_id, _ = _agent_context(x_nexus_agent_id, authorization)
    status = _lease_error(lambda: repository.complete(
        job_id=body.job_id,
        agent_id=agent_id,
        lease_token=body.lease_token,
        succeeded=body.succeeded,
        result=body.result,
        error_message=body.error_message,
    ))
    return {"status": status}


def _remote_auth_agent(
    x_nexus_agent_id: str,
    authorization: str,
) -> tuple[ServerSettings, str, str]:
    settings = _settings()
    if not settings.remote_auth_enabled:
        raise HTTPException(status_code=503, detail="Autenticação remota desativada.")
    repository = _repository(settings)
    agent_id, _ = _agent(settings, repository, x_nexus_agent_id, authorization)
    static_authorized = set(settings.remote_auth_bindings.values())
    if settings.remote_auth_agent_id:
        static_authorized.add(settings.remote_auth_agent_id)
    if agent_id in settings.tokens and agent_id not in static_authorized:
        raise HTTPException(status_code=403, detail="Agente não autorizado para autenticação.")
    proof_secret = settings.tokens.get(agent_id) or repository.dynamic_agent_proof_secret(agent_id)
    if not proof_secret:
        raise HTTPException(status_code=401, detail="Credenciais do agente inválidas ou revogadas.")
    return settings, agent_id, proof_secret


def _remote_auth_claim_impl(
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    _, agent_id, _ = _remote_auth_agent(x_nexus_agent_id, authorization)
    return {"request": broker.claim(agent_id=agent_id)}


@router.post("/api/local-agent/v1/remote-auth/claim")
async def remote_auth_claim(
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    return _remote_auth_claim_impl(x_nexus_agent_id, authorization)


def _remote_auth_complete_impl(
    body: RemoteAuthCompleteRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    _, agent_id, secret = _remote_auth_agent(x_nexus_agent_id, authorization)
    try:
        broker.complete(
            request_id=body.request_id,
            lease_token=body.lease_token,
            agent_id=agent_id,
            accepted=body.accepted,
            identity=body.identity_fingerprint,
            proof=body.proof,
            secret=secret,
        )
    except RemoteAuthConflict:
        raise HTTPException(
            status_code=409,
            detail="Solicitação de autenticação inexistente, expirada ou consumida.",
        ) from None
    return {"accepted": True}


@router.post("/api/local-agent/v1/remote-auth/complete")
async def remote_auth_complete(
    body: RemoteAuthCompleteRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    return _remote_auth_complete_impl(body, x_nexus_agent_id, authorization)


@router.post("/api/local-agent/v1/pair", status_code=201)
def redeem_pairing(body: PairingRedeemRequest):
    settings = _settings()
    if not settings.pairing_enabled:
        raise HTTPException(status_code=503, detail="Pareamento de agentes desativado.")
    try:
        result = _repository(settings).redeem_pairing(
            code=body.code,
            machine_name=body.machine_name,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return result


@router.post("/api/local-agent/v1/bootstrap/system-ping", status_code=202)
def bootstrap_system_ping(
    request: Request,
    body: BootstrapPingRequest,
    x_nexus_bootstrap_key_id: str = Header("", alias="X-Nexus-Bootstrap-Key-Id"),
    x_nexus_bootstrap_timestamp: str = Header("", alias="X-Nexus-Bootstrap-Timestamp"),
    x_nexus_bootstrap_nonce: str = Header("", alias="X-Nexus-Bootstrap-Nonce"),
    x_nexus_bootstrap_signature: str = Header("", alias="X-Nexus-Bootstrap-Signature"),
):
    settings, repository, remote = _bootstrap_context(request)
    path = "/api/local-agent/v1/bootstrap/system-ping"
    _verify_bootstrap(
        repository,
        settings,
        key_id=x_nexus_bootstrap_key_id,
        timestamp=x_nexus_bootstrap_timestamp,
        nonce=x_nexus_bootstrap_nonce,
        signature=x_nexus_bootstrap_signature,
        method="POST",
        path=path,
        body=canonical_ping_body(body.message),
        remote=remote,
    )
    try:
        job_id = repository.bootstrap_enqueue_ping(
            key_id=x_nexus_bootstrap_key_id,
            nonce=x_nexus_bootstrap_nonce,
            remote_address=remote,
            message=body.message,
            per_minute=settings.bootstrap_per_minute,
            per_hour=settings.bootstrap_per_hour,
        )
    except BootstrapReplayError:
        raise HTTPException(status_code=409, detail="Requisição de bootstrap repetida.") from None
    except BootstrapRateLimitError:
        raise HTTPException(status_code=429, detail="Limite do bootstrap excedido.") from None
    return {"accepted": True, "job_id": job_id, "task": "system.ping"}


@router.post("/api/local-agent/v1/bootstrap/system-ping/{job_id}/status")
def bootstrap_system_ping_status(
    request: Request,
    job_id: str = ApiPath(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    x_nexus_bootstrap_key_id: str = Header("", alias="X-Nexus-Bootstrap-Key-Id"),
    x_nexus_bootstrap_timestamp: str = Header("", alias="X-Nexus-Bootstrap-Timestamp"),
    x_nexus_bootstrap_nonce: str = Header("", alias="X-Nexus-Bootstrap-Nonce"),
    x_nexus_bootstrap_signature: str = Header("", alias="X-Nexus-Bootstrap-Signature"),
):
    settings, repository, remote = _bootstrap_context(request)
    path = f"/api/local-agent/v1/bootstrap/system-ping/{job_id}/status"
    _verify_bootstrap(
        repository,
        settings,
        key_id=x_nexus_bootstrap_key_id,
        timestamp=x_nexus_bootstrap_timestamp,
        nonce=x_nexus_bootstrap_nonce,
        signature=x_nexus_bootstrap_signature,
        method="POST",
        path=path,
        body=b"",
        remote=remote,
    )
    try:
        result = repository.bootstrap_authorize_status(
            key_id=x_nexus_bootstrap_key_id,
            nonce=x_nexus_bootstrap_nonce,
            remote_address=remote,
            job_id=job_id,
            per_minute=settings.bootstrap_per_minute,
            per_hour=settings.bootstrap_per_hour,
        )
    except BootstrapReplayError:
        raise HTTPException(status_code=409, detail="Requisição de bootstrap repetida.") from None
    except BootstrapRateLimitError:
        raise HTTPException(status_code=429, detail="Limite do bootstrap excedido.") from None
    if result is None:
        raise HTTPException(status_code=404, detail="Ping de bootstrap não encontrado.")
    return result


@router.get("/admin/local-agent")
def admin_overview(request: Request, limit: int = Query(50, ge=1, le=200)):
    settings, repository = _admin(request)
    csrf_token = request.session.setdefault("local_agent_csrf", secrets.token_urlsafe(32))
    return {
        "enabled": settings.enabled,
        "allowed_tasks": sorted(settings.allowed_tasks),
        "bootstrap_enabled": settings.bootstrap_enabled,
        "jobs": repository.list_jobs(limit=limit),
        "agents": repository.list_agents(),
        "bootstrap_audit": repository.list_bootstrap_audit(limit=100),
        "csrf_token": csrf_token,
    }


@router.post("/admin/local-agent/pairings", status_code=201)
def admin_create_pairing(
    request: Request,
    body: PairingCreateRequest,
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
):
    settings, repository = _admin(request)
    _csrf(request, x_csrf_token)
    if not settings.pairing_enabled:
        raise HTTPException(status_code=503, detail="Pareamento de agentes desativado.")
    client = obter_client(request)
    try:
        code = repository.create_pairing(
            identity_key=f"{body.dominio}/{body.usuario}",
            tasks=settings.paired_tasks,
            created_by=str(getattr(client, "usuario", "admin") or "admin"),
            ttl_seconds=settings.pairing_ttl_seconds,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"code": code, "expires_in_seconds": settings.pairing_ttl_seconds}


@router.post("/admin/local-agent/agents/{agent_id}/revoke")
def admin_revoke_agent(
    request: Request,
    agent_id: str = ApiPath(pattern=r"^[a-z][a-z0-9_-]{2,63}$"),
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
):
    _, repository = _admin(request)
    _csrf(request, x_csrf_token)
    if not repository.revoke_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agente dinâmico ativo não encontrado.")
    return {"revoked": True, "agent_id": agent_id}


@router.post("/admin/local-agent/jobs", status_code=202)
def admin_enqueue(
    request: Request,
    body: EnqueueRequest,
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
):
    settings, repository = _admin(request)
    _csrf(request, x_csrf_token)
    if body.task not in settings.allowed_tasks:
        raise HTTPException(status_code=422, detail="Tarefa fora da allowlist do servidor.")
    try:
        job_id = repository.enqueue(
            task=body.task,
            payload=body.payload,
            priority=body.priority,
            max_attempts=body.max_attempts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"accepted": True, "job_id": job_id, "status": "QUEUED"}


@router.get("/admin/local-agent/jobs/{job_id}")
def admin_job(
    request: Request,
    job_id: str = ApiPath(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
):
    _, repository = _admin(request)
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return job


@router.post("/admin/local-agent/jobs/{job_id}/cancel")
def admin_cancel(
    request: Request,
    job_id: str = ApiPath(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    x_csrf_token: str = Header("", alias="X-CSRF-Token"),
):
    _, repository = _admin(request)
    _csrf(request, x_csrf_token)
    try:
        status = repository.request_cancel(job_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.") from None
    return {"job_id": job_id, "status": status}


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usuario: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")
    machine_name: str = Field(min_length=1,max_length=64)
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{64}$")

class EnrollmentPoll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{64}$")

@router.post("/api/local-agent/v1/enrollment", status_code=201)
def enrollment_request(body: EnrollmentRequest, request: Request):
    settings = _settings()
    if not settings.pairing_enabled:
        raise HTTPException(503, "Pareamento desativado.")
    try:
        ref = _repository(settings).request_enrollment(usuario=body.usuario, machine_name=body.machine_name, token=body.token, source=request.client.host if request.client else "unknown")
    except (ValueError, RuntimeError):
        raise HTTPException(409, "Solicitação indisponível ou limite atingido.") from None
    return {"id":ref}

@router.post("/api/local-agent/v1/enrollment/{ref}/poll")
def enrollment_poll(ref: str, body: EnrollmentPoll):
    settings = _settings()
    if not settings.pairing_enabled:
        raise HTTPException(503, "Pareamento desativado.")
    try:
        return _repository(settings).collect_enrollment(ref, token=body.token, tasks=settings.paired_tasks & settings.allowed_tasks)
    except LookupError:
        raise HTTPException(404, "Solicitação indisponível.") from None
    except RuntimeError:
        raise HTTPException(409, "Solicitação indisponível.") from None

@router.get("/admin/agent-manager")
def agent_manager(request: Request):
    from fastapi.templating import Jinja2Templates
    from modules.local_agent.repository import utc_text, utc_now
    settings, repo = _admin(request)
    requests, agents = repo.enrollment_overview()
    for agent in agents:
        agent['online'] = not agent['revoked_at'] and repo.agent_online(agent['agent_id'], offline_seconds=settings.agent_offline_seconds)
    return Jinja2Templates(directory="web/templates").TemplateResponse("agent_manager.html", {"request":request,"is_admin":True,"requests":requests,"agents":agents,"now":utc_text(utc_now()),"csrf":request.session.setdefault("local_agent_csrf", secrets.token_urlsafe(32))})

@router.post("/admin/agent-manager/decision")
def agent_manager_decision(request: Request, ref: str = Form(...), action: str = Form(...), csrf: str = Form(...)):
    from fastapi.responses import RedirectResponse
    settings, repo = _admin(request)
    _csrf(request, csrf)
    if not settings.pairing_enabled or action not in {"approve","reject","revoke"}:
        raise HTTPException(409, "Ação indisponível.")
    try:
        if action == "revoke":
            if not repo.revoke_agent(ref):
                raise LookupError()
        else:
            repo.decide_enrollment(ref, approve=action=="approve", actor=str(obter_client(request).usuario))
    except (LookupError, RuntimeError):
        raise HTTPException(409, "Registro indisponível.") from None
    return RedirectResponse("/admin/agent-manager", status_code=303)
