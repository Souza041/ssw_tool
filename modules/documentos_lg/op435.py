"""Extrator OP435 por HTTP, baseado nos HARs de 21/10/2026. Python 3.9+."""
import argparse
from datetime import datetime, timedelta
from getpass import getpass
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE = 'https://sistema.ssw.inf.br'
LOG = logging.getLogger('op435')
FILTROS = {
    'cod_emp_ctb': '01', 't_data_ref_ini': '010101', 't_tp_fil': 'C',
    't_tp_cliente': 'C', 't_tp_cli_fat': 'T', 't_situacao_ctrc': 'I',
    't_periodicidade': 'T', 't_rel_lista': 'T', 't_cons_bloqueados': 'S',
    't_cons_a_vista': 'S', 't_tp_classificacao': 'C', 't_excel': 'S',
    't_ler_morto': 'S',
}


class ErroSSW(RuntimeError):
    pass


class Inputs(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.values = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input':
            self.values[a.get('name', a.get('id', ''))] = a.get('value', '')


def comando(text):
    # Decodifica o comando sem executar JavaScript vindo do servidor.
    return unquote(Inputs(text).values.get('web_body', ''))


def download_params(text):
    m = re.search(r"\babrir\('([^']+)'\s*,\s*'([^']+)'\s*,\s*1\s*,\s*1(?:\s*,\s*'([^']*)')?", comando(text))
    if not m:
        raise ErroSSW('A geração não retornou o comando de download esperado. Verifique filtros, permissão e sessão no SSW.')
    if not m[1].lower().endswith('.csv') or not m[2].lower().startswith('csvssw0103'):
        raise ErroSSW('O servidor retornou um arquivo diferente do relatório OP435 esperado.')
    return {'act': m[1], 'filename': m[2], 'down': '1', 'nw': '1'}


def validar_arquivo(path):
    with path.open('rb') as f:
        prefix = f.read(8192)
    # Apenas identifica o conteúdo; nenhum byte do arquivo é alterado.
    text = prefix.decode('latin1').lstrip('\x13\ufeff\r\n \t')
    if not text.startswith('0;') or 'CTRCS DISPONIVEIS PARA FATURAMENTO' not in text.upper():
        raise ErroSSW('Conteúdo inesperado: o download não é o relatório OP435 observado no HAR.')
    if not re.search(r'(?:\r?\n)1;EM;CTRC;', text):
        raise ErroSSW('Cabeçalho analítico da OP435 ausente no download.')


class Extrator:
    def __init__(self, session=None, timeout=600):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0', 'Origin': BASE,
            'Referer': BASE + '/bin/ssw0422',
        })

    def post(self, endpoint, data, ajax=True):
        payload = dict(data)
        if ajax:
            payload['dummy'] = str(int(time.time() * 1000))
        r = self.session.post(BASE + '/bin/' + endpoint, data=payload,
                              timeout=(30, self.timeout), allow_redirects=False)
        r.raise_for_status()
        if 300 <= r.status_code < 400:
            raise ErroSSW('Redirecionamento inesperado no SSW; sessão não confirmada.')
        r.encoding = 'latin1'
        return r.text

    def login(self, dominio, cpf, usuario, senha):
        LOG.info('Autenticando no SSW')
        # A captura começa no POST; não há necessidade de reaproveitar cookies do HAR.
        r = self.session.get(BASE + '/bin/ssw0422', timeout=(30, self.timeout), allow_redirects=False)
        r.raise_for_status()
        r.encoding = 'latin1'
        initial = Inputs(r.text).values
        data = {'act': 'L', 'f1': dominio, 'f2': cpf, 'f3': usuario,
                'f4': senha, 'f6': 'TRUE'}
        if initial.get('backimg'):
            data['backimg'] = initial['backimg']
        text = self.post('ssw0422', data)
        if 'frmlogin' not in text or 'menu01' not in text:
            raise ErroSSW('Login não confirmado. Confira domínio, CPF, usuário e senha.')
        menu = self.post('menu01', {'act': ''}, ajax=False)
        inputs = Inputs(menu).values
        if 'f2' not in inputs or 'f3' not in inputs:
            raise ErroSSW('O SSW não apresentou o menu esperado após o login.')
        self.session.headers['Referer'] = BASE + '/bin/menu01'

    def extrair(self, unidade, data_final, pasta):
        LOG.info('Abrindo OP435 na unidade %s', unidade)
        text = self.post('menu01', {'act': 'TRO', 'f2': unidade, 'f3': '435'})
        if not re.search(r"ssw0103\?sequencia=435", comando(text)):
            raise ErroSSW('A OP435 não foi liberada pelo menu. Confira unidade e permissão.')
        form = self.post('ssw0103', {'sequencia': '435'})
        inputs = Inputs(form).values
        if not all(k in inputs for k in FILTROS) or 't_data_ref_fim' not in inputs:
            raise ErroSSW('O formulário da OP435 mudou ou a sessão expirou.')
        self.session.headers['Referer'] = BASE + '/bin/ssw0103'
        filtros = dict(FILTROS, t_data_ref_fim=data_final.strftime('%d%m%y'))
        LOG.info('Gerando relatório até %s; aguardando resposta', data_final.strftime('%d/%m/%Y'))
        result = self.post('ssw0103', dict(filtros, act='PES'))
        params = download_params(result)  # Nome obtido na execução atual, nunca fixo.
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%d_%H%M%S_%f')
        dest = pasta / ('op435_' + stamp + '.sswweb')
        part = dest.with_suffix('.part')
        sha = hashlib.sha256()
        total = 0
        LOG.info('Baixando arquivo bruto')
        try:
            with self.session.get(BASE + '/bin/ssw0424', params=params, stream=True,
                                  timeout=(30, self.timeout), allow_redirects=False) as r:
                r.raise_for_status()
                if r.status_code != 200:
                    raise ErroSSW('Download não retornou HTTP 200.')
                with part.open('xb') as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            sha.update(chunk)
                            total += len(chunk)
            validar_arquivo(part)
            part.replace(dest)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        metadata = {'relatorio': 'OP435', 'unidade': unidade, 'filtros': filtros,
                    'arquivo': dest.name, 'bytes': total, 'sha256': sha.hexdigest(),
                    'extraido_em': datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()}
        dest.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        LOG.info('Download validado: %s (%s bytes)', dest, total)
        return dest

