"""Resolver uma pendência; liberar exige consulta atual, sem Map/btnApprove."""
import argparse
from datetime import date
from modules.lg_pod.config import Settings
from modules.lg_pod.parser import eligibility
from modules.lg_pod.portal import Portal
from modules.lg_pod.repository import Repository, now


def resolve_item(repo, item_id, result, reason, settings=None, portal_factory=Portal):
    if result not in {'enviado', 'liberar'} or len(reason.strip()) < 15:
        raise ValueError('Informe resultado válido e motivo com responsável (mínimo 15 caracteres).')
    if not repo.lock():
        raise ValueError('Há execução em andamento. Aguarde antes de registrar a conferência.')
    item = repo.query('SELECT * FROM lg_pod_itens WHERE id=%s', (item_id,), one=True)
    if not item or item['etapa'] not in {'CONFERIR', 'MAPEADO', 'MAPEAMENTO_SOLICITADO', 'ENVIO_SOLICITADO'}:
        raise ValueError('Item não encontrado ou sem pendência de conferência.')
    if settings and (settings.user != item['portal_user'] or settings.carrier != item['transportador']):
        raise ValueError('Use o perfil correspondente ao usuário e transportador deste item.')
    run = repo.query('SELECT * FROM lg_pod_execucoes WHERE id=%s', (item['execucao_id'],), one=True)
    latest = repo.previous(item['portal_user'], item['transportador'], item['pod_id'], '')
    if not run or run['simulacao'] or not latest or latest['id'] != item_id:
        raise ValueError('Use o item real mais recente desse POD.')
    if run['status'] in {'EXECUTANDO', 'AGUARDANDO'}:
        raise ValueError('A execução ainda não foi encerrada. Confira o processo antes de resolver.')
    if result == 'liberar':
        if item['etapa'] == 'MAPEADO':
            raise ValueError('MAPEADO já permite retomar o envio. Não liberar para novo mapeamento.')
        settings = settings or Settings.from_env()
        settings.validate()
        if settings.user != item['portal_user'] or settings.carrier != item['transportador']:
            raise ValueError('Usuário/transportador do .env diferem do item original.')
        if run['portal_user'] != item['portal_user'] or run['transportador'] != item['transportador']:
            raise ValueError('Identidade da execução diverge do item.')
        def as_date(value):
            return date.fromisoformat(value) if isinstance(value, str) else value
        with portal_factory(settings) as portal:
            portal.login()
            rows = portal.search(as_date(run['data_inicial']), as_date(run['data_final']))
            matches = [row for row in rows if str(row.pod_id) == str(item['pod_id'])]
            if len(matches) != 1:
                raise ValueError('POD não encontrado de forma única no período original. Nada foi liberado.')
            row = matches[0]
            nf, _ = eligibility(row, settings.carrier)
            if (row.filename != item['arquivo'] or not nf or nf != item['nf']
                    or row.values.get('NOTAS_MAPEADAS', '').strip()):
                raise ValueError('Portal não confirma o mesmo POD PENDING e sem NF vinculada. Nada foi liberado.')
        message = 'Conferência atual no portal: mesmo POD PENDING, sem NF vinculada; nova tentativa liberada. '
        stage = 'LIBERADO'
    else:
        message = 'Conferência manual: envio confirmado. '
        stage = 'ENVIADO'
    # Mantém a evidência anterior no campo de auditoria, antes do motivo opcional longo.
    message += reason.strip()[:250] + ' | Etapa anterior: ' + item['etapa'] + '. ' + (item['mensagem'] or '')
    repo.assert_lock()
    repo.query('UPDATE lg_pod_itens SET etapa=%s,mensagem=%s,atualizado_em=%s WHERE id=%s AND etapa=%s',
               (stage, message[:1000], now(), item_id, item['etapa']))
    saved = repo.query('SELECT * FROM lg_pod_itens WHERE id=%s', (item_id,), one=True)
    if not saved or saved['etapa'] != stage:
        raise RuntimeError('Não foi possível confirmar o registro da conferência no banco.')
    return stage


def main():
    parser = argparse.ArgumentParser(description='Resolver pendência LG POD; liberar consulta o portal sem alterar notas')
    parser.add_argument('--perfil', choices=['SC', 'PR', 'SP'], default='SC')
    parser.add_argument('--item', type=int, required=True, help='ID do item no banco/relatório')
    parser.add_argument('--resultado', choices=['enviado', 'liberar'], required=True)
    parser.add_argument('--motivo', required=True, help='Evidência da conferência manual e identificação do responsável')
    args = parser.parse_args()
    try:
        settings = Settings.from_env(args.perfil)
        settings.validate()
        with Repository(account=(settings.user, settings.carrier)) as repo:
            stage = resolve_item(repo, args.item, args.resultado, args.motivo, settings=settings)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    print(f'Item {args.item}: {stage}. Nenhum mapeamento/envio foi realizado no portal.')


if __name__ == '__main__':
    main()
