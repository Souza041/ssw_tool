"""Fila VPS -> Windows para tarefas SSW; opt-in e sem chamadas de rede ao SSW."""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from modules.local_agent.config import load_server_settings
from modules.local_agent.repository import LocalAgentRepository
from modules.local_agent.ssw_tasks import validate_ssw_payload


SSW_TASKS = frozenset({
    "ssw.documentos_gce",
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
    if target_agent_id is not None and task not in settings.agent_tasks.get(target_agent_id, ()):
        raise RuntimeError("Tarefa SSW fora da allowlist do agente de destino.")
    repository = LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
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
    )


def scheduled_key(task: str, *parts: object) -> str:
    normalized = [task, *(str(part).strip().lower() for part in parts)]
    return ":".join(normalized)


def current_minute_key(task: str, moment: datetime) -> str:
    return scheduled_key(task, moment.strftime("%Y%m%d%H%M"))
