"""Testes offline: sessões falsas, sem login ou chamadas ao SSW."""
import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import requests

from ssw.settings import SSWSettings, normalizar_base_url, settings
from ssw.client import SSWClient
from modules.documentos_lg.op435 import Extrator, FILTROS, ErroSSW
from modules.documentos_lg import service
from operations.op455.report import OP455Report


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    def bloquear(*args, **kwargs):
        raise AssertionError("Requisição real proibida neste teste")
    monkeypatch.setattr(requests.sessions.Session, 'request', bloquear)


@pytest.mark.parametrize('base', ['https://sistema.ssw.inf.br', 'http://tms.ssw.inf.br', 'https://ssw.test:8443'])
def test_cliente_e_extrator_usam_mesma_origem(monkeypatch, base):
    monkeypatch.setattr(settings, 'base_url', base + '/')
    client = SSWClient()
    response = SimpleNamespace(status_code=200, text='ok', raise_for_status=Mock())
    client.session.post = Mock(return_value=response)
    client.session.get = Mock(return_value=response)
    client.post('/bin/menu01', retries=1)
    assert client.session.post.call_args.args[0] == base + '/bin/menu01'
    assert client.session.headers['Origin'] == base
    assert client.session.headers['Referer'] == base + '/bin/ssw0422'
    extrator = Extrator(session=client.session, base_url=client.base_url)
    extrator.post('ssw0103', {'act': 'PES'})
    assert client.session.post.call_args.args[0] == base + '/bin/ssw0103'
    assert client.session.post.call_args.kwargs['allow_redirects'] is False
    client.get('/bin/menu01', retries=1)
    assert client.session.get.call_args.args[0] == base + '/bin/menu01'


def test_settings_le_ambiente_ao_criar_instancia(monkeypatch):
    monkeypatch.setenv('SSW_BASE_URL', 'http://tms.ssw.inf.br/')
    assert SSWSettings().base_url == 'http://tms.ssw.inf.br/'
    monkeypatch.delenv('SSW_BASE_URL')
    assert SSWSettings().base_url == 'https://tms.ssw.inf.br'


@pytest.mark.parametrize('valor', ['', 'ssw.test', 'ftp://ssw.test', 'https://u:p@ssw.test', 'https://ssw.test/bin/menu01', 'https://ssw.test?x=1', 'https://ssw.test#x', 'https://ssw.test:99999', 'https://ssw. test'])
def test_base_invalida_nao_vira_destino(valor):
    with pytest.raises(ValueError, match='SSW_BASE_URL'):
        normalizar_base_url(valor)


def test_op435_login_e_download_no_servidor_da_instancia(tmp_path, monkeypatch):
    base = 'http://tms.ssw.inf.br'
    session = requests.Session()
    def response(text='', content=b''):
        return SimpleNamespace(text=text, status_code=200, raise_for_status=Mock(),
                               iter_content=lambda size: [content])
    arquivo = response(content=b'0;CTRCS DISPONIVEIS PARA FATURAMENTO\n1;EM;CTRC;PAGADOR\n')
    class Download:
        def __enter__(self): return arquivo
        def __exit__(self, *args): pass
    session.get = Mock(side_effect=[response('<input name="backimg" value="x">'), Download()])
    form = ''.join(f'<input name="{key}">' for key in [*FILTROS, 't_data_ref_fim'])
    session.post = Mock(side_effect=[
        response('frmlogin menu01'), response('<input name="f2"><input name="f3">'),
        response('<input name="web_body" value="ssw0103?sequencia=435">'),
        response(form), response('download'),
    ])
    monkeypatch.setattr('modules.documentos_lg.op435.download_params', lambda text: {'act':'a.csv'})
    extrator = Extrator(session=session, base_url=base)
    extrator.login('TEST', 'fake', 'fake', 'fake')
    dest = extrator.extrair('CWB', date(2026,11,12), tmp_path)
    assert dest.is_file() and dest.with_suffix('.json').is_file()
    assert all(c.args[0].startswith(base + '/bin/') for c in session.get.call_args_list + session.post.call_args_list)
    assert session.get.call_args.args[0] == base + '/bin/ssw0424'
    assert session.headers['Referer'] == base + '/bin/ssw0103'


def test_op435_mantem_bloqueio_de_redirecionamento():
    session = SimpleNamespace(headers={}, post=Mock(return_value=SimpleNamespace(status_code=302, raise_for_status=Mock())))
    with pytest.raises(ErroSSW, match='Redirecionamento'):
        Extrator(session=session, base_url='http://tms.ssw.inf.br').post('ssw0103', {})


def test_op435_base_explicita_prevalece_sobre_global(monkeypatch):
    monkeypatch.setattr(settings, 'base_url', 'https://sistema.ssw.inf.br')
    session = SimpleNamespace(headers={})
    extrator = Extrator(session=session, base_url=' http://tms.ssw.inf.br/ ')
    assert extrator.base_url == session.headers['Origin'] == 'http://tms.ssw.inf.br'


def test_op455_diagnostico_usa_servidor_do_cliente(monkeypatch, tmp_path):
    import operations.op455.report as report_module
    fila = SimpleNamespace(extrair_arquivo_direto=Mock(return_value=None), baixar_por_opcao=Mock(return_value=tmp_path/'teste.sswweb'))
    monkeypatch.setattr(report_module, 'OP156Queue', lambda client: fila)
    report = OP455Report(SimpleNamespace(base_url='http://tms.ssw.inf.br/'))
    monkeypatch.setattr(report, '_abrir_op455_oc73', Mock(return_value='aberto'))
    monkeypatch.setattr(report, 'validar_contexto_abertura_ocorrencia_73', Mock(return_value={'status':'confirmado'}))
    monkeypatch.setattr(report, 'capturar_ids_fila', Mock(return_value=set()))
    monkeypatch.setattr(report, 'gerar_relatorio_ocorrencia_73', Mock(return_value='relatorio'))
    resumo = Mock(return_value={'frase_informe_unidade': {'texto_visivel':False}})
    monkeypatch.setattr(report, 'resumir_resposta_diagnostica', resumo)
    monkeypatch.setattr(report, 'validar_layout_ocorrencia_73', Mock())
    report.gerar_e_baixar_ocorrencia_73(output_dir=tmp_path, data_inicial='121126', data_final='121126', unidade='BIG')
    assert resumo.call_args.args[0].url == 'http://tms.ssw.inf.br/bin/ssw0230'
    assert fila.baixar_por_opcao.call_args.kwargs['estrito'] is True


def test_lg_usa_base_do_cliente_e_restaura_headers(tmp_path, monkeypatch):
    class Interromper(Exception): pass
    recebido = {}
    class FakeExtrator:
        def __init__(self, **kwargs):
            recebido.update(kwargs)
            kwargs['session'].headers['Origin'] = 'temporario'
        def extrair(self, *args): raise Interromper()
    monkeypatch.setattr(service, 'Extrator', FakeExtrator)
    monkeypatch.setattr(service, 'set_progress', Mock())
    monkeypatch.setattr(service, 'add_log', Mock())
    client = SimpleNamespace(base_url='http://tms.ssw.inf.br', session=SimpleNamespace(headers={'Origin':'original'}))
    with pytest.raises(RuntimeError, match='Etapa interrompida'):
        service.executar(SimpleNamespace(), client, {'modo':'op435','timeout':60,'unidade':'CWB','data_final':date(2026,11,12)}, tmp_path)
    assert recebido['base_url'] == client.base_url
    assert recebido['session'] is client.session
    assert client.session.headers == {'Origin':'original'}


def debug_functions():
    # Carrega só os helpers; não exige Chromium nem executa o script de diagnóstico.
    path = Path(__file__).resolve().parents[1] / 'scripts/debug_op475_playwright.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'instalar_network_logger','copiar_cookies_para_playwright'}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    ns = dict(urlsplit=urlsplit, normalizar_base_url=normalizar_base_url, settings=settings, log=Mock(), resumo_post_data=lambda value:{})
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), ns)
    return ns


def test_debug_filtra_origem_e_cookie_sem_dominio(capsys):
    ns = debug_functions()
    events = {}
    ns['instalar_network_logger'](SimpleNamespace(on=lambda name, fn: events.update({name:fn})), 'TEST', 'http://tms.ssw.inf.br')
    for url in ['http://tms.ssw.inf.br/bin/menu01', 'http://evil.test/?q=tms.ssw.inf.br', 'http://tms.ssw.inf.br.evil.test/', 'https://tms.ssw.inf.br/']:
        events['request'](SimpleNamespace(url=url, method='GET', post_data=None))
    saida = capsys.readouterr().out
    assert saida.count('PW REQUEST') == 1
    context = SimpleNamespace(add_cookies=Mock())
    cookies = [SimpleNamespace(name='sid',value='fake',domain='',path='/')]
    ns['copiar_cookies_para_playwright'](SimpleNamespace(base_url='http://tms.ssw.inf.br',session=SimpleNamespace(cookies=cookies)), context)
    assert context.add_cookies.call_args.args[0][0]['domain'] == 'tms.ssw.inf.br'
