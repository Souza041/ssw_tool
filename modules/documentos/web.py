from __future__ import annotations

import re
import secrets
import threading

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from modules.documentos.services.dashboard import (
    dashboard_snapshot,
    export_documents,
)
from modules.documentos.execution import execute_daily_documents
from modules.documentos.services.daily_pipeline import acquire_execution_lock
from web.admin_access import is_admin
from web.jobs import JOBS, add_log, criar_job, executar_job, set_progress
from web.session_store import obter_client

from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")
_manual_job_guard = threading.Lock()
_latest_manual_job_id: str | None = None
_manual_job_ids: set[str] = set()
_manual_remote_job_ids: set[str] = set()


def _require_documents_admin(request: Request) -> None:
    if not request.session.get("ssw_session_id") or obter_client(request) is None:
        raise HTTPException(status_code=401, detail="Sessão expirada. Entre novamente.")
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="Execução restrita a administradores.")


def _job_logs(job) -> list[str]:
    with job.logs.mutex:
        return list(job.logs.queue)


def _run_manual_job(job, *, execution_lease):
    set_progress(job, 0, 6)

    def progress(message: str) -> None:
        add_log(job, message)
        match = re.match(r"\[(\d)/6\]", message)
        if match:
            set_progress(job, max(job.progress, int(match.group(1)) - 1), 6)

    result = execute_daily_documents(
        triggered_by="MANUAL",
        progress=progress,
        execution_lease=execution_lease,
    )
    set_progress(job, 6, 6)
    return result


@router.get("/documentos", response_class=HTMLResponse)
def documentos_dashboard(
    request: Request,
    page: int = 1,
    per_page: int = 25,
    q: str = "",
    status: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE",
):
    if not request.session.get("ssw_session_id"):
        return RedirectResponse("/login", status_code=303)

    snapshot = dashboard_snapshot(
        page=page,
        per_page=per_page,
        search=q,
        alert_type=status,
        carrier_cnpj=carrier_cnpj,
        pending_status=pending_status,
        lifecycle_status=lifecycle_status,
    )

    csrf_token = request.session.setdefault(
        "documentos_gce_csrf", secrets.token_urlsafe(32)
    )

    return templates.TemplateResponse(
        "documentos_dashboard_v2.html",
        {
            "request": request,
            "dashboard": snapshot,
            "documents_is_admin": is_admin(request),
            "documents_csrf_token": csrf_token,
        },
    )


@router.post("/documentos/executar", status_code=202)
def documentos_executar(
    request: Request,
    csrf_token: str = Header("", alias="X-CSRF-Token"),
):
    """Reserva a trava e entrega o pipeline existente a uma thread daemon."""
    global _latest_manual_job_id
    _require_documents_admin(request)

    expected = str(request.session.get("documentos_gce_csrf") or "")
    if not expected or not csrf_token or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(status_code=403, detail="Token CSRF inválido. Recarregue a página.")

    from modules.local_agent.delegation import delegation_enabled, enqueue_ssw_task
    if delegation_enabled("ssw.documentos_gce"):
        try:
            job_id = enqueue_ssw_task(
                "ssw.documentos_gce",
                {
                    "origin": "MANUAL",
                    "send_notifications": False,
                    "preview_notifications": False,
                },
                idempotency_key=None,
                concurrency_key="ssw.documentos_gce",
                priority=50,
                coalesce_active=True,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        with _manual_job_guard:
            _latest_manual_job_id = job_id
            _manual_remote_job_ids.add(job_id)
        return {
            "accepted": True,
            "job_id": job_id,
            "message": "Execução enviada com segurança ao agente Windows.",
        }

    try:
        execution_lease = acquire_execution_lock()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail="O Documentos GCE já está em execução (manual ou agendada).",
        ) from exc

    try:
        with _manual_job_guard:
            job = criar_job("Documentos GCE · manual")
            _latest_manual_job_id = job.id
            _manual_job_ids.add(job.id)
            executar_job(
                job,
                _run_manual_job,
                execution_lease=execution_lease,
            )
    except BaseException:
        execution_lease.release()
        raise

    return {
        "accepted": True,
        "job_id": job.id,
        "message": "Execução manual iniciada em segundo plano.",
    }


@router.get("/documentos/executar/status")
def documentos_execution_status(request: Request, job_id: str = ""):
    _require_documents_admin(request)
    selected_id = job_id or _latest_manual_job_id or ""
    from modules.local_agent.delegation import delegation_enabled
    if selected_id and (
        selected_id in _manual_remote_job_ids
        or delegation_enabled("ssw.documentos_gce")
    ):
        from modules.local_agent.config import load_server_settings
        from modules.local_agent.repository import LocalAgentRepository

        settings = load_server_settings()
        remote = LocalAgentRepository(
            settings.database_path,
            max_payload_bytes=settings.max_payload_bytes,
            max_result_bytes=settings.max_result_bytes,
            max_log_bytes=settings.max_log_bytes,
        ).get_job(selected_id)
        if remote is not None:
            state = {
                "QUEUED": "running", "LEASED": "running", "SUCCEEDED": "done",
                "FAILED": "error", "CANCELLED": "error",
            }.get(remote["status"], "error")
            total = int(remote.get("progress_total") or 0)
            progress = int(remote.get("progress_current") or 0)
            return {
                "available": True,
                "job_id": selected_id,
                "status": state,
                "progress": progress,
                "total": total,
                "percent": round(progress * 100 / total) if total else 0,
                "logs": [item["message"] for item in remote.get("logs", [])],
                "error": remote.get("error_message"),
                "message": {
                    "running": "Execução aguardando ou em andamento no Windows.",
                    "done": "Execução concluída no Windows. Indicadores atualizados.",
                    "error": "A execução remota falhou ou foi cancelada.",
                }[state],
            }
    job = JOBS.get(selected_id) if selected_id in _manual_job_ids else None
    if job is None:
        return {
            "available": False,
            "status": "idle",
            "message": "Nenhuma execução manual ativa nesta instância.",
        }

    total = int(job.total or 0)
    progress = int(job.progress or 0)
    percent = round(progress * 100 / total) if total else 0
    messages = {
        "running": "Execução em andamento.",
        "done": "Execução concluída com sucesso. Indicadores atualizados.",
        "error": "A execução falhou. Consulte os logs abaixo.",
    }
    return {
        "available": True,
        "job_id": job.id,
        "status": job.status,
        "progress": progress,
        "total": total,
        "percent": percent,
        "logs": _job_logs(job),
        "error": job.error,
        "message": messages.get(job.status, "Status da execução atualizado."),
    }

@router.get("/documentos/export")
def documentos_export(
    request: Request,
    q: str = "",
    status: str = "",
    carrier_cnpj: str = "",
    pending_status: str = "",
    lifecycle_status: str = "ACTIVE"
):
    if not request.session.get("ssw_session_id"):
        return RedirectResponse("/login", status_code=303)

    rows = export_documents(
        search=q,
        alert_type=status,
        carrier_cnpj=carrier_cnpj,
        pending_status=pending_status,
        lifecycle_status=lifecycle_status,
    )

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Acompanhamento"

    headers = [
        "Situação",
        "CNPJ Transportadora",
        "Nota Fiscal",
        "Série NF",
        "CTRC",
        "Destinatário",
        "CNPJ Destinatário",
        "Número Transporte",
        "Data Entrega",
        "Prazo",
        "Dias Restantes",
        "Status Documental",
        "Motivo Pendência",
        "Usuário Pendência",
        "Data Pendência",
        "Quantidade Scans",
        "Último Scan",
        "Unidades Scan",
        "Parceiros",
        "Pacote Arquivo",
        "Capa Remessa",
        "Mapa Expedição",
        "Mapa Recepção",
    ]

    worksheet.append(headers)

    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    status_labels = {
        "VENCIDO": "Vencido",
        "ALERTA_5": "Até 5 dias",
        "ALERTA_10": "Até 10 dias",
        "ALERTA_20": "Até 20 dias",
        "SEM_OC_01": "Sem data de entrega",
        "NORMAL": "Regular",
    }

    def excel_value(value):
        if value is None:
            return ""

        if isinstance(value, (date, datetime)):
            return value

        return str(value)

    for row in rows:
        details = row["details"]

        worksheet.append(
            [
                status_labels.get(
                    row.get("alert_type"),
                    row.get("alert_type") or "",
                ),
                excel_value(details.get("carrier_cnpj")),
                excel_value(details.get("invoice_number")),
                excel_value(details.get("invoice_series")),
                excel_value(details.get("ctrc")),
                excel_value(details.get("recipient_name")),
                excel_value(details.get("recipient_cnpj")),
                excel_value(details.get("transport_number")),
                excel_value(row.get("delivery_date")),
                excel_value(row.get("deadline_date")),
                row.get("days_remaining"),
                (
                    "Recusado / com pendência"
                    if details.get("has_pending")
                    else "Sem pendência"
                ),
                excel_value(details.get("pending_reason")),
                excel_value(details.get("pending_user")),
                excel_value(details.get("pending_at")),
                details.get("scan_count") or 0,
                excel_value(details.get("last_scan_at")),
                excel_value(details.get("scan_units")),
                excel_value(details.get("scan_partners")),
                excel_value(details.get("archive_package_number")),
                excel_value(details.get("remittance_cover_number")),
                excel_value(details.get("expedition_map")),
                excel_value(details.get("reception_map")),
            ]
        )

    # Congela cabeçalho.
    worksheet.freeze_panes = "A2"

    # Filtro nativo do Excel.
    worksheet.auto_filter.ref = worksheet.dimensions

    # Formatação das datas.
    for row_number in range(2, worksheet.max_row + 1):
        for column_number in (9, 10, 15, 17):
            cell = worksheet.cell(
                row=row_number,
                column=column_number,
            )

            if isinstance(cell.value, datetime):
                cell.number_format = "dd/mm/yyyy hh:mm"

            elif isinstance(cell.value, date):
                cell.number_format = "dd/mm/yyyy"

    # Largura automática com limite.
    for column_cells in worksheet.columns:
        column_index = column_cells[0].column
        column_letter = get_column_letter(column_index)

        max_length = 0

        for cell in column_cells:
            value = cell.value

            if value is None:
                continue

            max_length = max(
                max_length,
                len(str(value)),
            )

        worksheet.column_dimensions[column_letter].width = min(
            max(max_length + 2, 12),
            45,
        )

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    filename = (
        "acompanhamento_documental_"
        f"{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    )

    return StreamingResponse(
        output,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )

@router.get(
    "/documentos/metricas",
    response_class=HTMLResponse,
)
def documentos_metricas(
    request: Request,
    days: int = 30,
):
    from modules.documentos.services.metrics import (
        metrics_snapshot,
    )

    days = max(7, min(days, 365))

    dashboard = metrics_snapshot(days)

    return templates.TemplateResponse(
        "documentos_metricas.html",
        {
            "request": request,
            "dashboard": dashboard,
            "days": days,
        },
    )
