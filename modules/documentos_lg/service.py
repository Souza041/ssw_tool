from pathlib import Path
import re

from operations.op455.report import OP455Report
from operations.op930.report import OP930Report
from web.jobs import add_log, set_progress
from ssw.utils import dummy
from .op435 import Extrator, Inputs, comando, BASE
from .filtro import filtrar

LG_CNPJ = '01166372000155'


class ClientLG:
    """Reusa o cliente autenticado sem repetir POST de geração após falha."""
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def post(self, path, data=None, **kwargs):
        kwargs['retries'] = 1
        r = self.original.post(path, data=data, **kwargs)
        r.encoding = 'latin1'
        return r

    def get(self, path, params=None, **kwargs):
        r = self.original.get(path, params=params, **kwargs)
        if 'text' in r.headers.get('Content-Type', '').lower():
            r.encoding = 'latin1'
        return r

    def open_option(self, option, unidade=None):
        # Respeita o programa retornado pelo menu em vez de fixar ssw0053.
        r = self.post('/bin/menu01', {'act': 'TRO', 'f2': unidade or self.unidade, 'f3': str(option), 'dummy': dummy()})
        target = re.search(r"\b(ssw\d+)\?sequencia=" + re.escape(str(option)) + r"(?:['&]|$)", comando(r.text))
        if not target:
            raise ValueError('O menu SSW não confirmou a abertura da opção solicitada.')
        return self.post('/bin/' + target[1], {'sequencia': str(option), 'dummy': dummy()})


def validar_download(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('O relatório retornou um arquivo vazio.')
    with path.open('rb') as f:
        prefix = f.read(1024).lstrip(b'\x13\xef\xbb\xbf \r\n\t').lower()
    if any(tag in prefix for tag in (b'<html', b'<!doctype html', b'<form', b'<input')):
        raise ValueError('O download retornou uma página HTML em vez do relatório.')
    return path


def executar(job, client, config, pasta):
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    original_headers = dict(client.session.headers)
    total = 3 if config['modo'] == 'completo' else 1
    set_progress(job, 0, total)

    def publicar(path, label):
        path = validar_download(path)
        if path.resolve().parent != pasta.resolve():
            raise ValueError('Arquivo gerado fora da pasta desta execução.')
        job.result_files.append({'name': path.name, 'periodo': label,
                                 'url': f'/documentos-lg/jobs/{job.id}/arquivos/{len(job.result_files)}'})

    try:
        if client.base_url.rstrip('/') != BASE:
            raise ValueError('A OP435 foi validada para o servidor sistema.ssw.inf.br.')
        add_log(job, 'OP435: gerando e baixando relatório bruto.')
        bruto = Extrator(session=client.session, timeout=config['timeout']).extrair(
            config['unidade'], config['data_final'], pasta)
        publicar(bruto, 'OP435 · original SSW')
        publicar(bruto.with_suffix('.json'), 'OP435 · resumo da extração')
        add_log(job, 'OP435: aplicando filtro na coluna PAGADOR.')
        csv_path, resumo = filtrar(bruto)
        publicar(csv_path, 'OP435 · base LG filtrada')
        publicar(csv_path.with_suffix('.filtro.json'), 'OP435 · resumo do filtro')
        add_log(job, f"OP435: {resumo['registros_mantidos']} de {resumo['registros_lidos']} registros mantidos.")
        set_progress(job, 1, total)
        if config['modo'] != 'completo':
            return None
        adapter = ClientLG(client)
        add_log(job, 'OP455: iniciando extração documental na unidade MTZ.')
        path = OP455Report(adapter).gerar_e_baixar_documentos(
            output_dir=pasta, data_inicial=config['inicio455'].strftime('%d%m%y'),
            data_final=config['fim455'].strftime('%d%m%y'), timeout_seconds=config['timeout'])
        publicar(path, 'OP455 · documentos, base bruta')
        set_progress(job, 2, total)
        add_log(job, 'OP930: iniciando extração do grupo LG.')
        adapter.open_option('930', config['unidade'])
        path = OP930Report(adapter).gerar_e_baixar_por_cnpj(
            output_dir=pasta, data_inicial=config['inicio930'].strftime('%d%m%y'),
            data_final=config['fim930'].strftime('%d%m%y'), cnpj=LG_CNPJ, grupo='LG',
            timeout_seconds=config['timeout'])
        publicar(path, 'OP930 · grupo LG, base bruta')
        set_progress(job, 3, total)
        add_log(job, 'Sequência concluída. Bases OP455 e OP930 preservadas sem tratamento.')
        return None
    except Exception:
        # Não expor payloads, URLs temporárias ou mensagens com credenciais.
        raise RuntimeError('Etapa interrompida. Confira sessão SSW, permissões, período e fila. Os arquivos já concluídos continuam disponíveis.') from None
    finally:
        client.session.headers.clear()
        client.session.headers.update(original_headers)
