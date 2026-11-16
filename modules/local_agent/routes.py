from __future__ import annotations

import hmac
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Path as ApiPath, Query, Request
from pydantic import BaseModel, Field

from modules.local_agent.config import (
    AGENT_ID_RE,
    LocalAgentConfigurationError,
    ServerSettings,
    load_server_settings,
)
from modules.local_agent.repository import LocalAgentRepository
from web.admin_access import is_admin
from web.session_store import obter_client


router = APIRouter(tags=["Nexus Local Agent"])


class ClaimRequest(BaseModel):
    capabilities: list[str] = Field(min_length=1, max_length=100)


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


class EnqueueRequest(BaseModel):
    task: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=100, ge=0, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=10)


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
    agent_id: str,
    authorization: str,
) -> str:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise HTTPException(status_code=401, detail="Credenciais do agente inválidas.")
    prefix = "Bearer "
    token = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    expected = settings.tokens.get(agent_id, "")
    if not token or not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Credenciais do agente inválidas.")
    return agent_id


def _agent_context(
    agent_id: str,
    authorization: str,
) -> tuple[ServerSettings, LocalAgentRepository, str]:
    settings = _settings()
    return settings, _repository(settings), _agent(settings, agent_id, authorization)


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
    settings, repository, agent_id = _agent_context(x_nexus_agent_id, authorization)
    capabilities = (
        set(body.capabilities)
        & set(settings.allowed_tasks)
        & set(settings.agent_tasks.get(agent_id, ()))
    )
    if not capabilities:
        raise HTTPException(status_code=422, detail="Executor sem capacidades autorizadas.")
    job = _lease_error(lambda: repository.claim(
        agent_id=agent_id,
        capabilities=capabilities,
        lease_seconds=settings.lease_seconds,
    ))
    return {"job": job, "lease_seconds": settings.lease_seconds}


@router.post("/api/local-agent/v1/heartbeat")
def heartbeat(
    body: HeartbeatRequest,
    x_nexus_agent_id: str = Header("", alias="X-Nexus-Agent-Id"),
    authorization: str = Header("", alias="Authorization"),
):
    settings, repository, agent_id = _agent_context(x_nexus_agent_id, authorization)
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
    settings, repository, agent_id = _agent_context(x_nexus_agent_id, authorization)
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
    settings, repository, agent_id = _agent_context(x_nexus_agent_id, authorization)
    status = _lease_error(lambda: repository.complete(
        job_id=body.job_id,
        agent_id=agent_id,
        lease_token=body.lease_token,
        succeeded=body.succeeded,
        result=body.result,
        error_message=body.error_message,
    ))
    return {"status": status}


@router.get("/admin/local-agent")
def admin_overview(request: Request, limit: int = Query(50, ge=1, le=200)):
    settings, repository = _admin(request)
    csrf_token = request.session.setdefault("local_agent_csrf", secrets.token_urlsafe(32))
    return {
        "enabled": settings.enabled,
        "allowed_tasks": sorted(settings.allowed_tasks),
        "jobs": repository.list_jobs(limit=limit),
        "agents": repository.list_agents(),
        "csrf_token": csrf_token,
    }


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
