from datetime import date
from pathlib import Path
import secrets
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from web.session_store import obter_client
from .config import today, Settings, validate_period
from .repository import Repository
from .service import start_execution, start_group
from .audit import xlsx_bytes, csv_bytes
from .accounts import PROFILES, account_cards, filtered_history, live_active
from .presentation import LABELS, clean_message, stamp, clock, day, overview

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/lg-pod', tags=['LG POD'])
templates = Jinja2Templates(directory=[str(Path(__file__).parent / 'templates'), str(ROOT / 'web/templates')])

templates.env.filters.update(lg_message=clean_message, lg_stamp=stamp, lg_clock=clock, lg_day=day)
templates.env.globals.update(lg_labels=LABELS)


def client(request):
    return obter_client(request)


@router.get('', response_class=HTMLResponse)
def dashboard(request: Request, execucao: str = '', page: int = 1, etapa: str = '', busca: str = '', operacao: str = ''):
    if client(request) is None:
        return RedirectResponse('/login', status_code=303)
    if operacao not in ('', *PROFILES):
        raise HTTPException(400, 'Operação inválida.')
    csrf = request.session.setdefault('lg_pod_csrf', secrets.token_urlsafe(32))
    page = max(1, min(page, 100000))
    history, report, error, activities, accounts = [], None, '', [], []
    busca = busca.strip()[:120]
    etapa = etapa[:40]
    try:
        with Repository() as repo:
            accounts = account_cards(repo)
            history = filtered_history(repo, operacao)
            if not execucao and history:
                execucao = history[0]['id']
            if execucao:
                report = repo.report(execucao, page, etapa, busca)
                if not report['run']:
                    raise HTTPException(404, 'Execução não encontrada.')
                activities = repo.activities(execucao)
        enabled = any(c['send_enabled'] for c in accounts)
    except HTTPException:
        raise
    except Exception:
        enabled = False
        error = 'Não foi possível carregar o painel LG POD. Verifique o banco e a configuração dos perfis.'
    view = overview(report) if report and report['run'] else None
    response = templates.TemplateResponse(request, 'lg_pod.html', dict(
        csrf_token=csrf, history=history, report=report, error=error, today=today().isoformat(),
        send_enabled=enabled, page=page, stage=etapa, search=busca, activities=activities,
        view=view, accounts=accounts, operation=operacao, live_active=live_active(view, accounts),
    ))
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/estado')
def state(request: Request, execucao: str, page: int = 1, etapa: str = '', busca: str = '', operacao: str = ''):
    if client(request) is None:
        return JSONResponse({'detail': 'Sua sessão expirou. Entre novamente.'}, status_code=401,
                            headers={'Cache-Control': 'no-store'})
    if operacao not in ('', *PROFILES):
        raise HTTPException(400, 'Operação inválida.')
    page = max(1, min(page, 100000))
    try:
        with Repository() as repo:
            report = repo.report(execucao, page, etapa[:40], busca.strip()[:120])
            if not report['run']:
                raise HTTPException(404, 'Execução não encontrada.')
            activities = repo.activities(execucao)
            accounts = account_cards(repo)
            history = filtered_history(repo, operacao)
    except HTTPException:
        raise
    except Exception:
        return JSONResponse({'detail': 'Não foi possível atualizar. Tentaremos novamente; a execução continua independente desta tela.'},
                            status_code=503, headers={'Cache-Control': 'no-store'})
    view = overview(report)
    context = dict(report=report, view=view, page=page, stage=etapa[:40], search=busca.strip()[:120],
                   activities=activities, history=history, accounts=accounts, operation=operacao)
    parts = {name: templates.env.get_template('lg_pod_' + name + '.html').render(**context)
             for name in ('summary', 'items', 'activity', 'history', 'accounts')}
    return JSONResponse(dict(parts=parts, active=live_active(view, accounts)), headers={'Cache-Control': 'no-store'})


@router.get('/relatorio')
def export_report(request: Request, formato: str = 'xlsx', execucao: str = '', inicio: date | None = None,
                  fim: date | None = None, operacao: str = '', etapa: str = '', busca: str = ''):
    if client(request) is None:
        return JSONResponse({'detail':'Sua sessão expirou. Entre novamente.'},status_code=401,
                            headers={'Cache-Control':'no-store'})
    if formato not in {'xlsx','csv'} or operacao not in ('',*PROFILES):
        raise HTTPException(400,'Formato ou operação inválidos.')
    if (bool(execucao) and (inicio or fim or operacao)) or (not execucao and (inicio is None or fim is None)):
        raise HTTPException(400,'Selecione uma execução ou um intervalo de datas de execução.')
    try:
        with Repository() as repo:
            if execucao:
                bundles=repo.export_ids([execucao],etapa[:40],busca.strip()[:120])
                criteria='Execução '+execucao+' | Etapa: '+(etapa[:40] or 'todas')+' | Busca: '+busca.strip()[:120]
            else:
                validate_period(inicio,fim)
                if (fim-inicio).days>366:raise ValueError('Selecione até 366 dias por exportação.')
                settings=Settings.from_env(operacao) if operacao else None
                bundles=repo.export_period(inicio,fim,settings.user if settings else None,settings.carrier if settings else None)
                criteria=f'Datas de INÍCIO das execuções (Brasília): {inicio} a {fim} | Operação: {operacao or "todas"}'
        payload=xlsx_bytes(bundles,criteria) if formato=='xlsx' else csv_bytes(bundles)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Não foi possível exportar. Verifique o banco e a dependência openpyxl para Excel.') from None
    name='lg_pod_auditoria.'+formato
    media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if formato=='xlsx' else 'text/csv; charset=utf-8'
    return Response(payload,media_type=media,headers={'Content-Disposition':'attachment; filename="'+name+'"',
                    'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.post('/executar')
def run(request: Request, data_inicial: date = Form(...), data_final: date = Form(...),
        modo: str = Form('simular'), csrf_token: str = Form(...), perfil: str = Form('SC')):
    user = client(request)
    if user is None:
        return RedirectResponse('/login', status_code=303)
    expected = request.session.get('lg_pod_csrf', '')
    if not expected or not secrets.compare_digest(expected, csrf_token):
        raise HTTPException(403, 'Token CSRF inválido. Atualize a página.')
    if modo not in {'simular', 'executar'}:
        raise HTTPException(400, 'Modo inválido.')
    if perfil not in (*PROFILES, 'AMBAS', 'TODAS'):
        raise HTTPException(400, 'Perfil inválido.')
    try:
        if perfil == 'TODAS':
            run_id = start_group(data_inicial, data_final, dry_run=modo == 'simular',
                                 actor=str(getattr(user, 'usuario', 'web')), parallel=True, profiles=PROFILES)[0]
        elif perfil == 'AMBAS':
            run_id = start_group(data_inicial, data_final, dry_run=modo == 'simular',
                                 actor=str(getattr(user, 'usuario', 'web')), parallel=True)[0]
        else:
            run_id = start_execution(data_inicial, data_final, dry_run=modo == 'simular',
                                     actor=str(getattr(user, 'usuario', 'web')), profile=perfil)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, 'Não foi possível iniciar. Verifique a configuração e o banco LG POD.') from None
    return RedirectResponse('/lg-pod?execucao=' + run_id + ('&operacao=' + perfil if perfil in PROFILES else ''), status_code=303)
