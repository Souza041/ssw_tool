import csv
from datetime import date,datetime,timezone
from io import BytesIO,StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,mock
from openpyxl import load_workbook
from modules.lg_pod.audit import xlsx_bytes,csv_bytes,ITEM_HEADERS
from modules.lg_pod.repository import Repository
from modules.lg_pod.scheduler import previous_day,render_units,save_audit,CALENDAR
from test_routes import Repo,routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware


def bundle(status='CONCLUIDA'):
    r=Repo().report();r['run'].update(portal_user='SC_TEST',transportador='ROBR_SC',status=status,
                                    iniciado_em=datetime(2026,11,11,1),finalizado_em=datetime(2026,11,11,1,30))
    r['items'][0].update(nf='000139153',pod_id='1725985',serie='33',chave_nota='000139153|33|ROBR_SC|20261110',
                         mensagem='Sucesso &#010 &#010',status_portal='PENDING')
    return r

class ExportTests(TestCase):
    def test_xlsx_has_three_tabs_and_keeps_nf_zeroes_as_text(self):
        wb=load_workbook(BytesIO(xlsx_bytes([bundle()])))
        self.assertEqual(wb.sheetnames,['Execuções','Comprovantes','Critérios'])
        ws=wb['Comprovantes'];nf=ws.cell(2,ITEM_HEADERS.index('NF')+1)
        self.assertEqual((nf.value,nf.data_type),('000139153','s'))
        self.assertEqual(ws.freeze_panes,'A2');self.assertTrue(ws.auto_filter.ref)
        self.assertEqual(ws.cell(2,ITEM_HEADERS.index('Mensagem')+1).value,'Sucesso')
    def test_portal_formula_is_literal_in_excel_and_escaped_in_csv(self):
        b=bundle();b['items'][0]['arquivo']='=HYPERLINK("bad")'
        wb=load_workbook(BytesIO(xlsx_bytes([b])))
        cell=wb['Comprovantes'].cell(2,ITEM_HEADERS.index('Arquivo')+1)
        self.assertEqual(cell.data_type,'s')
        text=csv_bytes([b]).decode('utf-8-sig')
        row=list(csv.reader(StringIO(text),delimiter=';'))[1]
        self.assertTrue(row[ITEM_HEADERS.index('Arquivo')].startswith("'="))
    def test_csv_bom_and_all_items_no_100_row_pagination_limit(self):
        b=bundle();b['items']=b['items']*150
        payload=csv_bytes([b]);self.assertTrue(payload.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(StringIO(payload.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),151)
        self.assertEqual(rows[1][ITEM_HEADERS.index('NF')],'000139153')
    def test_empty_execution_is_still_auditable(self):
        b=bundle();b['items']=[]
        wb=load_workbook(BytesIO(xlsx_bytes([b])))
        self.assertEqual(wb['Execuções'].max_row,2);self.assertEqual(wb['Comprovantes'].max_row,1)
        self.assertIn('Sem itens',csv_bytes([b]).decode('utf-8-sig'))
    def test_no_runs_produces_a_valid_workbook(self):
        wb=load_workbook(BytesIO(xlsx_bytes([])))
        self.assertEqual(len(wb.sheetnames),3)
    def test_export_queries_are_parameterized_and_filtered(self):
        repo=Repository.__new__(Repository);b=bundle();calls=[]
        def query(sql,params=()):
            calls.append((sql,params))
            if sql.startswith('SELECT * FROM lg_pod_execucoes'):return [b['run']]
            if 'COUNT(*)' in sql:return [dict(execucao_id=b['run']['id'],etapa='SIMULADO',total=1)]
            return [dict(b['items'][0],execucao_id=b['run']['id'])]
        repo.query=query
        result=repo.export_ids([b['run']['id']],'SIMULADO',"NF_%'")
        sql,params=calls[-1]
        self.assertNotIn("NF_%'",sql);self.assertIn('LIMIT 100001',sql)
        self.assertEqual(params[1],'SIMULADO');self.assertEqual(result[0]['counts'],{'SIMULADO':1})
    def test_period_is_execution_start_date_including_entire_last_day(self):
        repo=Repository.__new__(Repository);repo.query=mock.Mock(return_value=[])
        repo.export_period(date(2026,11,10),date(2026,11,11),'SC_TEST','ROBR_SC')
        sql,params=repo.query.call_args.args
        self.assertIn('iniciado_em>=%s AND iniciado_em<%s',sql)
        self.assertEqual(params,[datetime(2026,11,10),datetime(2026,11,12),'SC_TEST','ROBR_SC'])
    def test_large_export_is_refused_without_silent_truncation(self):
        repo=Repository.__new__(Repository);b=bundle()
        repo.query=mock.Mock(side_effect=[[b['run']],[],[dict(execucao_id=b['run']['id'])]*100001])
        with self.assertRaisesRegex(ValueError,'100.000'):repo.export_ids([b['run']['id']])

class ExportRoutesTests(TestCase):
    def setUp(self):
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test-only');app.include_router(routes.router)
        self.http=TestClient(app)
        class ExportRepo(Repo):
            def export_ids(self,*args):return [bundle()]
            def export_period(self,*args):return [bundle()]
        self.repo=ExportRepo
    def get(self,url):
        with mock.patch.object(routes,'client',return_value=object()),mock.patch.object(routes,'Repository',self.repo):return self.http.get(url)
    def test_download_requires_authentication(self):
        with mock.patch.object(routes,'client',return_value=None),mock.patch.object(routes,'Repository') as repo:
            r=self.http.get('/lg-pod/relatorio?execucao=run')
        self.assertEqual(r.status_code,401);repo.assert_not_called()
    def test_excel_download_is_read_only(self):
        with mock.patch.object(routes,'start_execution') as start:
            r=self.get('/lg-pod/relatorio?execucao=run&formato=xlsx')
        self.assertEqual(r.status_code,200);self.assertIn('attachment',r.headers['content-disposition'])
        self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertIn('Comprovantes',load_workbook(BytesIO(r.content)).sheetnames);start.assert_not_called()
    def test_period_csv_download(self):
        r=self.get('/lg-pod/relatorio?inicio=2026-11-10&fim=2026-11-11&formato=csv')
        self.assertEqual(r.status_code,200);self.assertIn('000139153',r.text)
    def test_invalid_selection_or_format_is_refused(self):
        for query in ['execucao=run&formato=html','execucao=run&inicio=2026-11-10','inicio=2026-11-11','inicio=2026-11-11&fim=2026-11-10']:
            with self.subTest(query=query):self.assertEqual(self.get('/lg-pod/relatorio?'+query).status_code,400)
    def test_database_error_does_not_expose_secret(self):
        class Failed(self.repo):
            def export_ids(self,*args):raise RuntimeError('private-secret')
        self.repo=Failed;r=self.get('/lg-pod/relatorio?execucao=run')
        self.assertEqual(r.status_code,503);self.assertNotIn('private-secret',r.text)

class ScheduleTests(TestCase):
    def test_d_minus_one_uses_brasilia_even_when_utc_is_next_day(self):
        self.assertEqual(previous_day(datetime(2026,11,11,1,30,tzinfo=timezone.utc)),date(2026,11,9))
        self.assertEqual(previous_day(datetime(2026,11,11,4,tzinfo=timezone.utc)),date(2026,11,10))
    def test_month_year_and_leap_boundaries(self):
        self.assertEqual(previous_day(datetime(2026,1,1,4,tzinfo=timezone.utc)),date(2025,12,31))
        self.assertEqual(previous_day(datetime(2024,3,1,4,tzinfo=timezone.utc)),date(2024,2,29))
        with self.assertRaises(ValueError):previous_day(datetime(2026,11,11))
    def test_timer_is_daily_fixed_zone_and_no_batch_restart(self):
        service,timer=render_units('/home/automacao/bots/ssw_tool','/home/automacao/venv/bin/python','automacao')
        self.assertIn('OnCalendar='+CALENDAR,timer);self.assertIn('Persistent=false',timer)
        self.assertIn('Restart=no',service);self.assertIn('User=automacao',service)
        self.assertIn('--executar',service);self.assertNotIn('PASSWORD',service)
    def test_unit_values_reject_injection(self):
        with self.assertRaises(ValueError):render_units('/tmp/a\nExecStart=bad','/usr/bin/python3','automacao')
    def test_artifacts_and_manifest_hashes_are_saved_together(self):
        import hashlib
        with TemporaryDirectory() as tmp:
            path=save_audit([bundle()],date(2026,11,10),tmp)
            manifest=json.loads((path/'manifesto.json').read_text())
            self.assertEqual(manifest['referencia'],'2026-11-10')
            for name,digest in manifest['sha256'].items():self.assertEqual(hashlib.sha256((path/name).read_bytes()).hexdigest(),digest)
            self.assertFalse(list(path.glob('*.tmp')))

class DailyRunnerTests(TestCase):
    def test_runner_uses_only_yesterday_and_all_profiles(self):
        from scripts import executar_lg_pod_diario as daily
        class DailyRepo(Repo):
            def export_ids(self,ids):return [bundle()]
        with TemporaryDirectory() as tmp,mock.patch.object(daily,'ROOT',Path(tmp)), \
             mock.patch.object(daily,'previous_day',return_value=date(2026,11,10)), \
             mock.patch.object(daily,'preflight'),mock.patch.object(daily,'start_group',return_value=['run']) as start, \
             mock.patch.object(daily,'Repository',DailyRepo),mock.patch.object(daily,'save_audit',return_value=Path(tmp)):
            self.assertEqual(daily.main(['--executar']),0)
        self.assertEqual(start.call_args.args,(date(2026,11,10),date(2026,11,10)))
        self.assertEqual(start.call_args.kwargs['profiles'],('SC','PR','SP'))
        self.assertFalse(start.call_args.kwargs['background']);self.assertFalse(start.call_args.kwargs['dry_run'])
    def test_check_does_not_start_a_portal_or_create_a_run(self):
        from scripts import executar_lg_pod_diario as daily
        with TemporaryDirectory() as tmp,mock.patch.object(daily,'ROOT',Path(tmp)),mock.patch.object(daily,'preflight'), \
             mock.patch.object(daily,'start_group') as start:
            self.assertEqual(daily.main(['--check','--executar']),0)
        start.assert_not_called()

class DailyOutcomeTests(TestCase):
    def test_pending_and_technical_failure_have_distinct_exit_codes(self):
        from scripts import executar_lg_pod_diario as daily
        for status,expected in [('COM_PENDENCIAS',2),('ERRO',1)]:
            with self.subTest(status=status),TemporaryDirectory() as tmp:
                class DailyRepo(Repo):
                    def export_ids(self,ids):return [bundle(status)]
                with mock.patch.object(daily,'ROOT',Path(tmp)),mock.patch.object(daily,'preflight'), \
                     mock.patch.object(daily,'start_group',return_value=['run']),mock.patch.object(daily,'Repository',DailyRepo), \
                     mock.patch.object(daily,'save_audit',return_value=Path(tmp)) as save:
                    self.assertEqual(daily.main(['--executar']),expected)
                    save.assert_called_once()
    def test_bad_preflight_never_starts_a_batch(self):
        from scripts import executar_lg_pod_diario as daily
        with mock.patch.object(daily,'preflight',side_effect=ValueError('Configuração inválida')), \
             mock.patch.object(daily,'start_group') as start:
            self.assertEqual(daily.main(['--executar']),1)
        start.assert_not_called()
