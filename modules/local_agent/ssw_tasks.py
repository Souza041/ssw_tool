"""Contratos e handlers Windows permitidos para automações dependentes do SSW."""

from __future__ import annotations

from datetime import date
import base64
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import re

from modules.local_agent.executor import ExecutionContext, HandlerRegistry


from modules.local_agent.config import GCE_MAX_REPORT_BYTES, GCE_PIPELINE_TASK, GCE_QUERY_TASK

TASK_DOCUMENTOS = GCE_PIPELINE_TASK
TASK_METRICAS = "ssw.metricas_op455"
TASK_OC73 = "ssw.ocorrencia_73"
TASK_OC73_RECHECK = "ssw.ocorrencia_73.recheck_big"
TASK_OP001_NFD = "ssw.op001.nfd"
TASK_OP001_TRANSPORTE = "ssw.op001.transporte"
TASK_SESSION_CLOSE = "ssw.session.close"


_IDENTITY_FIELDS = {"dominio", "usuario", "unidade", "session_binding"}


def _identity(payload: dict[str, Any]) -> dict[str, str]:
    dominio = str(payload.get("dominio") or "").strip().upper()
    usuario = str(payload.get("usuario") or "").strip()
    unidade = str(payload.get("unidade") or "").strip().upper()
    binding = str(payload.get("session_binding") or "").strip()
    if not re.fullmatch(r"[A-Z0-9_-]{1,32}", dominio):
        raise ValueError("Domínio SSW inválido.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", usuario):
        raise ValueError("Usuário SSW inválido.")
    if not re.fullmatch(r"[A-Z0-9]{3,16}", unidade):
        raise ValueError("Unidade SSW inválida.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", binding):
        raise ValueError("Vínculo de sessão SSW inválido.")
    return {"dominio": dominio, "usuario": usuario, "unidade": unidade, "session_binding": binding}


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
        _exact(payload, {"days", "origin", "mode", "correlation_id"})
        days = payload.get("days", 30)
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("Período de métricas inválido.")
        origin = str(payload.get("origin") or "manual").strip().lower()
        if origin not in {"manual", "scheduler", "homologation"}:
            raise ValueError("Origem de métricas inválida.")
        mode = str(payload.get("mode") or "production").strip().lower()
        if mode not in {"production", "validate_only"}:
            raise ValueError("Modo de métricas inválido.")
        correlation_id = str(payload.get("correlation_id") or "").strip()
        if mode == "validate_only":
            if origin != "homologation" or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}", correlation_id
            ):
                raise ValueError("Correlação de homologação inválida.")
            if days > 30:
                raise ValueError("Homologação OP455 limitada a 30 dias.")
        elif correlation_id:
            raise ValueError("Correlação só é aceita na homologação.")
        return {
            "days": days, "origin": origin, "mode": mode,
            "correlation_id": correlation_id or None,
        }
    if task == GCE_QUERY_TASK:
        _exact(payload, {"period_start", "period_end", "cnpj", "group", "timeout", *_IDENTITY_FIELDS})
        start = _iso_date(payload.get("period_start"), "Data inicial", optional=False)
        end = _iso_date(payload.get("period_end"), "Data final", optional=False)
        cnpj, group, timeout = (payload.get(key) for key in ("cnpj", "group", "timeout"))
        if start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > 366:
            raise ValueError("Período da consulta GCE inválido.")
        if (not isinstance(cnpj, str) or not re.fullmatch(r"[0-9]{14}", cnpj)
                or not isinstance(group, str) or not re.fullmatch(r"[\w .-]{1,64}", group)
                or type(timeout) is not int or not 1 <= timeout <= 1800):
            raise ValueError("Parâmetros da consulta GCE inválidos.")
        return {
            **_identity(payload), "period_start": start, "period_end": end,
            "cnpj": cnpj, "group": group, "timeout": timeout,
        }
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
        result = {
            "origin": origin,
            "reference_date": _iso_date(payload.get("reference_date"), "Data de referência"),
            "period_start": start,
            "period_end": end,
            "send_notifications": send,
            "preview_notifications": preview,
            "notification_limit": limit,
        }
        return result
    if task in {TASK_OP001_NFD, TASK_OP001_TRANSPORTE}:
        _exact(payload, {"filename", "workbook_base64", *_IDENTITY_FIELDS})
        filename = Path(str(payload.get("filename") or "")).name
        if not filename or not filename.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError("Arquivo OP001 inválido.")
        encoded = str(payload.get("workbook_base64") or "")
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception:
            raise ValueError("Conteúdo OP001 inválido.") from None
        maximum = int(os.getenv("NEXUS_OP001_MAX_UPLOAD_BYTES", "8388608"))
        if not content or len(content) > maximum:
            raise ValueError("Arquivo OP001 vazio ou acima do limite permitido.")
        return {"filename": filename, "workbook_base64": encoded, **_identity(payload)}
    if task == TASK_SESSION_CLOSE:
        _exact(payload, _IDENTITY_FIELDS)
        return _identity(payload)
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


def _collect_gce_ssw(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    from dataclasses import replace
    from modules.documentos.collectors.ssw import SSWDocumentsCollector
    from modules.documentos.config import settings
    from ssw.browser_transport import require_browser_session
    from ssw.client import SSWClient

    clean = validate_ssw_payload(GCE_QUERY_TASK, payload)
    transport = require_browser_session(
        domain=clean["dominio"], user=clean["usuario"],
        session_binding=clean["session_binding"],
    )
    lease = transport.acquire_operation("Documentos GCE")
    try:
        context.raise_if_cancelled()
        client = SSWClient(
            dominio=clean["dominio"], usuario=clean["usuario"], unidade=clean["unidade"],
            auth_mode="browser", session_binding=clean["session_binding"],
        )
        client.session.close()  # Descarta apenas o requests.Session recém-criado.
        client.session = transport
        client.authenticated_unit = transport.observed_unit
        config = replace(settings, op930_cnpj=clean["cnpj"], op930_group=clean["group"],
                         ssw_report_timeout=clean["timeout"])

        def progress(message: str) -> None:
            context.raise_if_cancelled()
            context.log(message)

        context.set_progress(0, 2)
        with TemporaryDirectory(prefix="nexus-gce-") as temporary:
            root = Path(temporary).resolve()
            reports = SSWDocumentsCollector(config, client_factory=lambda: client, progress=progress).download(
                root, _date(clean["period_start"]), _date(clean["period_end"]),
            )
            diagnostic = logging.getLogger(f"{__name__}.gce")
            for position, (kind, path) in enumerate((("op455", reports.op455), ("op930", reports.op930)), 1):
                stage = "validar_caminho"
                inside_root = exists = regular = size = bytes_read = None
                error = None
                try:
                    inside_root = path.resolve().is_relative_to(root)
                    if inside_root:
                        regular = path.is_file()
                        exists = regular or path.exists()
                    if not inside_root or not regular:
                        raise ValueError("Caminho de relatório GCE inválido.")
                    stage = "ler_arquivo"
                    with path.open("rb") as report:
                        size = os.fstat(report.fileno()).st_size
                        content = report.read(GCE_MAX_REPORT_BYTES + 1)
                    bytes_read = len(content)
                    if bytes_read == 0:
                        stage = "arquivo_vazio"
                    elif bytes_read > GCE_MAX_REPORT_BYTES:
                        stage = "acima_do_limite"
                    else:
                        stage = "transferir_https"
                    context.upload_gce_report(kind, content)
                    stage = "https_confirmado"
                except Exception as exc:
                    error = type(exc).__name__
                    raise
                finally:
                    diagnostic.log(
                        logging.WARNING if error else logging.INFO,
                        "GCE manual relatorio=%s etapa=%s dentro_da_raiz=%s existe=%s "
                        "arquivo_regular=%s tamanho_bytes=%s bytes_lidos=%s limite_bytes=%s erro=%s",
                        kind, stage, inside_root, exists, regular, size, bytes_read,
                        GCE_MAX_REPORT_BYTES, error,
                    )
                context.set_progress(position, 2)
    finally:
        lease.release()
    return {"success": True}


def _authenticated_client(clean: dict[str, Any]):
    from ssw.client import SSWClient

    client = SSWClient(
        dominio=clean["dominio"], usuario=clean["usuario"], unidade=clean["unidade"],
        auth_mode="browser", session_binding=clean["session_binding"],
    )
    client.login()
    client.open_menu()
    return client


class _RemoteJobProgress:
    def __init__(self, context: ExecutionContext) -> None:
        self.context = context
        self._progress = 0
        self._total = 0

        class _Logs:
            def put(_self, message: str) -> None:
                context.raise_if_cancelled()
                context.log(str(message)[:4000])

        self.logs = _Logs()

    @property
    def progress(self) -> int:
        return self._progress

    @progress.setter
    def progress(self, value: int) -> None:
        self._progress = int(value)
        self._sync_progress()

    @property
    def total(self) -> int:
        return self._total

    @total.setter
    def total(self, value: int) -> None:
        self._total = int(value)
        self._sync_progress()

    def _sync_progress(self) -> None:
        if 0 <= self._progress <= self._total:
            self.context.set_progress(
                self._progress,
                self._total,
            )


def _op001(payload: dict[str, Any], context: ExecutionContext, *, task: str) -> dict[str, Any]:
    clean = validate_ssw_payload(task, payload)
    content = base64.b64decode(clean["workbook_base64"], validate=True)
    client = _authenticated_client(clean)
    lease = client.acquire_operation("OP001 · Cadastro de Coletas")
    try:
        from operations.op001.coleta import OP001Coleta
        from operations.op001.batch import processar_planilha_nfd
        from operations.op001.batch_transporte import processar_planilha_transporte

        with TemporaryDirectory(prefix="nexus-op001-") as temporary:
            root = Path(temporary)
            input_file = root / clean["filename"]
            output_file = root / f"processado_{clean['filename']}"
            input_file.write_bytes(content)
            op001 = OP001Coleta(client)
            op001.open(unidade=clean["unidade"])
            job = _RemoteJobProgress(context)
            if task == TASK_OP001_NFD:
                result_file = processar_planilha_nfd(
                    op001=op001, input_file=input_file, output_file=output_file, job=job,
                )
            else:
                result_file = processar_planilha_transporte(
                    op001=op001, input_file=input_file, output_file=output_file, job=job,
                )
            artifact = Path(result_file).read_bytes()
            return {
                "success": True,
                "filename": Path(result_file).name,
                "workbook_base64": base64.b64encode(artifact).decode("ascii"),
            }
    finally:
        if lease is not None:
            lease.release()


def _close_session(payload: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    clean = validate_ssw_payload(TASK_SESSION_CLOSE, payload)
    from ssw.browser_transport import discard_browser_session

    context.set_progress(0, 1)
    closed = discard_browser_session(
        domain=clean["dominio"], user=clean["usuario"],
        session_binding=clean["session_binding"],
    )
    context.set_progress(1, 1)
    return {"success": True, "closed": closed}


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
    from modules.metricas.service import MetricasService
    if clean["mode"] == "validate_only":
        context.set_progress(0, 3)
        context.log(
            f"Homologação OP455 iniciada; correlação={clean['correlation_id']}; sem escrita MySQL."
        )
        context.set_progress(1, 3)
        result = MetricasService().homologar_op455(dias=clean["days"])
        context.raise_if_cancelled()
        context.set_progress(3, 3)
        context.log("Homologação OP455 concluída; arquivo temporário removido.")
        return {**result, "correlation_id": clean["correlation_id"]}

    if os.getenv("METRICAS_SNAPSHOT_STORAGE", "").strip().lower() != "database":
        raise RuntimeError("Métricas remotas exigem snapshot central no banco.")
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


def selected_ssw_tasks() -> frozenset[str]:
    raw = os.getenv("NEXUS_LOCAL_AGENT_SSW_TASKS", "")
    selected = frozenset(item.strip() for item in raw.split(",") if item.strip())
    known = {TASK_DOCUMENTOS, GCE_QUERY_TASK, TASK_METRICAS, TASK_OP001_NFD, TASK_OP001_TRANSPORTE, TASK_SESSION_CLOSE}
    unknown = selected - known
    if unknown:
        raise RuntimeError("Allowlist de handlers SSW contém tarefa desconhecida.")
    return selected


def register_ssw_handlers(registry: HandlerRegistry, *, supported_tasks: frozenset[str] | None = None) -> None:
    enabled = os.getenv(
        "NEXUS_LOCAL_AGENT_SSW_HANDLERS_ENABLED", "false"
    ).strip().casefold() in {"1", "true", "yes", "sim", "on"}
    if supported_tasks is None and not enabled:
        return
    handlers = {
        TASK_DOCUMENTOS: _documents,
        GCE_QUERY_TASK: _collect_gce_ssw,
        TASK_METRICAS: _metrics,
        TASK_OP001_NFD: lambda payload, context: _op001(payload, context, task=TASK_OP001_NFD),
        TASK_OP001_TRANSPORTE: lambda payload, context: _op001(payload, context, task=TASK_OP001_TRANSPORTE),
        TASK_SESSION_CLOSE: _close_session,
    }
    tasks = selected_ssw_tasks() if supported_tasks is None else supported_tasks
    for task in sorted(tasks):
        registry.register(task, handlers[task])


def validate_ssw_runtime_environment(tasks: frozenset[str] | None = None) -> None:
    """Valida somente configuração local; não abre banco, portal ou SSW."""
    selected = tasks if tasks is not None else selected_ssw_tasks()
    required: set[str] = set()
    if TASK_DOCUMENTOS in selected:
        required.update((
        "GCE_LOGIN", "GCE_PASSWORD",
        "DOCUMENTOS_DB_HOST", "DOCUMENTOS_DB_USER",
        "DOCUMENTOS_DB_PASSWORD", "DOCUMENTOS_DB_NAME",
        ))
    if TASK_METRICAS in selected:
        required.update((
        "METRICAS_SSW_DOMINIO", "METRICAS_SSW_CPF",
        "METRICAS_SSW_USUARIO", "METRICAS_SSW_SENHA",
        "METRICAS_DB_HOST", "METRICAS_DB_USER",
        "METRICAS_DB_PASSWORD", "METRICAS_DB_NAME",
        ))
    if selected & {TASK_OC73, TASK_OC73_RECHECK}:
        required.update((
        "OCORRENCIA_73_SSW_DOMINIO", "OCORRENCIA_73_SSW_CPF",
        "OCORRENCIA_73_SSW_USUARIO", "OCORRENCIA_73_SSW_SENHA",
        "AUTOMATION_DB_HOST", "AUTOMATION_DB_USER",
        "AUTOMATION_DB_PASSWORD", "AUTOMATION_DB_NAME",
        ))
    if os.getenv("HEALTH_CENTER_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }:
        required.update((
            "AUTOMATION_DB_HOST", "AUTOMATION_DB_USER",
            "AUTOMATION_DB_PASSWORD", "AUTOMATION_DB_NAME",
        ))
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Configuração SSW Windows incompleta: " + ", ".join(missing))
    if TASK_METRICAS in selected and os.getenv(
        "METRICAS_SNAPSHOT_STORAGE", ""
    ).strip().lower() != "database":
        raise RuntimeError("METRICAS_SNAPSHOT_STORAGE deve ser database no Windows.")
    port_names = set()
    if TASK_DOCUMENTOS in selected:
        port_names.add("DOCUMENTOS_DB_PORT")
    if TASK_METRICAS in selected:
        port_names.add("METRICAS_DB_PORT")
    if selected & {TASK_OC73, TASK_OC73_RECHECK} or os.getenv(
        "HEALTH_CENTER_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "sim", "on"}:
        port_names.add("AUTOMATION_DB_PORT")
    for port_name in port_names:
        try:
            port = int(os.getenv(port_name, "3306"))
        except ValueError:
            raise RuntimeError(f"{port_name} inválida.") from None
        if not 1 <= port <= 65535:
            raise RuntimeError(f"{port_name} inválida.")
