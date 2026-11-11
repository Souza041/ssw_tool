from datetime import datetime
from zoneinfo import ZoneInfo
from .database import connect


def now():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)


class Repository:
    LOCK = 'ssw_tool:lg_pod:execucao'

    def __init__(self, connection=None):
        self.conn = connection or connect()
        self.locked = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            if self.locked:
                self.query('SELECT RELEASE_LOCK(%s)', (self.LOCK,))
        finally:
            self.conn.close()

    def query(self, sql, params=(), *, one=False):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                return cur.fetchone() if one else cur.fetchall()
            return cur.lastrowid

    def lock(self):
        row = self.query('SELECT GET_LOCK(%s, 0) AS ok', (self.LOCK,), one=True)
        self.locked = bool(row and row['ok'] == 1)
        return self.locked

    def assert_lock(self):
        row = self.query('SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS ok', (self.LOCK,), one=True)
        if not self.locked or not row or row['ok'] != 1:
            raise RuntimeError('Bloqueio de execução perdido; execução interrompida.')

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
        self.query("""UPDATE lg_pod_execucoes SET status='INTERROMPIDA', finalizado_em=%s,
            mensagem='Processo anterior encerrado. Consultar etapas dos itens antes de retomar.'
            WHERE (status='EXECUTANDO' OR (status='AGUARDANDO' AND iniciado_em < DATE_SUB(%s, INTERVAL 10 MINUTE))) AND id<>%s""", (now(), now(), current))

    def previous(self, user, carrier, pod_id, run_id):
        return self.query('''SELECT i.* FROM lg_pod_itens i
            JOIN lg_pod_execucoes e ON e.id=i.execucao_id
            WHERE i.portal_user=%s AND i.transportador=%s AND i.pod_id=%s
              AND i.execucao_id<>%s AND e.simulacao=0
              AND i.etapa NOT IN ('IGNORADO','ERRO_CONSULTA','CONSULTANDO')
            ORDER BY i.id DESC LIMIT 1''', (user, carrier, pod_id, run_id), one=True)

    def item(self, run_id, settings, row, nf, stage, message='', invoice=None):
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
        return self.query("""SELECT i.* FROM lg_pod_itens i
            JOIN lg_pod_execucoes e ON e.id=i.execucao_id
            WHERE i.portal_user=%s AND i.transportador=%s AND i.execucao_id<>%s
              AND e.simulacao=0
              AND i.etapa IN ('MAPEADO','MAPEAMENTO_SOLICITADO','ENVIO_SOLICITADO','CONFERIR')
              AND NOT EXISTS (
                SELECT 1 FROM lg_pod_itens n JOIN lg_pod_execucoes ne ON ne.id=n.execucao_id
                WHERE n.portal_user=i.portal_user AND n.transportador=i.transportador
                  AND n.pod_id=i.pod_id AND n.id>i.id AND ne.simulacao=0
                  AND n.etapa NOT IN ('IGNORADO','ERRO_CONSULTA','CONSULTANDO')
              )""", (user, carrier, run_id))

    def history(self):
        return self.query('SELECT * FROM lg_pod_execucoes ORDER BY iniciado_em DESC LIMIT 50')

    def report(self, run_id, page=1, stage=''):
        run = self.query('SELECT * FROM lg_pod_execucoes WHERE id=%s', (run_id,), one=True)
        counts = self.query('SELECT etapa,COUNT(*) AS total FROM lg_pod_itens WHERE execucao_id=%s GROUP BY etapa', (run_id,))
        where, params = 'execucao_id=%s', [run_id]
        if stage:
            where += ' AND etapa=%s'
            params.append(stage)
        total = self.query('SELECT COUNT(*) AS total FROM lg_pod_itens WHERE ' + where, params, one=True)['total']
        items = self.query('SELECT * FROM lg_pod_itens WHERE ' + where + ' ORDER BY id LIMIT 100 OFFSET %s', params + [(page-1)*100])
        return dict(run=run, counts={r['etapa']: r['total'] for r in counts}, items=items, total=total)
