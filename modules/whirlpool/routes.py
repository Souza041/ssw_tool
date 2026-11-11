import secrets
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from modules.whirlpool.service import (
    UploadWhirlpoolInvalido,
    WhirlpoolService,
    owner_hash,
)
from modules.whirlpool.config import WhirlpoolPortalSettings
from modules.whirlpool.portal_jobs import (
    PORTAL_JOBS,
    consumir_logs,
    criar_portal_job,
    executar_portal_job,
)
from web.session_store import obter_client


ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/whirlpool", tags=["whirlpool"])
templates = Jinja2Templates(directory=[
    str(ROOT / "modules" / "whirlpool" / "templates"),
    str(ROOT / "web" / "templates"),
])


def _service() -> WhirlpoolService:
    return WhirlpoolService()


def _session_owner(request: Request) -> str | None:
    session_id = str(request.session.get("ssw_session_id") or "")
    if not session_id or obter_client(request) is None:
        request.session.clear()
        return None
    return owner_hash(session_id)


@router.get("/emails", response_class=HTMLResponse)
def emails_page(request: Request, execucao: str | None = None):
    owner = _session_owner(request)
    if owner is None:
        return RedirectResponse("/login", status_code=303)
    csrf = request.session.setdefault("whirlpool_csrf", secrets.token_urlsafe(32))
    service = _service()
    selected = service.obter_do_usuario(execucao, owner) if execucao else None
    client = obter_client(request)
    try:
        portal_settings = WhirlpoolPortalSettings.from_env()
        portal_available = (
            portal_settings.enabled
            and portal_settings.dry_run
            and bool(client)
            and str(getattr(client, "usuario", "")).strip().casefold()
            in portal_settings.authorized_users
        )
    except ValueError:
        portal_available = False
    return templates.TemplateResponse(
        request,
        "whirlpool_emails.html",
        {
            "csrf_token": csrf,
            "execucao": selected,
            "historico": service.listar_do_usuario(owner),
            "portal_available": portal_available,
        },
    )


@router.post("/emails/simular")
async def simular_emails(
    request: Request,
    arquivo: UploadFile = File(...),
    csrf_token: str = Form(...),
):
    owner = _session_owner(request)
    if owner is None:
        return RedirectResponse("/login", status_code=303)
    expected = str(request.session.get("whirlpool_csrf") or "")
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(status_code=403, detail="Token CSRF inválido.")

    service = _service()
    content = await arquivo.read(service.settings.max_upload_bytes + 1)
    try:
        record = service.simular(content, arquivo.filename or "whirlpool.xlsx", owner)
        run_id = record["id"]
    except UploadWhirlpoolInvalido as exc:
        if exc.run_id:
            run_id = exc.run_id
        else:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/whirlpool/emails?execucao={run_id}", status_code=303,
    )


@router.get("/emails/execucoes/{run_id}")
def status_execucao(request: Request, run_id: str):
    owner = _session_owner(request)
    if owner is None:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    record = _service().obter_do_usuario(run_id, owner)
    if record is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return {
        "id": record["id"],
        "status": record["status"],
        "progresso": record.get("progresso", 0),
        "total": record.get("total", 0),
        "envio_real": False,
    }


@router.get("/emails/resultados/{run_id}")
def download_resultado(request: Request, run_id: str):
    owner = _session_owner(request)
    if owner is None:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    result = _service().resultado_do_usuario(run_id, owner)
    if result is None:
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    return FileResponse(
        result,
        filename=f"whirlpool_resultado_{run_id[:8]}.xlsx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/emails/{run_id}/portal-dry-run")
def iniciar_portal_dry_run(
    request: Request,
    run_id: str,
    csrf_token: str = Form(...),
    confirmacao: str = Form(...),
):
    owner = _session_owner(request)
    client = obter_client(request)
    if owner is None or client is None:
        return RedirectResponse("/login", status_code=303)
    expected = str(request.session.get("whirlpool_csrf") or "")
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(status_code=403, detail="Token CSRF inválido.")
    if confirmacao.strip() != "HOMOLOGAR PORTAL SEM ENVIO":
        raise HTTPException(status_code=400, detail="Confirmação inválida.")
    if _service().obter_do_usuario(run_id, owner) is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")

    try:
        portal_settings = WhirlpoolPortalSettings.from_env()
        portal_settings.validar_homologacao(str(getattr(client, "usuario", "")))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    run = criar_portal_job(owner, run_id)
    executar_portal_job(run, str(client.usuario))
    return RedirectResponse(
        f"/whirlpool/emails/portal-jobs/{run.job.id}", status_code=303,
    )


def _portal_job_do_usuario(request: Request, job_id: str):
    owner = _session_owner(request)
    if owner is None:
        raise HTTPException(status_code=401, detail="Sessão expirada.")
    run = PORTAL_JOBS.get(job_id)
    if run is None or run.owner_hash != owner:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return run


@router.get("/emails/portal-jobs/{job_id}", response_class=HTMLResponse)
def portal_job_page(request: Request, job_id: str):
    _portal_job_do_usuario(request, job_id)
    return templates.TemplateResponse(
        request, "whirlpool_portal_job.html", {"job_id": job_id},
    )


@router.get("/emails/portal-jobs/{job_id}/estado")
def portal_job_status(request: Request, job_id: str):
    run = _portal_job_do_usuario(request, job_id)
    job = run.job
    return {
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "logs": consumir_logs(run),
        "error": job.error,
        "smtp_executado": False,
        "files": [
            {
                "name": item["name"],
                "transporte": item["transporte"],
                "url": (
                    f"/whirlpool/emails/portal-jobs/{job_id}/arquivos/{index}"
                ),
            }
            for index, item in enumerate(job.result_files)
        ],
    }


@router.get("/emails/portal-jobs/{job_id}/arquivos/{index}")
def download_documento_portal(request: Request, job_id: str, index: int):
    run = _portal_job_do_usuario(request, job_id)
    if index < 0 or index >= len(run.job.result_files):
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    item = run.job.result_files[index]
    path = Path(item["path"]).resolve()
    allowed = (
        _service().settings.data_dir / "files" / run.run_id / "portal" / job_id
    ).resolve()
    if allowed not in path.parents or not path.is_file() or path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    return FileResponse(
        path,
        filename=Path(item["name"]).name,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
