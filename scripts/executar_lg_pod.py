"""Rotina diária ou retroativa; consulta sem envio é o padrão."""
import argparse
from datetime import date
from modules.lg_pod.config import today
from modules.lg_pod.repository import Repository
from modules.lg_pod.service import start_execution


def main():
    parser = argparse.ArgumentParser(description='Mapeamento POD LG')
    parser.add_argument('--inicio', type=date.fromisoformat, default=None)
    parser.add_argument('--fim', type=date.fromisoformat, default=None)
    parser.add_argument('--executar', action='store_true', help='Mapear e enviar (exige habilitação no .env)')
    args = parser.parse_args()
    if bool(args.inicio) != bool(args.fim):
        parser.error('Informe --inicio e --fim juntos, ou omita ambos para consultar hoje.')
    try:
        run_id = start_execution(args.inicio or today(), args.fim or today(),
                                 dry_run=not args.executar, actor='cli', background=False)
        with Repository() as repo:
            report = repo.report(run_id)
        print('Execução:', run_id)
        print('Situação:', report['run']['status'])
        print('Resultados:', report['counts'])
        return 0 if report['run']['status'] == 'CONCLUIDA' else 1
    except Exception as exc:
        print('Não foi possível executar LG POD (' + type(exc).__name__ + '). Verifique a configuração e o banco.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
