"""Fila VPS -> Windows para tarefas SSW; opt-in e sem chamadas de rede ao SSW."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from modules.local_agent.config import (
    GCE_PIPELINE_TASK, GCE_QUERY_TASK, INDIVIDUAL_TASKS, individual_task_grants, load_server_settings,
    remote_auth_agent_for,
)
from modules.local_agent.repository import LocalAgentRepository
from modules.local_agent.ssw_tasks import validate_ssw_payload


SSW_TASKS = frozenset({
    GCE_PIPELINE_TASK,
    GCE_QUERY_TASK,
    "ssw.metricas_op455",
    "ssw.op001.nfd",
    "ssw.op001.transporte",
    "ssw.session.close",
})


def delegated_tasks() -> frozenset[str]:
    raw = os.getenv("NEXUS_SSW_DELEGATED_TASKS", "")
    tasks = frozenset(item.strip() for item in raw.split(",") if item.strip())
    unsupported = tasks - SSW_TASKS
    if unsupported:
        raise RuntimeError("Allowlist de delegação contém tarefa não autorizada nesta fase.")
    return tasks


def delegation_enabled(task: str | None = None) -> bool:
    global_enabled = os.getenv(
        "NEXUS_SSW_DELEGATION_ENABLED", "false"
    ).strip().casefold() in {
        "1", "true", "yes", "sim", "on",
    }
    if not global_enabled or task is None:
        return global_enabled
    return task in delegated_tasks()


def individual_permission_ceiling(settings) -> frozenset[str]:
    """As allowlists são tetos de concessão para agentes individuais."""
    if not settings.pairing_enabled:
        return frozenset()
    available = (delegated_tasks() if delegation_enabled() else frozenset()) | {"system.ping"}
    return INDIVIDUAL_TASKS & settings.allowed_tasks & settings.paired_tasks & available


def initial_individual_grants(settings) -> frozenset[str]:
    return (INDIVIDUAL_TASKS & settings.allowed_tasks & settings.paired_tasks) - {GCE_QUERY_TASK}


def gce_central_agent() -> str:
    """GCE usa exclusivamente o agente estático com ambiente privado."""
    settings = load_server_settings()
    agent_id = settings.remote_auth_agent_id
    if (not agent_id or agent_id not in settings.tokens
            or "ssw.documentos_gce" not in settings.agent_tasks.get(agent_id, ())):
        raise RuntimeError("Agente central do Documentos GCE não configurado ou autorizado.")
    return agent_id


def enqueue_ssw_task(
    task: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None,
    concurrency_key: str,
    priority: int = 100,
    coalesce_active: bool = False,
    target_agent_id: str | None = None,
) -> str:
    if task not in SSW_TASKS:
        raise ValueError("Tarefa SSW não reconhecida para delegação.")
    if not delegation_enabled(task):
        raise RuntimeError("Delegação da tarefa SSW está desativada ou não autorizada.")
    clean_payload = validate_ssw_payload(task, payload)
    settings = load_server_settings()
    if not settings.enabled:
        raise RuntimeError("Nexus Local Agent está desativado.")
    if task not in settings.allowed_tasks:
        raise RuntimeError("Tarefa SSW fora da allowlist da VPS.")
    if task == GCE_PIPELINE_TASK and target_agent_id != gce_central_agent():
        raise RuntimeError("Pipeline GCE completo restrito ao agente central.")
    if task == GCE_QUERY_TASK and not target_agent_id:
        raise RuntimeError("Consulta GCE exige agente vinculado à sessão.")
    if task == GCE_QUERY_TASK and target_agent_id == settings.remote_auth_agent_id:
        raise RuntimeError("O GCE manual exige uma sessão em agente individual.")
    repository = LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
    )
    if task == GCE_QUERY_TASK:
        identity = {key: clean_payload[key] for key in ("dominio", "usuario")}
        bound_agent = repository.bound_agent(**identity)
        if bound_agent is None and target_agent_id in settings.tokens:
            try:
                bound_agent = remote_auth_agent_for(settings, **identity)
            except LookupError:
                pass
        if bound_agent != target_agent_id:
            raise RuntimeError("Agente da consulta GCE não está vinculado ao usuário SSW.")

    paired_ceiling = None
    if target_agent_id is not None:
        if target_agent_id in settings.tokens:
            authorized_tasks = settings.agent_tasks.get(
                target_agent_id, frozenset()
            )
        elif settings.pairing_enabled:
            paired_ceiling = individual_permission_ceiling(settings)
            dynamic_tasks = repository.dynamic_agent_tasks(
                agent_id=target_agent_id
            )

            authorized_tasks = (
                individual_task_grants(frozenset(dynamic_tasks or ()))
                & paired_ceiling
            )
        else:
            authorized_tasks = frozenset()

        if task not in authorized_tasks:
            raise RuntimeError(
                "Tarefa SSW fora da allowlist do agente de destino."
            )
    return repository.enqueue(
        task=task,
        payload=clean_payload,
        priority=priority,
        # Tarefas com efeitos operacionais nunca são repetidas automaticamente
        # após perda de lease. A recuperação exige nova decisão do scheduler.
        max_attempts=1,
        idempotency_key=idempotency_key,
        concurrency_key=concurrency_key,
        coalesce_active=coalesce_active,
        target_agent_id=target_agent_id,
        paired_ceiling=paired_ceiling,
    )


def scheduled_key(task: str, *parts: object) -> str:
    normalized = [task, *(str(part).strip().lower() for part in parts)]
    return ":".join(normalized)


def current_minute_key(task: str, moment: datetime) -> str:
    return scheduled_key(task, moment.strftime("%Y%m%d%H%M"))
