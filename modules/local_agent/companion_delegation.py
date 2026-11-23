"""Enfileiramento VPS para o agente Companion, opt-in e manual."""

from __future__ import annotations

import os

from modules.local_agent.companion_tasks import (
    TASK_AUTH, TASK_METRICS, validate_companion_payload,
)
from modules.local_agent.config import load_server_settings
from modules.local_agent.repository import LocalAgentRepository


def companion_enabled() -> bool:
    return os.getenv("NEXUS_SSW_COMPANION_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }


def companion_agent_id() -> str:
    return os.getenv("NEXUS_SSW_COMPANION_AGENT_ID", "").strip()


def enqueue_companion(task: str, payload: dict, *, concurrency_key: str) -> str:
    if not companion_enabled():
        raise RuntimeError("Nexus SSW Companion está desativado.")
    clean = validate_companion_payload(task, payload)
    settings = load_server_settings()
    agent_id = companion_agent_id()
    if (
        not settings.enabled
        or task not in settings.allowed_tasks
        or agent_id not in settings.agent_tasks
        or task not in settings.agent_tasks[agent_id]
    ):
        raise RuntimeError("Companion não está autorizado na configuração da VPS.")
    repository = LocalAgentRepository(
        settings.database_path,
        max_payload_bytes=settings.max_payload_bytes,
        max_result_bytes=settings.max_result_bytes,
        max_log_bytes=settings.max_log_bytes,
    )
    return repository.enqueue(
        task=task, payload=clean, priority=50, max_attempts=1,
        idempotency_key=None, concurrency_key=concurrency_key,
        coalesce_active=False,
    )


__all__ = [
    "TASK_AUTH", "TASK_METRICS", "companion_enabled", "companion_agent_id",
    "enqueue_companion",
]

