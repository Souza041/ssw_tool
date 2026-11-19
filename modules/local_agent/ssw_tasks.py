"""Contratos e handlers Windows permitidos para automações dependentes do SSW."""

from __future__ import annotations

from datetime import date
import os
from typing import Any

from modules.local_agent.executor import ExecutionContext, HandlerRegistry


TASK_DOCUMENTOS = "ssw.documentos_gce"
TASK_METRICAS = "ssw.metricas_op455"
TASK_OC73 = "ssw.ocorrencia_73"
TASK_OC73_RECHECK = "ssw.ocorrencia_73.recheck_big"


def _exact(payload: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Payload contém campos não permitidos.")


def _iso_date(value: object, label: str, *, optional: bool = True) -> str | None:
    if value in (None, "") and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} inválida.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} inválida.") from None
    if not 2020 <= parsed.year <= 2099:
        raise ValueError(f"{label} fora do intervalo permitido.")
    return parsed.isoformat()


def validate_ssw_payload(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task == TASK_OC73:
        _exact(payload, {"flow", "reference_date"})
        flow = str(payload.get("flow") or "").strip().upper()
        if flow not in {"CWB", "BIG"}:
            raise ValueError("Fluxo OC73 inválido.")
        return {
            "flow": flow,
            "reference_date": _iso_date(payload.get("reference_date"), "Data de referência", optional=False),
        }
    if task == TASK_OC73_RECHECK:
        _exact(payload, {"scheduled_at"})
        scheduled_at = str(payload.get("scheduled_at") or "")
        if not 10 <= len(scheduled_at) <= 40:
            raise ValueError("Instante da rechecagem inválido.")
        return {"scheduled_at": scheduled_at}
    if task == TASK_METRICAS:
        _exact(payload, {"days", "origin"})
        days = payload.get("days", 30)
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("Período de métricas inválido.")
        origin = str(payload.get("origin") or "manual").strip().lower()
        if origin not in {"manual", "scheduler"}:
            raise ValueError("Origem de métricas inválida.")
        return {"days": days, "origin": origin}
    if task == TASK_DOCUMENTOS:
        _exact(payload, {
            "origin", "reference_date", "period_start", "period_end",
            "send_notifications", "preview_notifications", "notification_limit",
        })
        origin = str(payload.get("origin") or "SCHEDULED").strip().upper()
        if origin not in {"MANUAL", "SCHEDULED"}:
            raise ValueError("Origem de Documentos GCE inválida.")
        start = _iso_date(payload.get("period_start"), "Data inicial")
        end = _iso_date(payload.get("period_end"), "Data final")
        if (start is None) != (end is None) or (start and end and start > end):
            raise ValueError("Período de Documentos GCE inválido.")
        send = payload.get("send_notifications", False)
        preview = payload.get("preview_notifications", False)
        if type(send) is not bool or type(preview) is not bool or (send and preview):
            raise ValueError("Modo de notificação inválido.")
        limit = payload.get("notification_limit")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
            raise ValueError("Limite de notificações inválido.")
        return {
            "origin": origin,
            "reference_date": _iso_date(payload.get("reference_date"), "Data de referência"),
            "period_start": start,
            "period_end": end,
            "send_notifications": send,
            "preview_notifications": preview,
            "notification_limit": limit,
        }
    raise ValueError("Tarefa SSW não reconhecida.")


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _summary(result: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: result.get(key) for key in keys if key in result}


def _documents(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    clean = validate_ssw_payload(TASK_DOCUMENTOS, payload)
    from modules.documentos.execution import execute_daily_documents

    def progress(message: str) -> None:
        context.raise_if_cancelled()
        context.log(str(message)[:4000])
        if str(message).startswith("[") and "/6]" in str(message):
            try:
                current = int(str(message)[1])
                context.set_progress(max(0, current - 1), 6)
            except ValueError:
                pass

    context.set_progress(0, 6)
    result = execute_daily_documents(
        triggered_by=clean["origin"],
        progress=progress,
        reference_date=_date(clean["reference_date"]),
        period_start=_date(clean["period_start"]),
        period_end=_date(clean["period_end"]),
        send_notifications=clean["send_notifications"],
        preview_notifications=clean["preview_notifications"],
        notification_limit=clean["notification_limit"],
    )
    context.set_progress(6, 6)
    matching = result.get("matching") or {}
    return {
        "success": result.get("success") is True,
        "pipeline_run_id": result.get("pipeline_run_id"),
        "reference_date": result.get("reference_date"),
        "period": result.get("period"),
        "matching": _summary(matching, ("total", "matched", "ambiguous", "not_found", "errors")),
    }


def _oc73(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    clean = validate_ssw_payload(TASK_OC73, payload)
    from modules.ocorrencia_73.scheduler import executar_ocorrencia_73_local

    context.set_progress(0, 1)
    result = executar_ocorrencia_73_local(
        fluxo=clean["flow"], reference_date=_date(clean["reference_date"]),
        raise_on_error=True,
    )
    context.raise_if_cancelled()
    context.set_progress(1, 1)
    if result is None:
        return {"success": True, "skipped": True, "flow": clean["flow"]}
    allowed = (
        "success", "dry_run", "total_relatorio", "total_filtrado", "total_consultado",
        "total_encontrado_op101", "total_nao_encontrado_op101", "total_lancado",
        "total_erro_tecnico", "total_bloqueado",
    )
    return {"flow": clean["flow"], **_summary(result, allowed)}


def _oc73_recheck(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    validate_ssw_payload(TASK_OC73_RECHECK, payload)
    from modules.ocorrencia_73.scheduler import executar_rechecagens_big_local

    context.set_progress(0, 1)
    results = executar_rechecagens_big_local(raise_on_error=True)
    context.raise_if_cancelled()
    context.set_progress(1, 1)
    return {"success": True, "processed": len(results or [])}


def _metrics(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    clean = validate_ssw_payload(TASK_METRICAS, payload)
    if os.getenv("METRICAS_SNAPSHOT_STORAGE", "").strip().lower() != "database":
        raise RuntimeError("Métricas remotas exigem snapshot central no banco.")
    from modules.metricas.service import MetricasService
    from modules.metricas.health import BOT, classify_result
    from modules.health_center.monitor import run_monitored

    context.set_progress(0, 1)
    result = run_monitored(
        bot=BOT,
        origin=f"agent_{clean['origin']}",
        operation=lambda: MetricasService().atualizar_op455(
            triggered_by=f"local_agent:{clean['origin']}",
            triggered_user_id=None,
            dias=clean["days"],
        ),
        classify=classify_result,
    )
    context.raise_if_cancelled()
    context.set_progress(1, 1)
    if result.get("success") is not True:
        raise RuntimeError("Atualização de métricas retornou falha.")
    return _summary(result, ("success", "run_id", "file", "total"))


def register_ssw_handlers(registry: HandlerRegistry) -> None:
    enabled = os.getenv(
        "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false"
    ).strip().casefold() in {"1", "true", "yes", "sim", "on"}
    if not enabled:
        return
    registry.register(TASK_DOCUMENTOS, _documents)
    registry.register(TASK_METRICAS, _metrics)
    registry.register(TASK_OC73, _oc73)
    registry.register(TASK_OC73_RECHECK, _oc73_recheck)


def validate_ssw_runtime_environment() -> None:
    """Valida somente configuração local; não abre banco, portal ou SSW."""
    required = (
        "DOCUMENTOS_SSW_DOMINIO", "DOCUMENTOS_SSW_CPF",
        "DOCUMENTOS_SSW_USUARIO", "DOCUMENTOS_SSW_SENHA",
        "GCE_LOGIN", "GCE_PASSWORD",
        "DOCUMENTOS_DB_HOST", "DOCUMENTOS_DB_USER",
        "DOCUMENTOS_DB_PASSWORD", "DOCUMENTOS_DB_NAME",
        "METRICAS_SSW_DOMINIO", "METRICAS_SSW_CPF",
        "METRICAS_SSW_USUARIO", "METRICAS_SSW_SENHA",
        "METRICAS_DB_HOST", "METRICAS_DB_USER",
        "METRICAS_DB_PASSWORD", "METRICAS_DB_NAME",
        "OCORRENCIA_73_SSW_DOMINIO", "OCORRENCIA_73_SSW_CPF",
        "OCORRENCIA_73_SSW_USUARIO", "OCORRENCIA_73_SSW_SENHA",
        "AUTOMATION_DB_HOST", "AUTOMATION_DB_USER",
        "AUTOMATION_DB_PASSWORD", "AUTOMATION_DB_NAME",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Configuração SSW Windows incompleta: " + ", ".join(missing))
    if os.getenv("METRICAS_SNAPSHOT_STORAGE", "").strip().lower() != "database":
        raise RuntimeError("METRICAS_SNAPSHOT_STORAGE deve ser database no Windows.")
    for port_name in ("AUTOMATION_DB_PORT", "DOCUMENTOS_DB_PORT", "METRICAS_DB_PORT"):
        try:
            port = int(os.getenv(port_name, "3306"))
        except ValueError:
            raise RuntimeError(f"{port_name} inválida.") from None
        if not 1 <= port <= 65535:
            raise RuntimeError(f"{port_name} inválida.")
