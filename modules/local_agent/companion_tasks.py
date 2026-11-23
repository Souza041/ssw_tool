"""Handlers exclusivos do agente Companion interativo."""

from __future__ import annotations

import os
import re
from typing import Any

from modules.local_agent.executor import ExecutionContext, HandlerRegistry


TASK_AUTH = "ssw.companion.authenticate"
TASK_METRICS = "ssw.companion.metricas_op455"
TASKS = frozenset({TASK_AUTH, TASK_METRICS})


def _identity(payload: dict[str, Any]) -> tuple[str, str]:
    usuario = str(payload.get("usuario") or "").strip()
    unidade = str(payload.get("unidade") or "").strip().upper()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", usuario):
        raise ValueError("Usuário esperado inválido.")
    if not re.fullmatch(r"[A-Z0-9]{3,16}", unidade):
        raise ValueError("Unidade esperada inválida.")
    return usuario, unidade


def validate_companion_payload(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido.")
    if task == TASK_AUTH:
        if set(payload) != {"dominio", "usuario", "unidade", "request_nonce"}:
            raise ValueError("Payload de autenticação assistida inválido.")
        usuario, unidade = _identity(payload)
        dominio = str(payload.get("dominio") or "").strip().upper()
        nonce = str(payload.get("request_nonce") or "")
        if not re.fullmatch(r"[A-Z0-9_-]{2,16}", dominio):
            raise ValueError("Domínio inválido.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", nonce):
            raise ValueError("Vínculo de autenticação inválido.")
        return {"dominio": dominio, "usuario": usuario, "unidade": unidade, "request_nonce": nonce}
    if task == TASK_METRICS:
        if set(payload) != {"usuario", "unidade", "days", "mode"}:
            raise ValueError("Payload OP455 assistido inválido.")
        usuario, unidade = _identity(payload)
        days = payload.get("days")
        mode = str(payload.get("mode") or "").strip().lower()
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError("Período OP455 assistido inválido.")
        if mode not in {"validate_only", "production"}:
            raise ValueError("Modo OP455 assistido inválido.")
        return {"usuario": usuario, "unidade": unidade, "days": days, "mode": mode}
    raise ValueError("Tarefa Companion desconhecida.")


def register_companion_handlers(registry: HandlerRegistry, session) -> None:
    def authenticate(payload: dict, context: ExecutionContext) -> dict:
        clean = validate_companion_payload(TASK_AUTH, payload)
        context.set_progress(0, 1)
        identity = session.authenticate(
            usuario=clean["usuario"], unidade=clean["unidade"],
            timeout_seconds=int(os.getenv("NEXUS_SSW_COMPANION_AUTH_TIMEOUT", "300")),
            cancel=context.raise_if_cancelled,
            dominio=clean["dominio"],
        )
        context.set_progress(1, 1)
        return {
            "authenticated": True,
            "dominio": clean["dominio"],
            "usuario": identity.usuario,
            "unidade": identity.unidade,
            "request_nonce": clean["request_nonce"],
        }

    def metrics(payload: dict, context: ExecutionContext) -> dict:
        clean = validate_companion_payload(TASK_METRICS, payload)
        session.authenticate(
            usuario=clean["usuario"], unidade=clean["unidade"],
            timeout_seconds=int(os.getenv("NEXUS_SSW_COMPANION_AUTH_TIMEOUT", "300")),
            cancel=context.raise_if_cancelled,
        )
        from modules.metricas.service import MetricasService
        service = MetricasService(client_factory=lambda: session.client(
            usuario=clean["usuario"], unidade=clean["unidade"],
            cancel=context.raise_if_cancelled,
        ))
        context.set_progress(0, 1)
        if clean["mode"] == "production":
            if os.getenv("NEXUS_SSW_COMPANION_ALLOW_PRODUCTION", "false").strip().lower() != "true":
                raise RuntimeError("Execução produtiva do Companion permanece bloqueada.")
            result = service.atualizar_op455(
                triggered_by="companion_manual", triggered_user_id=clean["usuario"],
                dias=clean["days"],
            )
        else:
            result = service.homologar_op455(dias=clean["days"])
        context.raise_if_cancelled()
        context.set_progress(1, 1)
        return result

    registry.register(TASK_AUTH, authenticate)
    registry.register(TASK_METRICS, metrics)

