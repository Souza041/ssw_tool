"""Testes HTTP isolados: sem login SSW, banco ou portal reais."""
from datetime import datetime, date
from types import SimpleNamespace
from unittest import TestCase, mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
import sys
from types import ModuleType
from jinja2 import ChoiceLoader, DictLoader
# O pacote de atualização não contém o login SSW. Só nos testes, usar uma
# dependência mínima se o aplicativo completo não estiver presente.
try:
    import web.session_store
except ModuleNotFoundError:
    web = ModuleType('web')
    web.__path__ = []
    session = ModuleType('web.session_store')
    session.obter_client = lambda request: None
    sys.modules['web'] = web
    sys.modules['web.session_store'] = session
from modules.lg_pod import routes
routes.templates.env.loader = ChoiceLoader([routes.templates.env.loader,
    DictLoader({'base.html': '<html><body>{% block content %}{% endblock %}</body></html>'})])


class Repo:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def history(self): return []
    def activities(self, *args): return self.report()['items']
    def report(self, *args):
        return dict(run=dict(id='test-run', status='CONCLUIDA', data_inicial=date(2026,11,3),
                    data_final=date(2026,11,4), simulacao=True, solicitado_por='teste',mensagem='Concluído'),
                    counts={'SIMULADO':1}, total=1,
                    items=[dict(id=1,pod_id='1',arquivo='<script>unsafe</script>.png',nf='000138810',serie='33',
                    etapa='SIMULADO',mensagem='NF encontrada.',atualizado_em=datetime(2026,11,4,8))])


class RoutesTests(TestCase):
    def setUp(self):
        app=FastAPI()
        app.add_middleware(SessionMiddleware, secret_key='test-secret-only')
        app.include_router(routes.router)
        self.client=TestClient(app)

    def test_expired_session_redirects(self):
        with mock.patch.object(routes,'client', return_value=None):
            response=self.client.get('/lg-pod',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/login')

    def test_report_renders_and_escapes_portal_content(self):
        with mock.patch.object(routes,'client',return_value=object()), mock.patch.object(routes,'Repository',Repo):
            response=self.client.get('/lg-pod?execucao=test-run')
        self.assertEqual(response.status_code,200)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;.png',response.text)
        self.assertNotIn('<script>unsafe</script>',response.text)
        self.assertIn('000138810',response.text)

    def test_csrf_missing_denies_start(self):
        with mock.patch.object(routes,'client',return_value=object()):
            response=self.client.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',
                         data_final='2026-11-04',modo='executar',csrf_token='wrong'))
        self.assertEqual(response.status_code,403)

    def test_valid_request_starts_with_current_user(self):
        with mock.patch.object(routes,'client',return_value=SimpleNamespace(usuario='operador')), \
             mock.patch.object(routes,'Repository',Repo), \
             mock.patch.object(routes,'start_execution',return_value='run-1') as start:
            page=self.client.get('/lg-pod')
            import re
            csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
            response=self.client.post('/lg-pod/executar', data=dict(data_inicial='2026-11-03',
                 data_final='2026-11-04',modo='simular',csrf_token=csrf),follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/lg-pod?execucao=run-1&operacao=SC')
        self.assertEqual(start.call_args.kwargs,dict(dry_run=True,actor='operador',profile='SC'))
