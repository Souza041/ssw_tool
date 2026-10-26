"""Atualiza bases LG e gera uma cópia da planilha. Simulação por padrão."""
import argparse
import json
import os
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ENCERRADAS = {"concluida", "sem_dados"}


def lacunas(inicio, fim, intervalos):
    """Complemento de intervalos inclusivos; não usa apenas MAX(data)."""
    atual = inicio
    for ini, final in sorted(intervalos):
        if final < atual:
            continue
        if ini > fim:
            break
        if ini > atual:
            yield atual, min(fim, ini - timedelta(days=1))
        atual = max(atual, final + timedelta(days=1))
        if atual > fim:
            return
    if atual <= fim:
        yield atual, fim


def salvar(path, dados):
    temporario = path.with_suffix('.part')
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding='utf-8')
    temporario.replace(path)


@contextmanager
def trava_local():
    pasta = ROOT / 'data' / 'documentos_lg'
    pasta.mkdir(parents=True, exist_ok=True)
    with (pasta / 'incremental.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def listar_e_planejar(inicio, fim):
    from .executar_lote_historico import consultar_tarefas
    from .planejar_historico import PERFIS, preparar
    tarefas = consultar_tarefas()
    # Falhas/interrupções precisam de conferência antes de novas solicitações.
    bloqueadas = [t for t in tarefas if t['status'] not in ENCERRADAS | {'pendente'}]
    if bloqueadas:
        raise RuntimeError('Tarefas bloqueadas: ' + ', '.join(
            f"{t['id']} ({t['status']})" for t in bloqueadas))
    # Valida o perfil também das tarefas encerradas usadas como cobertura.
    from .database import conectar
    from .executar_historico import carregar
    conexao = conectar()
    try:
        with conexao.cursor() as cursor:
            for tarefa in tarefas:
                if tarefa['relatorio'] in PERFIS:
                    carregar(cursor, tarefa['id'])
        conexao.rollback()
    finally:
        conexao.close()
    novas = []
    for relatorio in PERFIS:
        intervalos = [(t['inicio_periodo'], t['fim_periodo']) for t in tarefas
                      if t['relatorio'] == relatorio
                      and t['status'] in ENCERRADAS | {'pendente'}]
        for ini, final in lacunas(inicio, fim, intervalos):
            novas.extend(t for t in preparar(ini, final) if t[0] == relatorio)
    pendentes = [t for t in tarefas if t['status'] == 'pendente'
                 and t['inicio_periodo'] <= fim and t['fim_periodo'] >= inicio]
    if any(t['inicio_periodo'] < inicio or t['fim_periodo'] > fim for t in pendentes):
        raise RuntimeError('Uma tarefa pendente atravessa o intervalo pedido. Confira antes de continuar.')
    return novas, pendentes


def atualizar_op435(fim, timeout, pasta):
    from ssw.client import SSWClient
    from .op435 import BASE, Extrator, FILTROS
    from .filtro import filtrar
    from .service import validar_download
    from .retry_importacao import importar_com_retry
    client = SSWClient()
    try:
        client.timeout = timeout
        if client.base_url.rstrip('/') != BASE:
            raise ValueError('Servidor SSW diferente do homologado.')
        if not all((client.dominio, client.cpf, client.usuario, client.senha)):
            raise ValueError('Preencha as credenciais SSW no .env.')
        extrator = Extrator(session=client.session, timeout=timeout)
        extrator.login(client.dominio, client.cpf, client.usuario, client.senha)
        bruto = validar_download(extrator.extrair('CWB', fim, pasta))
        filtrado, resumo = filtrar(bruto)
        validar_download(filtrado)
        print(f"OP435: {resumo['registros_mantidos']} registros LG.", flush=True)
        return importar_com_retry('OP435', filtrado,
            datetime.strptime(FILTROS['t_data_ref_ini'], '%d%m%y').date().isoformat(),
            fim.isoformat(), tipo='incremental')
    finally:
        client.session.close()


def executar(args):
    from .planejar_atualizacao_planilha import ler_planilha, planejar
    from .aplicar_plano_planilha import aplicar
    from .planejar_historico import gravar
    from .executar_historico import executar as executar_tarefa
    from .executar_lote_historico import consultar_tarefas

    planilha = Path(args.planilha).resolve(strict=True)
    if planilha.suffix.lower() != '.xlsx':
        raise ValueError('Informe uma planilha .xlsx.')
    ler_planilha(planilha)  # valida layout antes de solicitar relatórios
    novas, pendentes = listar_e_planejar(args.inicio_base, args.fim)
    print(json.dumps({'fim': str(args.fim), 'novas_tarefas': [
        {'relatorio': t[0], 'inicio': str(t[2]), 'fim': str(t[3])} for t in novas],
        'pendentes': [t['id'] for t in pendentes], 'renovar_op435': True},
        ensure_ascii=False, indent=2), flush=True)
    if not args.executar:
        print('Simulação: nenhum acesso ao SSW, nenhuma gravação no banco ou na planilha.')
        return

    if args.retomar:
        pasta = Path(args.retomar).resolve(strict=True)
        estado = json.loads((pasta / 'execucao.json').read_text(encoding='utf-8'))
        if (estado['fim'] != str(args.fim) or estado['inicio_base'] != str(args.inicio_base)
                or estado['planilha'] != str(planilha)):
            raise ValueError('A retomada exige a mesma planilha e o mesmo intervalo.')
        if estado['etapa'] in {'op435_iniciada', 'planilha_iniciada'}:
            raise RuntimeError('Etapa anterior sem confirmação. Confira recibos e arquivos antes de nova execução.')
        if estado['etapa'] == 'concluida':
            print(json.dumps(estado, ensure_ascii=False, indent=2))
            return
    else:
        pasta = ROOT / 'data' / 'documentos_lg' / 'incremental' / uuid.uuid4().hex
        pasta.mkdir(parents=True, exist_ok=False)
        estado = {'fim': str(args.fim), 'inicio_base': str(args.inicio_base),
                  'planilha': str(planilha), 'etapa': 'preparada'}
        salvar(pasta / 'execucao.json', estado)
    print(f'Pasta da execução: {pasta}', flush=True)

    if estado['etapa'] == 'preparada':
        estado['etapa'] = 'op435_iniciada'
        salvar(pasta / 'execucao.json', estado)
        op435 = pasta / 'op435'
        op435.mkdir(exist_ok=False)
        estado['op435'] = atualizar_op435(args.fim, args.timeout, op435)
        estado['etapa'] = 'op435_concluida'
        salvar(pasta / 'execucao.json', estado)

    gravar(novas)
    # Executa somente IDs do intervalo; não consome uma fila global com limite.
    tarefas = [t for t in consultar_tarefas() if t['status'] == 'pendente'
               and args.inicio_base <= t['inicio_periodo'] <= t['fim_periodo'] <= args.fim]
    for tarefa in tarefas:
        executar_tarefa(tarefa['id'], aplicar=True, timeout=args.timeout, tipo='incremental')
        atual = next(t for t in consultar_tarefas() if t['id'] == tarefa['id'])
        if atual['status'] not in ENCERRADAS:
            raise RuntimeError('Tarefa sem encerramento confirmado.')
    faltantes, pendentes = listar_e_planejar(args.inicio_base, args.fim)
    if faltantes or pendentes:
        raise RuntimeError('Cobertura ainda incompleta. Planilha não será gerada.')
    estado['etapa'] = 'bases_concluidas'
    salvar(pasta / 'execucao.json', estado)
    plano = planejar(planilha)
    estado['plano'] = str(plano)
    estado['etapa'] = 'planilha_iniciada'
    salvar(pasta / 'execucao.json', estado)
    estado['resultado'] = aplicar(plano, executar=True)
    estado['etapa'] = 'concluida'
    salvar(pasta / 'execucao.json', estado)
    print(json.dumps(estado, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--planilha', required=True)
    parser.add_argument('--inicio-base', type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument('--fim', type=date.fromisoformat)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--executar', action='store_true')
    parser.add_argument('--retomar', help='Pasta de uma execução com OP435 confirmada.')
    args = parser.parse_args()
    ontem = datetime.now(ZoneInfo('America/Sao_Paulo')).date() - timedelta(days=1)
    args.fim = args.fim or ontem
    if not args.inicio_base <= args.fim <= ontem:
        parser.error('Use início até fim e fim até ontem.')
    if not 30 <= args.timeout <= 1800:
        parser.error('Timeout deve estar entre 30 e 1800 segundos.')
    if args.retomar and not args.executar:
        parser.error('--retomar exige --executar.')
    if args.executar:
        with trava_local():
            executar(args)
    else:
        executar(args)


if __name__ == '__main__':
    main()
