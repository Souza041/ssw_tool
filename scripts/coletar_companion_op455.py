"""Homologação isolada: sessão humana, relatório OP455 e fila OP156."""
import hashlib
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import requests

OPCAO = '455 - Fretes Expedidos/Recebidos - CTRCs'

from ssw.authenticated_report_client import AuthenticatedReportClient

def coletar(session, user, unit, dias):
    from operations.op455.report import OP455Report, _OP455HTMLDiagnosticoParser
    from operations.op156.queue import OP156Queue
    from modules.metricas.processor_v34_compact import processar_op455_snapshot
    if type(dias) is not int or not 1 <= dias <= 30:
        raise ValueError('Período inválido.')
    client = AuthenticatedReportClient(session,user,unit)
    queue = OP156Queue(client)
    print('[1/5] Consultando fila anterior à geração.', flush=True)
    jobs = queue.extrair_jobs(queue.abrir_fila(retries=1), OPCAO, client.unidade, estrito=True, filtrar_usuario=True)
    previous = {j['sequencia'] for j in jobs}
    print('[2/5] Abrindo OP455 pela interface.', flush=True)
    field = session.page.locator('input[name="f3"][maxlength="3"]')
    existing = [p for p in session.context.pages if p.url == client.base_url+'/bin/ssw0230' and p.locator('form[action="/bin/ssw0230"]').count()]
    if not existing:
        field.fill('455', timeout=10000)
        field.press('Enter', timeout=10000)
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        matches = [p for p in session.context.pages if p.url.startswith(client.base_url+'/') and p.locator('form[action="/bin/ssw0230"]').count()]
        if len(matches) == 1:
            client.form_page = matches[0]
            break
        if len(matches) > 1:
            raise RuntimeError("Mais de um formulário OP455 aberto.")
        session.page.wait_for_timeout(500)
    else:
        raise RuntimeError('Formulário OP455 não confirmado.')
    print('[3/5] Solicitando relatório uma única vez.', flush=True)
    now = datetime.now()
    report = OP455Report(client)
    html = report.gerar_relatorio(now-timedelta(days=dias), now)
    parser = _OP455HTMLDiagnosticoParser()
    parser.feed(html)
    parser.close()
    if parser.localizacoes['texto_visivel']:
        raise RuntimeError('SSW retornou aviso de unidade no texto da resposta; confira OP156 antes de repetir.')
    if any(parser.localizacoes.values()):
        print('Aviso de unidade fora do texto visível; conferindo fila sem repetir geração.', flush=True)
    print('[4/5] Aguardando novo relatório na OP156.', flush=True)
    client.job = queue.aguardar_download_id(OPCAO, client.unidade, 300, 5, previous, estrito=True)
    if not re.fullmatch(r'[0-9]+',client.job):
        raise RuntimeError('Sequência inválida.')
    with TemporaryDirectory(prefix='nexus-op455-hml-') as tmp:
        file = queue.baixar_arquivo(client.job, Path(tmp), estrito=True)
        print('[5/5] Validando arquivo com parser existente.', flush=True)
        payload = processar_op455_snapshot(file)
        total = payload.get('meta',{}).get('total')
        if type(total) is not int or total < 0:
            raise RuntimeError('Total inválido.')
        return {'success':True,'mode':'validate_only','days':dias,'total':total,'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'database_writes':0}
