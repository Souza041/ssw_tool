"""Contrato do painel: sessão, escape, filtros, progresso e indisponibilidade."""
from datetime import date, datetime
from unittest import TestCase, mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from test_routes import Repo, routes
from modules.lg_pod.presentation import overview, clean_message
from modules.lg_pod.repository import Repository


class ProgressTests(TestCase):
    def report(self, status, message='', counts=None):
        return dict(run=dict(status=status, mensagem=message), counts=counts or {})
    def test_progress_tracks_finished_items_not_current_position(self):
        v = overview(self.report('EXECUTANDO','Consultando NF 000139098 — comprovante 5/20.',
                                 {'ENVIADO':2,'IGNORADO':2,'CONSULTANDO':1}))
        self.assertEqual((v['done'],v['total'],v['percent'],v['current_nf']), (4,20,20,'000139098'))
    def test_unknown_total_is_not_a_fake_percentage(self):
        v = overview(self.report('EXECUTANDO','Autenticando no portal LG.'))
        self.assertIsNone(v['percent'])
        self.assertIsNone(v['total'])
    def test_finished_and_error_do_not_share_a_fake_100_percent(self):
        self.assertEqual(overview(self.report('CONCLUIDA',counts={'ENVIADO':67,'IGNORADO':24}))['percent'],100)
        v = overview(self.report('ERRO','Falha na consulta',{'ENVIADO':4,'ERRO_CONSULTA':1}))
        self.assertFalse(v['active'])
        self.assertIsNone(v['percent'])
    def test_markup_is_cleaned_but_still_escaped_in_views(self):
        self.assertEqual(clean_message('Sucesso &#010 &#010 — enviado.'),'Sucesso — enviado.')
        self.assertEqual(clean_message('&lt;script&gt;'),' <script>'.strip())


class PanelRoutesTests(TestCase):
    def setUp(self):
        app=FastAPI()
        app.add_middleware(SessionMiddleware,secret_key='test-only')
        app.include_router(routes.router)
        self.http=TestClient(app)
    def get(self, url, repo=Repo):
        with mock.patch.object(routes,'client',return_value=object()), mock.patch.object(routes,'Repository',repo):
            return self.http.get(url)
    def test_state_requires_login_without_redirect_html(self):
        with mock.patch.object(routes,'client',return_value=None), mock.patch.object(routes,'Repository') as repo:
            response=self.http.get('/lg-pod/estado?execucao=run')
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.headers['cache-control'],'no-store')
        repo.assert_not_called()
    def test_read_only_endpoint_renders_escaped_parts(self):
        with mock.patch.object(routes,'start_execution') as start:
            response=self.get('/lg-pod/estado?execucao=run')
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['active'])
        self.assertEqual(set(response.json()['parts']),{'summary','items','activity','history','accounts'})
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;',response.json()['parts']['items'])
        self.assertNotIn('<script>unsafe</script>',str(response.json()))
        start.assert_not_called()
    def test_filters_are_bounded_and_forwarded_to_repository(self):
        called=[]
        class FilterRepo(Repo):
            def report(self,*args):
                called.append(args)
                return super().report()
        self.get('/lg-pod/estado?execucao=run&page=-3&etapa=ENVIADO&busca=000139098',FilterRepo)
        self.assertEqual([args for args in called if args],[('run',1,'ENVIADO','000139098')])
    def test_missing_run_is_404(self):
        class MissingRepo(Repo):
            def report(self,*args): return dict(run=None)
        self.assertEqual(self.get('/lg-pod/estado?execucao=missing',MissingRepo).status_code,404)
    def test_database_failure_is_503_and_omits_raw_details(self):
        class FailedRepo(Repo):
            def report(self,*args): raise RuntimeError('private-password')
        response=self.get('/lg-pod/estado?execucao=run',FailedRepo)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private-password',response.text)
    def test_initial_page_has_filters_and_no_full_page_reload(self):
        response=self.get('/lg-pod?execucao=run&etapa=SIMULADO&busca=000138810&page=2')
        self.assertEqual(response.status_code,200)
        self.assertIn('value="000138810"',response.text)
        self.assertIn('Atividades recentes',response.text)
        self.assertNotIn('location.reload',response.text)
        self.assertIn("cache:'no-store'",response.text)
    def test_cleaned_portal_message_cannot_become_markup(self):
        class MessageRepo(Repo):
            def report(self,*args):
                r=super().report()
                r['items'][0]['mensagem']='&#60;img src=x onerror=alert(1)&#62; &#010'
                return r
        response=self.get('/lg-pod/estado?execucao=run',MessageRepo)
        self.assertNotIn('<img',response.json()['parts']['items'])
        self.assertIn('&lt;img',response.json()['parts']['items'])


class QueryTests(TestCase):
    def test_search_is_parameterized_and_escapes_like_wildcards(self):
        repo=Repository.__new__(Repository)
        calls=[]
        def query(sql,params=(),one=False):
            calls.append((sql,params))
            if 'COUNT(*) AS total' in sql and 'GROUP BY' not in sql: return {'total':0}
            return []
        repo.query=query
        repo.report('run',2,'ENVIADO',"NF_%'!")
        sql,params=calls[-1]
        self.assertNotIn("NF_%'!",sql)
        self.assertIn("ESCAPE '!'",sql)
        self.assertEqual(params[2:5],["%NF!_!%'!!%"]*3)
        self.assertEqual(params[-1],100)
    def test_activities_are_independent_of_table_filter(self):
        repo=Repository.__new__(Repository)
        repo.query=mock.Mock(return_value=[])
        repo.activities('run')
        sql,params=repo.query.call_args.args
        self.assertIn('ORDER BY atualizado_em DESC,id DESC LIMIT 30',sql)
        self.assertEqual(params,('run',))
