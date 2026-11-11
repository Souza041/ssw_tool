"""Dados de apresentação derivados do relatório; não altera a execução."""
from datetime import date, datetime
import html
import re
from .repository import now

ACTIVE = {'AGUARDANDO', 'EXECUTANDO'}
LABELS = {
    'AGUARDANDO': 'Na fila', 'EXECUTANDO': 'Em andamento', 'CONCLUIDA': 'Concluída',
    'COM_PENDENCIAS': 'Concluída com pendências', 'ERRO': 'Interrompida por erro',
    'INTERROMPIDA': 'Interrompida', 'BLOQUEADA': 'Outra execução em andamento',
    'ENVIADO': 'Enviado', 'SIMULADO': 'Consultado', 'IGNORADO': 'Ignorado',
    'CONFERIR': 'Conferir no portal', 'ERRO_CONSULTA': 'Erro na consulta',
    'MAPEADO': 'Mapeado', 'CONSULTANDO': 'Consultando',
    'MAPEAMENTO_SOLICITADO': 'Mapeando', 'ENVIO_SOLICITADO': 'Enviando',
    'LIBERADO': 'Liberado após conferência',
}


def clean_message(value):
    # O portal produz referências numéricas sem ponto e vírgula (&#010).
    value = re.sub(r'&#0*(?:10|13);?', ' ', str(value or ''))
    return ' '.join(html.unescape(value).split())


def stamp(value):
    if value is None:
        return '—'
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime('%d/%m/%Y %H:%M:%S')


def clock(value):
    return stamp(value).split(' ')[-1]


def day(value):
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime('%d/%m/%Y') if value else '—'


def duration(run):
    start = run.get('iniciado_em')
    if not isinstance(start, datetime):
        return '—'
    end = run.get('finalizado_em') or now()
    seconds = max(0, int((end - start).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def overview(report):
    r, c = report['run'], report['counts']
    active = r['status'] in ACTIVE
    transient = {'CONSULTANDO', 'MAPEAMENTO_SOLICITADO', 'ENVIO_SOLICITADO', 'MAPEADO'}
    done = sum(v for k, v in c.items() if k not in transient)
    message = r.get('mensagem', '')
    match = re.search(r'comprovante\s+(\d+)/(\d+)', message)
    found = re.search(r'(\d+) comprovantes encontrados', message)
    total = int(match[2]) if match else int(found[1]) if found else None
    # Somente uma execução concluída permite inferir o total pelo relatório final.
    if r['status'] in {'CONCLUIDA', 'COM_PENDENCIAS'}:
        total = sum(c.values())
    percent = min(100, int(done * 100 / total)) if total else None
    nf = re.search(r'Consultando NF\s+(\d+)', message)
    return dict(active=active, done=done, total=total, percent=percent,
                current_nf=nf[1] if nf else '', duration=duration(r),
                position=int(match[1]) if match else None)
