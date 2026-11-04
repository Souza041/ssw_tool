"""Registrar conferência humana de resultado incerto; não acessa o portal LG."""
import argparse
from modules.lg_pod.repository import Repository, now


def main():
    parser = argparse.ArgumentParser(description='Registrar resultado já conferido manualmente no portal LG')
    parser.add_argument('--item', type=int, required=True, help='ID do item no banco/relatório')
    parser.add_argument('--resultado', choices=['enviado', 'liberar'], required=True)
    parser.add_argument('--motivo', required=True, help='Evidência da conferência manual e identificação do responsável')
    args = parser.parse_args()
    if len(args.motivo.strip()) < 15:
        parser.error('Descreva a conferência e o responsável em pelo menos 15 caracteres.')
    with Repository() as repo:
        if not repo.lock():
            raise SystemExit('Há execução em andamento. Aguarde antes de registrar a conferência.')
        item = repo.query('SELECT * FROM lg_pod_itens WHERE id=%s', (args.item,), one=True)
        if not item or item['etapa'] not in {'CONFERIR', 'MAPEADO', 'MAPEAMENTO_SOLICITADO', 'ENVIO_SOLICITADO'}:
            raise SystemExit('Item não encontrado ou sem pendência de conferência.')
        run = repo.query('SELECT * FROM lg_pod_execucoes WHERE id=%s', (item['execucao_id'],), one=True)
        latest = repo.previous(item['portal_user'], item['transportador'], item['pod_id'], '')
        if run['simulacao'] or not latest or latest['id'] != args.item:
            raise SystemExit('Use o item real mais recente desse POD.')
        if args.resultado == 'liberar' and item['etapa'] == 'MAPEADO':
            raise SystemExit('MAPEADO já permite retomar o envio. Não liberar para novo mapeamento.')
        stage = 'ENVIADO' if args.resultado == 'enviado' else 'LIBERADO'
        message = ('Conferência manual: envio confirmado. ' if stage == 'ENVIADO' else
                   'Conferência manual: POD PENDING, sem mapeamento/envio; nova tentativa liberada. ')
        message += args.motivo.strip()
        # Preserva o registro anterior no próprio campo de auditoria.
        message += ' | Etapa anterior: ' + item['etapa'] + '. ' + item['mensagem']
        repo.assert_lock()
        repo.query('UPDATE lg_pod_itens SET etapa=%s,mensagem=%s,atualizado_em=%s WHERE id=%s',
                   (stage, message[:1000], now(), args.item))
        print('Conferência registrada. Nenhuma ação foi realizada no portal.')


if __name__ == '__main__':
    main()
