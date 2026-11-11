"""Entrada do timer: sempre D-1 em Brasília; três contas; exportação ao final."""
import argparse
from datetime import date,datetime
import logging
from pathlib import Path
from modules.lg_pod.config import Settings,PROFILES,ROOT
from modules.lg_pod.db_worker import safe_error_message
from modules.lg_pod.repository import Repository
from modules.lg_pod.scheduler import previous_day,ZONE,save_audit
from modules.lg_pod.service import start_group


def preflight(real):
    import openpyxl
    settings=[Settings.from_env(code) for code in PROFILES]
    for item in settings:item.validate(real=real)
    if len({s.user.casefold() for s in settings})!=len(settings):
        raise ValueError('As três operações devem usar logins distintos.')
    with Repository() as repo:
        repo.query('SELECT id,portal_user,transportador,simulacao,iniciado_em FROM lg_pod_execucoes LIMIT 0')
        repo.query('SELECT id,execucao_id,etapa,mensagem FROM lg_pod_itens LIMIT 0')
    return settings


def main(argv=None):
    parser=argparse.ArgumentParser(description='LG POD diário: D-1 em Brasília, SC/PR/SP em paralelo')
    parser.add_argument('--executar',action='store_true',help='Mapear/enviar; exige as três flags no .env')
    parser.add_argument('--check',action='store_true',help='Validar configuração, banco e exportação sem entrar no portal')
    parser.add_argument('--referencia',type=date.fromisoformat,help='Recuperação manual de um dia passado; padrão: ontem')
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    try:
        reference=args.referencia or previous_day()
        if reference>=datetime.now(ZONE).date():raise ValueError('A referência diária precisa ser anterior a hoje em Brasília.')
        preflight(args.executar)
        directory=ROOT/'downloads'/'lg_pod'
        directory.mkdir(parents=True,exist_ok=True,mode=0o750)
        if args.check:
            # Confirmar permissão de escrita sem criar execução nem acessar o portal.
            from tempfile import TemporaryFile
            with TemporaryFile(dir=directory) as check:check.write(b'check')
            print(f'LG POD: preflight OK; SC/PR/SP; referência {reference}; nenhum portal acessado.')
            return 0
        ids=start_group(reference,reference,dry_run=not args.executar,actor='timer-01h',
                        background=False,parallel=True,profiles=PROFILES)
        with Repository() as repo:bundles=repo.export_ids(ids)
        destination=save_audit(bundles,reference,directory)
        print('LG POD: auditoria salva em',destination)
        statuses={b['run']['status'] for b in bundles}
        if statuses <= {'CONCLUIDA'}:return 0
        if statuses <= {'CONCLUIDA','COM_PENDENCIAS'}:
            logging.warning('LG POD: concluído com pendências; conferir o relatório de auditoria.')
            return 2
        return 1
    except Exception as exc:
        print('LG POD diário: falha:',type(exc).__name__,safe_error_message(exc),flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
