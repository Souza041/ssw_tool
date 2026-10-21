from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.documentos_lg import service
from modules.documentos_lg.filtro import corresponde, filtrar
from web.jobs import Job
from web import documentos_lg as routes


def config(modo='completo'):
    return routes.configurar(modo, 'CWB', '2026-10-20', '2026-09-01', '2026-09-30', '2026-10-01', '2026-10-20', 600)


def source(path):
    path.write_bytes(b'\x13\r\n0;CTRCS DISPONIVEIS PARA FATURAMENTO\r\n1;EM;CTRC;PAGADOR\r\n2;CWB;123;LG ELECTRONICS DO BRASIL  (B2) \r\n2;CWB;124;OUTRO\r\n9;TOTAL\r\n')
    return path


def test_filtro_preserva_bruto_e_nomes(tmp_path):
    raw = source(tmp_path / 'original.sswweb')
    before = raw.read_bytes()
    dest, resumo = filtrar(raw)
    assert raw.read_bytes() == before
    assert resumo['registros_lidos'] == 2
    assert resumo['registros_mantidos'] == 1
    assert 'LG ELECTRONICS DO BRASIL  (B2) ' in dest.read_text(encoding='utf-8-sig')
    assert not corresponde('LG ELETRONICS DO BRASIL (B.)')
    assert not corresponde('OUTRO LG ELECTRONICS DO BRASIL')
    with pytest.raises(FileExistsError):
        filtrar(raw)


@pytest.fixture
def fake_operations(monkeypatch):
    calls = []

    class Extract:
        def __init__(self, session, timeout):
            pass

        def extrair(self, unidade, data_final, pasta):
            calls.append(('435', unidade, data_final))
            path = source(Path(pasta) / 'op435.sswweb')
            path.with_suffix('.json').write_text('{}')
            return path

    class Op455:
        def __init__(self, client):
            pass

        def gerar_e_baixar_documentos(self, **kw):
            calls.append(('455', kw))
            p = kw['output_dir'] / 'op455.sswweb'
            p.write_bytes(b'relatorio;455\n')
            return p

    class Op930:
        def __init__(self, client):
            pass

        def gerar_e_baixar_por_cnpj(self, **kw):
            calls.append(('930', kw))
            p = kw['output_dir'] / 'op930.sswweb'
            p.write_bytes(b'relatorio;930\n')
            return p

    monkeypatch.setattr(service, 'Extrator', Extract)
    monkeypatch.setattr(service, 'OP455Report', Op455)
    monkeypatch.setattr(service, 'OP930Report', Op930)
    monkeypatch.setattr(service.ClientLG, 'open_option', lambda *a: calls.append(('abrir930',)))
    return calls


def client():
    return SimpleNamespace(base_url=service.BASE, session=SimpleNamespace(headers={'Referer': 'original'}))


def test_sequencia_e_periodos_independentes(tmp_path, fake_operations):
    job = Job(id='exemplo')
    c = client()
    service.executar(job, c, config(), tmp_path)
    assert [a[0] for a in fake_operations] == ['435', '455', 'abrir930', '930']
    assert job.progress == job.total == 3
    assert len(job.result_files) == 6
    assert fake_operations[1][1]['data_inicial'] == '010926'
    assert fake_operations[3][1]['data_inicial'] == '011026'
    assert fake_operations[3][1]['cnpj'] == service.LG_CNPJ
    assert c.session.headers == {'Referer': 'original'}


def test_modo435_nao_dispara_outros(tmp_path, fake_operations):
    job = Job(id='exemplo')
    service.executar(job, client(), config('op435'), tmp_path)
    assert [a[0] for a in fake_operations] == ['435']
    assert job.progress == job.total == 1
    assert len(job.result_files) == 4


def test_falha455_interrompe930_preserva435(tmp_path, fake_operations, monkeypatch):
    class Fail:
        def __init__(self, client): pass
        def gerar_e_baixar_documentos(self, **kw): raise RuntimeError('detalhe interno')
    monkeypatch.setattr(service, 'OP455Report', Fail)
    job = Job(id='exemplo')
    with pytest.raises(RuntimeError, match='Etapa interrompida'):
        service.executar(job, client(), config(), tmp_path)
    assert [a[0] for a in fake_operations] == ['435']
    assert len(job.result_files) == 4
    assert job.progress == 1


def test_adapter_abre_programa_retornado_sem_retry_post():
    response = SimpleNamespace(text='<input name=web_body value="ajaxEnvia%28%27%27%2C1%2C%27ssw0846%3Fsequencia%3D930%27%29">', encoding=None)
    original = SimpleNamespace(post=Mock(return_value=response), unidade='CWB')
    adapter = service.ClientLG(original)
    adapter.open_option('930')
    assert original.post.call_args_list[0].args[0] == '/bin/menu01'
    assert original.post.call_args_list[1].args[0] == '/bin/ssw0846'
    assert all(c.kwargs['retries'] == 1 for c in original.post.call_args_list)


@pytest.fixture
def web(monkeypatch, tmp_path):
    routes.RUNS.clear()
    sessions = {'a': {'ssw_session_id': 'a'}, 'b': {'ssw_session_id': 'b'}}
    app = FastAPI()
    @app.middleware('http')
    async def session(request, call_next):
        request.scope['session'] = sessions.get(request.headers.get('x-test-session'), {})
        return await call_next(request)
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'obter_client', lambda r: SimpleNamespace(unidade='CWB') if r.session.get('ssw_session_id') else None)
    monkeypatch.setattr(routes, 'PASTA', tmp_path)
    worker = Mock()
    monkeypatch.setattr(routes, 'executar_job', worker)
    with TestClient(app) as test:
        yield test, sessions, worker
    routes.RUNS.clear()


def request_data(web):
    test, sessions, _ = web
    page = test.get('/documentos-lg', headers={'x-test-session': 'a'})
    assert page.status_code == 200
    assert 'LG ELECTRONICS DO BRASIL' in page.text
    return dict(csrf_token=sessions['a']['documentos_lg_csrf'], modo='op435', unidade='CWB', data_final='2026-10-20')


def test_rota_exige_login_csrf_e_datas(web):
    test, _, worker = web
    assert test.get('/documentos-lg', follow_redirects=False).status_code == 303
    data = request_data(web)
    data['csrf_token'] = 'incorreto'
    assert test.post('/documentos-lg/executar', data=data, headers={'x-test-session': 'a'}).status_code == 403
    data = request_data(web)
    data['modo'] = 'completo'
    assert test.post('/documentos-lg/executar', data=data, headers={'x-test-session': 'a'}).status_code == 422
    worker.assert_not_called()


def test_dono_download_e_duplicidade(web, tmp_path):
    test, _, worker = web
    data = request_data(web)
    r = test.post('/documentos-lg/executar', data=data, headers={'x-test-session': 'a'}, follow_redirects=False)
    assert r.status_code == 303
    worker.assert_called_once()
    path = r.headers['location']
    assert test.post('/documentos-lg/executar', data=data, headers={'x-test-session': 'a'}).status_code == 409
    assert test.get(path + '/estado', headers={'x-test-session': 'b'}).status_code == 404
    assert test.get(path + '/estado').status_code == 401
    assert test.get(path, headers={'x-test-session': 'a'}).status_code == 200
    job_id = path.rsplit('/',1)[1]
    job = routes.RUNS[job_id]['job']
    folder = tmp_path / job_id
    folder.mkdir()
    (folder / 'base.csv').write_text('exemplo')
    job.result_files.append({'name':'base.csv','url':path+'/arquivos/0','periodo':'OP435'})
    assert test.get(path+'/arquivos/0', headers={'x-test-session':'a'}).text == 'exemplo'
    assert test.get(path+'/arquivos/0', headers={'x-test-session':'b'}).status_code == 404
    assert test.get(path+'/arquivos/-1', headers={'x-test-session':'a'}).status_code == 404
    job.result_files.append({'name':'../fora.csv'})
    assert test.get(path+'/arquivos/1', headers={'x-test-session':'a'}).status_code == 404
    state = test.get(path+'/estado', headers={'x-test-session':'a'}).json()
    assert state['status'] == 'running'


@pytest.mark.parametrize('values', [
    ('completo','CWB','2026-10-20','2026-10-21','2026-10-20','2026-10-01','2026-10-20',600),
    ('outro','CWB','2026-10-20','','','','',600),
    ('op435','../','2026-10-20','','','','',600),
    ('op435','CWB','invalid','','','','',600),
    ('op435','CWB','2026-10-20','','','','',0),
])
def test_configuracoes_invalidas(values):
    with pytest.raises(ValueError): routes.configurar(*values)
