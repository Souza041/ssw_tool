"""Rotina diária ou retroativa; consulta sem envio é o padrão."""
import argparse
import logging
import sys
from datetime import date
from modules.lg_pod.config import today
from modules.lg_pod.repository import Repository
from modules.lg_pod.service import start_execution, start_group


def main():
    log = logging.getLogger('modules.lg_pod')
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S'))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    parser = argparse.ArgumentParser(description='Mapeamento POD LG')
    parser.add_argument('--perfil', choices=['SC', 'PR', 'SP', 'AMBAS', 'TODAS'], default='SC')
    parser.add_argument('--sequencial', action='store_true', help='Com AMBAS/TODAS, executar os perfis em sequência')
    parser.add_argument('--inicio', type=date.fromisoformat, default=None)
    parser.add_argument('--fim', type=date.fromisoformat, default=None)
    parser.add_argument('--executar', action='store_true', help='Mapear e enviar (exige habilitação no .env)')
    args = parser.parse_args()
    if bool(args.inicio) != bool(args.fim):
        parser.error('Informe --inicio e --fim juntos, ou omita ambos para consultar hoje.')
    try:
        if args.perfil == 'TODAS':
            ids = start_group(args.inicio or today(), args.fim or today(), dry_run=not args.executar,
                              actor='cli', background=False, parallel=not args.sequencial, profiles=('SC','PR','SP'))
        elif args.perfil == 'AMBAS':
            ids = start_group(args.inicio or today(), args.fim or today(), dry_run=not args.executar,
                              actor='cli', background=False, parallel=not args.sequencial)
        else:
            ids = [start_execution(args.inicio or today(), args.fim or today(), dry_run=not args.executar,
                                   actor='cli', background=False, profile=args.perfil)]
        result = 0
        with Repository() as repo:
            for run_id in ids:
                report = repo.report(run_id)
                run = report['run']
                print('Execução:', run_id, '| Transportador:', run['transportador'])
                print('Situação:', run['status'])
                print('Resultados:', report['counts'])
                print('Mensagem:', run['mensagem'])
                if run['status'] != 'CONCLUIDA':
                    result = 1
        return result

    except Exception as exc:
        print('Não foi possível executar LG POD (' + type(exc).__name__ + '). Verifique a configuração e o banco.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
