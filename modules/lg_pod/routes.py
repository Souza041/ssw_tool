from datetime import date
from pathlib import Path
import secrets
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.session_store import obter_client
from .config import today, Settings
from .repository import Repository
from .service import start_execution

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/lg-pod', tags=['LG POD'])
templates = Jinja2Templates(directory=[str(Path(__file__).parent / 'templates'), str(ROOT / 'web/templates')])


def client(request):
    return obter_client(request)


@router.get('', response_class=HTMLResponse)
def dashboard(request: Request, execucao: str = '', page: int = 1, etapa: str = ''):
    if client(request) is None:
        return RedirectResponse('/login', status_code=303)
    csrf = request.session.setdefault('lg_pod_csrf', secrets.token_urlsafe(32))
    page = max(1, min(page, 100000))
    history, report, error = [], None, ''
    try:
        with Repository() as repo:
            history = repo.history()
            if execucao:
                report = repo.report(execucao, page, etapa)
                if not report['run']:
                    raise HTTPException(404, 'Execução não encontrada.')
        enabled = Settings.from_env().send_enabled
    except HTTPException:
        raise
    except Exception:
        enabled = False
        error = 'Não foi possível ler o banco LG POD. Confira o .env e execute a instalação das tabelas.'
    return templates.TemplateResponse(request, 'lg_pod.html', dict(
        csrf_token=csrf, history=history, report=report, error=error, today=today().isoformat(),
        send_enabled=enabled, page=page, stage=etapa,
    ))


@router.post('/executar')
def run(request: Request, data_inicial: date = Form(...), data_final: date = Form(...),
        modo: str = Form('simular'), csrf_token: str = Form(...)):
    user = client(request)
    if user is None:
        return RedirectResponse('/login', status_code=303)
    expected = request.session.get('lg_pod_csrf', '')
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(403, 'Token CSRF inválido. Atualize a página.')
    if modo not in {'simular', 'executar'}:
        raise HTTPException(400, 'Modo inválido.')
    try:
        run_id = start_execution(data_inicial, data_final, dry_run=modo == 'simular',
                                 actor=str(getattr(user, 'usuario', 'web')))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, 'Não foi possível iniciar. Verifique a configuração e o banco LG POD.') from None
    return RedirectResponse('/lg-pod?execucao=' + run_id, status_code=303)
