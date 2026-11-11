from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import hashlib
from .database import connect
from .db_worker import DatabaseWorker


def now():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


def account_lock(user):
    # A sessão pertence ao login: mesmo usuário com outro carrier não pode
    # executar simultaneamente. O nome ASCII cabe nos 64 caracteres do MySQL.
    digest = hashlib.sha256(user.strip().casefold().encode('utf-8')).hexdigest()[:40]
    return 'ssw_tool:lg_pod:user:' + digest


class Repository:
    LOCK = 'ssw_tool:lg_pod:execucao'

    def __init__(self, connection=None, *, connection_factory=None, heartbeat_interval=5.0, account=None):
        # connection é mantido apenas para compatibilidade com injeção em testes.
        factory = connection_factory or ((lambda: connection) if connection is not None else connect)
        self.account = account
        lock_name = account_lock(account[0]) if account else self.LOCK
        self.worker = DatabaseWorker(factory, lock_name, interval=heartbeat_interval)
        self.locked = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.worker.close()

    def query(self, sql, params=(), *, one=False):
        return self.worker.request('query', sql, params, one)

    def lock(self):
        self.locked = self.worker.request('lock')
        return self.locked

    def assert_lock(self):
        self.worker.request('assert')

    def create_run(self, run_id, settings, start, end, dry_run, actor):
        self.query('''INSERT INTO lg_pod_execucoes
            (id,portal_user,transportador,solicitado_por,data_inicial,data_final,simulacao,iniciado_em)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (run_id, settings.user, settings.carrier, actor[:100], start, end, dry_run, now()))

    def run_status(self, run_id, status, message='', finished=False):
        self.query('UPDATE lg_pod_execucoes SET status=%s,mensagem=%s,finalizado_em=%s WHERE id=%s',
                   (status, message[:1000], now() if finished else None, run_id))

    def interrupt_old(self, current):
        self.assert_lock()
        if not self.account:
            raise RuntimeError('Defina a conta antes de encerrar execuções anteriores.')
        self.query("""UPDATE lg_pod_execucoes SET status='INTERROMPIDA', finalizado_em=%s,
            mensagem='Processo anterior encerrado. Consultar etapas dos itens antes de retomar.'
            WHERE (status='EXECUTANDO' OR (status='AGUARDANDO' AND iniciado_em < DATE_SUB(%s, INTERVAL 10 MINUTE))) AND id<>%s
            AND portal_user=%s AND transportador=%s""", (now(), now(), current, *self.account))

    def previous(self, user, carrier, pod_id, run_id):
        return self.query('''SELECT i.* FROM lg_pod_itens i
            JOIN lg_pod_execucoes e ON e.id=i.execucao_id
            WHERE i.portal_user=%s AND i.transportador=%s AND i.pod_id=%s
              AND i.execucao_id<>%s AND e.simulacao=0
              AND i.etapa NOT IN ('IGNORADO','ERRO_CONSULTA','CONSULTANDO')
            ORDER BY i.id DESC LIMIT 1''', (user, carrier, pod_id, run_id), one=True)

    def item(self, run_id, settings, row, nf, stage, message='', invoice=None):
        if self.account and self.account != (settings.user, settings.carrier):
            raise RuntimeError('A conta do item difere da conta bloqueada para esta execução.')
        self.assert_lock()
        values = invoice.values if invoice else {}
        self.query('''INSERT INTO lg_pod_itens
            (execucao_id,portal_user,transportador,pod_id,arquivo,nf,serie,chave_nota,
             status_portal,etapa,mensagem,atualizado_em)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE nf=VALUES(nf),serie=IF(VALUES(serie)='',serie,VALUES(serie)),
            chave_nota=IF(VALUES(chave_nota)='',chave_nota,VALUES(chave_nota)),
            status_portal=VALUES(status_portal),etapa=VALUES(etapa),
            mensagem=VALUES(mensagem),atualizado_em=VALUES(atualizado_em)''',
            (run_id, settings.user, settings.carrier, row.pod_id, row.filename, nf or '',
             values.get('LD_NF_SERIE', ''), values.get('KEY_CODE', ''), row.status,
             stage, message[:1000], now()))

    def unresolved(self, user, carrier, run_id):
        # Agrupar o último evento significativo de cada POD evita a subconsulta
        # correlacionada repetida para cada linha do histórico.
        return self.query("""SELECT i.* FROM lg_pod_itens i
            JOIN (
                SELECT n.pod_id,MAX(n.id) AS latest_id FROM lg_pod_itens n
                JOIN lg_pod_execucoes e ON e.id=n.execucao_id
                WHERE n.portal_user=%s AND n.transportador=%s AND n.execucao_id<>%s
                  AND e.simulacao=0
                  AND n.etapa NOT IN ('IGNORADO','ERRO_CONSULTA','CONSULTANDO')
                GROUP BY n.pod_id
            ) latest ON latest.latest_id=i.id
            WHERE i.etapa IN ('MAPEADO','MAPEAMENTO_SOLICITADO','ENVIO_SOLICITADO','CONFERIR')""",
            (user, carrier, run_id))

    def history(self, user=None, carrier=None):
        if user is not None:
            return self.query('SELECT * FROM lg_pod_execucoes WHERE portal_user=%s AND transportador=%s '
                              'ORDER BY iniciado_em DESC LIMIT 50', (user, carrier))
        return self.query('SELECT * FROM lg_pod_execucoes ORDER BY iniciado_em DESC LIMIT 50')

    def latest_run(self, user, carrier):
        return self.query('SELECT * FROM lg_pod_execucoes WHERE portal_user=%s AND transportador=%s '
                          "ORDER BY CASE WHEN status='EXECUTANDO' THEN 0 WHEN status='AGUARDANDO' THEN 1 ELSE 2 END,"
                          'iniciado_em DESC LIMIT 1', (user, carrier), one=True)

    def activities(self, run_id):
        return self.query('SELECT * FROM lg_pod_itens WHERE execucao_id=%s '
                          'ORDER BY atualizado_em DESC,id DESC LIMIT 30', (run_id,))

    def export_ids(self, run_ids, stage='', search=''):
        ids=list(dict.fromkeys(run_ids))
        if not ids or len(ids)>200:
            raise ValueError('Selecione de 1 a 200 execuções para exportar.')
        placeholders=','.join(['%s']*len(ids))
        runs=self.query('SELECT * FROM lg_pod_execucoes WHERE id IN ('+placeholders+') ORDER BY iniciado_em,id',ids)
        if len(runs)!=len(ids):
            raise ValueError('Uma das execuções selecionadas não existe.')
        return self._export(runs,stage,search)

    def export_period(self, start, end, user=None, carrier=None):
        sql='SELECT * FROM lg_pod_execucoes WHERE iniciado_em>=%s AND iniciado_em<%s'
        params=[datetime.combine(start,time()),datetime.combine(end+timedelta(days=1),time())]
        if user is not None:
            sql+=' AND portal_user=%s AND transportador=%s';params.extend([user,carrier])
        runs=self.query(sql+' ORDER BY iniciado_em,id LIMIT 201',params)
        if len(runs)>200:
            raise ValueError('Mais de 200 execuções. Reduza o intervalo da auditoria.')
        return self._export(runs)

    def _export(self, runs, stage='', search=''):
        if not runs:return []
        ids=[r['id'] for r in runs];where='execucao_id IN ('+','.join(['%s']*len(ids))+')'
        params=list(ids)
        counts=self.query('SELECT execucao_id,etapa,COUNT(*) AS total FROM lg_pod_itens WHERE '+where+' GROUP BY execucao_id,etapa',ids)
        if stage:where+=' AND etapa=%s';params.append(stage)
        if search:
            where+=" AND (arquivo LIKE %s ESCAPE '!' OR nf LIKE %s ESCAPE '!' OR pod_id LIKE %s ESCAPE '!')"
            pattern='%'+search.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
            params.extend([pattern]*3)
        items=self.query('SELECT * FROM lg_pod_itens WHERE '+where+' ORDER BY execucao_id,id LIMIT 100001',params)
        if len(items)>100000:
            raise ValueError('Mais de 100.000 itens. Reduza os filtros; nenhum relatório parcial foi gerado.')
        bundles={r['id']:dict(run=r,counts={},items=[]) for r in runs}
        for c in counts:bundles[c['execucao_id']]['counts'][c['etapa']]=c['total']
        for item in items:bundles[item['execucao_id']]['items'].append(item)
        return list(bundles.values())

    def report(self, run_id, page=1, stage='', search=''):
        run = self.query('SELECT * FROM lg_pod_execucoes WHERE id=%s', (run_id,), one=True)
        counts = self.query('SELECT etapa,COUNT(*) AS total FROM lg_pod_itens WHERE execucao_id=%s GROUP BY etapa', (run_id,))
        where, params = 'execucao_id=%s', [run_id]
        if stage:
            where += ' AND etapa=%s'
            params.append(stage)
        if search:
            where += " AND (arquivo LIKE %s ESCAPE '!' OR nf LIKE %s ESCAPE '!' OR pod_id LIKE %s ESCAPE '!')"
            pattern = '%' + search.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            params.extend([pattern] * 3)
        total = self.query('SELECT COUNT(*) AS total FROM lg_pod_itens WHERE ' + where, params, one=True)['total']
        items = self.query('SELECT * FROM lg_pod_itens WHERE ' + where + ' ORDER BY id LIMIT 100 OFFSET %s', params + [(page-1)*100])
        return dict(run=run, counts={r['etapa']: r['total'] for r in counts}, items=items, total=total)
