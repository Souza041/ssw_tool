"""Rotas aditivas da rotina LG; reutilizam cliente e executor do sistema."""
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import secrets
import threading
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.jobs import Job, executar_job
from web.session_store import obter_client
from modules.documentos_lg.service import executar

router = APIRouter(prefix='/documentos-lg')
ROOT = Path(__file__).resolve().parent.parent
PASTA = ROOT / 'temp' / 'documentos_lg'
templates = Jinja2Templates(directory=str(ROOT / 'web' / 'templates'))
# Mesma duração do processo que os jobs existentes; não persistem após reinício.
RUNS = {}
LOCK = threading.Lock()


def cliente(request):
    return obter_client(request)


def dono(request, job_id):
    if not cliente(request):
        raise HTTPException(401, 'Entre novamente no sistema.')
    run = RUNS.get(job_id)
    if not run or run['owner'] != request.session.get('ssw_session_id'):
        raise HTTPException(404, 'Execução não encontrada.')
    return run


def configurar(modo, unidade, data_final, inicio455, fim455, inicio930, fim930, timeout):
    if modo not in ('op435', 'completo'):
        raise ValueError('Escolha uma opção de execução válida.')
    unidade = unidade.strip().upper()
    if not re.fullmatch('[A-Z0-9]{3}', unidade):
        raise ValueError('Informe uma unidade de três caracteres.')
    if not 30 <= timeout <= 1800:
        raise ValueError('O tempo limite deve estar entre 30 e 1800 segundos.')
    try:
        result = dict(modo=modo, unidade=unidade, timeout=timeout, data_final=date.fromisoformat(data_final))
        if result['data_final'].year < 2001 or result['data_final'].year > 2099:
            raise ValueError()
        if modo == 'completo':
            for key, value in [('inicio455',inicio455),('fim455',fim455),('inicio930',inicio930),('fim930',fim930)]:
                result[key] = date.fromisoformat(value)
                if not 2001 <= result[key].year <= 2099:
                    raise ValueError()
            if result['inicio455'] > result['fim455'] or result['inicio930'] > result['fim930']:
                raise ValueError()
    except ValueError:
        raise ValueError('Confira as datas: use períodos válidos e completos, com início até o fim, entre 2001 e 2099.') from None
    return result


@router.get('')
def formulario(request: Request):
    client = cliente(request)
    if not client:
        return RedirectResponse('/login', status_code=303)
    token = request.session.setdefault('documentos_lg_csrf', secrets.token_urlsafe(32))
    return templates.TemplateResponse('documentos_lg.html', {
        'request': request, 'csrf_token': token,
        'unidade': client.unidade, 'pode_executar': True,
        'data_final': (datetime.now(ZoneInfo('America/Sao_Paulo')) - timedelta(days=1)).date().isoformat(),
    })


@router.post('/executar')
def iniciar(request: Request, csrf_token: str = Form(...), modo: str = Form('op435'),
            unidade: str = Form(...), data_final: str = Form(...),
            inicio455: str = Form(''), fim455: str = Form(''),
            inicio930: str = Form(''), fim930: str = Form(''), timeout: int = Form(600)):
    client = cliente(request)
    if not client:
        return RedirectResponse('/login', status_code=303)
    expected = request.session.get('documentos_lg_csrf', '')
    if not expected or not secrets.compare_digest(csrf_token, expected):
        raise HTTPException(403, 'Reabra a tela antes de executar.')
    try:
        config = configurar(modo, unidade, data_final, inicio455, fim455, inicio930, fim930, timeout)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    owner = request.session['ssw_session_id']
    with LOCK:
        if any(r['owner'] == owner and r['job'].status == 'running' for r in RUNS.values()):
            raise HTTPException(409, 'Já existe uma execução LG em andamento nesta sessão.')
        job = Job(id=str(uuid.uuid4()))
        RUNS[job.id] = {'owner': owner, 'job': job}
    executar_job(job, executar, client, config, PASTA / job.id)
    return RedirectResponse(f'/documentos-lg/jobs/{job.id}', status_code=303)


@router.get('/jobs/{job_id}')
def progresso(request: Request, job_id: str):
    dono(request, job_id)
    return templates.TemplateResponse('documentos_lg_job.html', {'request': request, 'job_id': job_id})


@router.get('/jobs/{job_id}/estado')
def estado(request: Request, job_id: str):
    job = dono(request, job_id)['job']
    with job.logs.mutex:
        logs = list(job.logs.queue)[-200:]
    return {'status': job.status, 'progress': job.progress, 'total': job.total,
            'logs': logs, 'error': job.error, 'files': list(job.result_files)}


@router.get('/jobs/{job_id}/arquivos/{indice}')
def baixar(request: Request, job_id: str, indice: int):
    job = dono(request, job_id)['job']
    if indice < 0 or indice >= len(job.result_files):
        raise HTTPException(404, 'Arquivo não encontrado.')
    name = job.result_files[indice]['name']
    pasta = (PASTA / job_id).resolve()
    path = (pasta / name).resolve()
    if path.parent != pasta or not path.is_file():
        raise HTTPException(404, 'Arquivo não encontrado.')
    return FileResponse(path, filename=path.name)
