"""Retoma exclusivamente um download OP455 existente, sem gerar relatório."""
import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from ssw.authenticated_report_client import AuthenticatedReportClient
from operations.op156.queue import OP156Queue, _normalizar_estado

OPCAO = '455 - Fretes Expedidos/Recebidos - CTRCs'

def baixar(session, user, unit, args):
    if len(args) != 1 or not re.fullmatch(r'[0-9]{1,20}', args[0]):
        raise ValueError('Sequência inválida.')
    seq = args[0]
    client = AuthenticatedReportClient(session, user, unit)
    queue = OP156Queue(client)
    jobs = queue.extrair_jobs(queue.abrir_fila(retries=1), OPCAO, client.unidade, estrito=True, filtrar_usuario=True)
    matches = [j for j in jobs if j['sequencia'] == seq]
    if len(matches) != 1:
        raise ValueError('Sequência não encontrada na fila do operador e unidade.')
    job = matches[0]
    if _normalizar_estado(job['situacao']) not in {'concluido', 'concluida'} or _normalizar_estado(job['acao']) not in {'baixar', 'dow'+seq} or job['download_id'] != seq:
        raise ValueError('Relatório não está disponível para download.')
    client.job = seq
    print('OP156: relatório confirmado; iniciando download original.', flush=True)
    with TemporaryDirectory(prefix='nexus-op455-retomada-') as tmp:
        file = queue.baixar_arquivo(seq, Path(tmp), estrito=True)
        data = file.read_bytes()
        print('Download validado pela OP156.', flush=True)
        from modules.metricas.processor_v34_compact import processar_op455_snapshot
        result = processar_op455_snapshot(file)
        total = result.get('meta', {}).get('total')
        if type(total) is not int or total < 0:
            raise RuntimeError('Total inválido no parser.')
        return {'success': True, 'sequencia': seq, 'total': total, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'database_writes': 0}
