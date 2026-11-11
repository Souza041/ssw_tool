"""SC/PR: credenciais, locks, histórico e sessões concorrentes sem rede real."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest import TestCase, mock
from urllib.parse import parse_qs, urlsplit
from modules.lg_pod.config import Settings
from modules.lg_pod.repository import Repository, account_lock
from modules.lg_pod.db_worker import safe_error_message
from modules.lg_pod import service
from modules.lg_pod.portal import Portal
from test_http_portal import ReplaySession, SETTINGS, PERIOD
from test_lg_pod import Repo as FlowRepo

SC=replace(SETTINGS,user='SC_LOGIN',password='SC_SECRET',operation='SC')
PR=replace(SETTINGS,user='PR_LOGIN',password='PR_SECRET',carrier='ROBR_PR',operation='PR')

class ConfigTests(TestCase):
    def load(self, code, env):
        with mock.patch.dict(os.environ,env,clear=True),mock.patch('modules.lg_pod.config.load_env'):
            return Settings.from_env(code)
    def test_legacy_sc_keeps_working(self):
        s=self.load('SC',{'LG_POD_USER':'old-sc','LG_POD_PASSWORD':'old-secret','LG_POD_CARRIER':'ROBR_SC','LG_POD_ENVIO_HABILITADO':'true'})
        self.assertEqual((s.user,s.password,s.carrier,s.send_enabled,s.operation),('old-sc','old-secret','ROBR_SC',True,'SC'))
    def test_pr_never_inherits_sc_credentials_carrier_or_send_flag(self):
        s=self.load('PR',{'LG_POD_USER':'sc','LG_POD_PASSWORD':'secret','LG_POD_CARRIER':'ROBR_SC','LG_POD_ENVIO_HABILITADO':'true'})
        self.assertEqual((s.user,s.password,s.carrier,s.send_enabled),('','','ROBR_PR',False))
        with self.assertRaises(ValueError):s.validate()
    def test_explicit_sc_credentials_are_an_atomic_pair(self):
        s=self.load('SC',{'LG_POD_USER':'old','LG_POD_PASSWORD':'old-secret','LG_POD_SC_USER':'new'})
        self.assertEqual((s.user,s.password),('new',''))
    def test_pr_overrides_are_local_without_changing_environment(self):
        env={'LG_POD_USER':'sc','LG_POD_PASSWORD':'scpass','LG_POD_PR_USER':'pr','LG_POD_PR_PASSWORD':'prpass',
             'LG_POD_PR_ENVIO_HABILITADO':'true','LG_POD_TIMEOUT_MS':'60000','LG_POD_PR_TIMEOUT_MS':'30000',
             'LG_POD_PROXY_SERVER':'http://proxy.invalid:3128','LG_POD_READ_ATTEMPTS':'4'}
        with mock.patch.dict(os.environ,env,clear=True),mock.patch('modules.lg_pod.config.load_env'):
            before=dict(os.environ)
            sc,pr=Settings.from_env(),Settings.from_env('PR')
            self.assertEqual(dict(os.environ),before)
        self.assertEqual((sc.user,sc.timeout_ms),('sc',60000))
        self.assertEqual((pr.user,pr.password,pr.timeout_ms,pr.send_enabled),('pr','prpass',30000,True))
        self.assertEqual(pr.proxy_server,sc.proxy_server)
    def test_invalid_profile_is_rejected(self):
        with self.assertRaises(ValueError):self.load('XX',{})
    def test_profile_passwords_are_redacted_from_database_diagnostics(self):
        with mock.patch.dict(os.environ,{'LG_POD_PR_PASSWORD':'PR_SECRET','LG_POD_SC_PASSWORD':'SC_SECRET'}):
            detail=safe_error_message(RuntimeError('SC_SECRET PR_SECRET'))
        self.assertNotIn('SC_SECRET',detail);self.assertNotIn('PR_SECRET',detail)


class LockServer:
    def __init__(self):self.mutex=threading.Lock();self.locks={};self.connections=[]
    def connect(self):
        with self.mutex:
            c=LockConnection(self,len(self.connections)+1);self.connections.append(c);return c
class LockConnection:
    def __init__(self,s,id):self.s=s;self.id=id;self.owner=threading.get_ident();self.closed=False
    def cursor(self):
        assert self.owner==threading.get_ident()
        return LockCursor(self)
    def close(self):
        assert self.owner==threading.get_ident()
        with self.s.mutex:
            self.s.locks={k:v for k,v in self.s.locks.items() if v!=self.id}
        self.closed=True
class LockCursor:
    description=True;lastrowid=0
    def __init__(self,c):self.c=c
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params):
        c=self.c;s=c.s
        with s.mutex:
            if 'GET_LOCK(' in sql:
                ok=s.locks.get(params[0]) in (None,c.id)
                if ok:s.locks[params[0]]=c.id
                self.row={'ok':int(ok)}
            elif 'IS_USED_LOCK' in sql:self.row={'ok':int(s.locks.get(params[0])==c.id)}
            elif 'RELEASE_LOCK' in sql:s.locks.pop(params[0],None);self.row={'ok':1}
            elif '@@SESSION.wait_timeout' in sql:self.row={'seconds':600}
            else:self.row={'value':42}
    def fetchone(self):return self.row
    def fetchall(self):return [self.row]

class ScopeTests(TestCase):
    def test_lock_key_is_canonical_private_and_within_mysql_limit(self):
        self.assertEqual(account_lock(' User '),account_lock('user'))
        self.assertNotEqual(account_lock('SC_LOGIN'),account_lock('PR_LOGIN'))
        self.assertLessEqual(len(account_lock('x'*200)),64)
        self.assertNotIn('SC_LOGIN',account_lock('SC_LOGIN'))
    def test_different_accounts_hold_real_worker_locks_simultaneously(self):
        server=LockServer()
        with ExitStack() as stack:
            sc=stack.enter_context(Repository(connection_factory=server.connect,account=(SC.user,SC.carrier)))
            pr=stack.enter_context(Repository(connection_factory=server.connect,account=(PR.user,PR.carrier)))
            duplicate=stack.enter_context(Repository(connection_factory=server.connect,account=(SC.user.lower(),'ROBR_OTHER')))
            self.assertTrue(sc.lock());self.assertTrue(pr.lock());self.assertFalse(duplicate.lock())
            sc.assert_lock();pr.assert_lock()
            self.assertEqual(len(server.locks),2)
        self.assertFalse(server.locks)
        self.assertTrue(all(c.closed for c in server.connections))
    def test_closing_sc_does_not_release_pr_lock(self):
        server=LockServer()
        with Repository(connection_factory=server.connect,account=(PR.user,PR.carrier)) as pr:
            self.assertTrue(pr.lock())
            with Repository(connection_factory=server.connect,account=(SC.user,SC.carrier)) as sc:self.assertTrue(sc.lock())
            pr.assert_lock()
            self.assertEqual(len(server.locks),1)
    def test_interrupt_old_is_restricted_to_same_user_and_carrier(self):
        r=Repository.__new__(Repository);r.account=(PR.user,PR.carrier)
        r.assert_lock=mock.Mock();r.query=mock.Mock()
        r.interrupt_old('pr-current')
        sql,params=r.query.call_args.args
        self.assertIn('AND portal_user=%s AND transportador=%s',sql)
        self.assertEqual(params[-2:],(PR.user,PR.carrier))
    def test_unscoped_interrupt_is_refused(self):
        r=Repository.__new__(Repository);r.account=None;r.assert_lock=mock.Mock();r.query=mock.Mock()
        with self.assertRaises(RuntimeError):r.interrupt_old('run')
        r.query.assert_not_called()
    def test_history_and_duplicate_lookup_include_both_account_fields(self):
        r=Repository.__new__(Repository);r.query=mock.Mock(return_value=[])
        r.history(PR.user,PR.carrier)
        self.assertEqual(r.query.call_args.args[1],(PR.user,PR.carrier))
        r.previous(PR.user,PR.carrier,'same-pod','run')
        sql,params=r.query.call_args.args
        self.assertIn('i.portal_user=%s AND i.transportador=%s',sql)
        self.assertEqual(params[:2],(PR.user,PR.carrier))
    def test_cross_account_item_write_is_refused(self):
        r=Repository.__new__(Repository);r.account=(SC.user,SC.carrier);r.assert_lock=mock.Mock();r.query=mock.Mock()
        with self.assertRaises(RuntimeError):r.item('run',PR,None,'nf','ENVIADO')
        r.query.assert_not_called()


class BatchRepo:
    def __init__(self):self.runs=[];self.status=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def create_run(self,*args):self.runs.append(args)
    def run_status(self,*args):self.status.append(args)

class BatchTests(TestCase):
    def test_both_execute_concurrently_with_distinct_settings(self):
        repo=BatchRepo();barrier=threading.Barrier(2);seen=[]
        def execute(run,s,*args):
            barrier.wait(3)
            seen.append((run,s.operation,s.user,s.carrier,threading.get_ident()))
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else PR), \
             mock.patch.object(service,'Repository',return_value=repo),mock.patch.object(service,'execute',side_effect=execute):
            ids=service.start_group(*PERIOD,dry_run=False,background=False)
        self.assertEqual(len(ids),2);self.assertEqual(len(set(ids)),2)
        self.assertEqual({x[1] for x in seen},{'SC','PR'})
        self.assertEqual(len({x[4] for x in seen}),2)
        self.assertEqual(len(repo.runs),2)
    def test_all_profiles_are_validated_before_any_run_or_portal(self):
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else replace(PR,password='')), \
             mock.patch.object(service,'Repository') as repo,mock.patch.object(service,'execute') as execute:
            with self.assertRaises(ValueError):service.start_group(*PERIOD,background=False)
        repo.assert_not_called();execute.assert_not_called()
    def test_duplicate_logins_are_rejected_even_with_different_carriers(self):
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else replace(PR,user=SC.user.lower())), \
             mock.patch.object(service,'Repository') as repo:
            with self.assertRaisesRegex(ValueError,'distintos'):service.start_group(*PERIOD,background=False)
        repo.assert_not_called()
    def test_disabled_pr_send_blocks_real_group_before_sc_starts(self):
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else replace(PR,send_enabled=False)), \
             mock.patch.object(service,'Repository') as repo:
            with self.assertRaises(ValueError):service.start_group(*PERIOD,dry_run=False,background=False)
        repo.assert_not_called()
    def test_single_profile_defaults_to_sc_and_pr_is_explicit(self):
        for code in ('SC','PR'):
            with mock.patch.object(service.Settings,'from_env',return_value=SC if code=='SC' else PR) as load, \
                 mock.patch.object(service,'Repository',return_value=BatchRepo()),mock.patch.object(service,'execute') as run:
                id=service.start_execution(*PERIOD,background=False,profile=code)
            self.assertTrue(id);load.assert_called_once_with(code)
            self.assertEqual(run.call_args.args[1].operation,code)
    def test_sequential_mode_keeps_order(self):
        order=[]
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else PR), \
             mock.patch.object(service,'Repository',return_value=BatchRepo()), \
             mock.patch.object(service,'execute',side_effect=lambda run,s,*args:order.append(s.operation)):
            service.start_group(*PERIOD,background=False,parallel=False)
        self.assertEqual(order,['SC','PR'])


class ProfileSession(ReplaySession):
    def __init__(self,settings,fail=False):super().__init__();self.settings=settings;self.fail=fail
    def send(self,req,**kwargs):
        result=super().send(req,**kwargs)
        if self.fail and '/base/start.x2ps' in req.url:
            result._content=b'<form>Login</form>';return result
        if result._content:
            text=result.text.replace('HTTP_TEST',self.settings.user).replace('ROBR_SC',self.settings.carrier)
            result._content=text.encode('utf-8')
        return result
class RunRepo(FlowRepo):
    def __init__(self):super().__init__();self.status=[];self.scopes=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def lock(self):return True
    def interrupt_old(self,*args):pass
    def run_status(self,*args):self.status.append(args)

class SessionIsolationTests(TestCase):
    def test_two_real_http_clients_complete_independent_flows_at_once(self):
        self.check_parallel(False)
    def test_sc_session_failure_does_not_interrupt_pr(self):
        self.check_parallel(True)
    def check_parallel(self,fail_sc):
        barrier=threading.Barrier(2);sessions={};repos={}
        def task(s):
            repo=RunRepo();repos[s.operation]=repo
            session=ProfileSession(s,fail=s.operation=='SC' and fail_sc);sessions[s.operation]=session
            class Client(Portal):
                def login(self):barrier.wait(3);return super().login()
            def factory(**kwargs):
                if kwargs:repo.scopes.append(kwargs['account'])
                return repo
            service.execute(s.operation,s,*PERIOD,False,repo_factory=factory,
                            portal_factory=lambda cfg:Client(cfg,lambda:session,sleep_fn=lambda seconds:None))
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(task,(SC,PR)))
        self.assertIsNot(sessions['SC'].cookies,sessions['PR'].cookies)
        for code,settings in [('SC',SC),('PR',PR)]:
            repo=repos[code];session=sessions[code]
            self.assertEqual(repo.scopes[0],(settings.user,settings.carrier))
            failed=code=='SC' and fail_sc
            self.assertEqual(repo.status[-1][1],'ERRO' if failed else 'CONCLUIDA')
            self.assertEqual(session.actions().count('Map'),0 if failed else 1)
            if not failed:
                self.assertEqual(repo.events[-1][0],'ENVIADO')
                login=next(r for r,k in session.calls if urlsplit(r.url).path.endswith('LoginViewController.jsp'))
                self.assertEqual(parse_qs(login.body)['loginUser'],[settings.user])
                mapxml=session.action_xml('Map')
                self.assertEqual(mapxml.find('.//LD_NOTA_CARRIER').get('Value'),settings.carrier)

class MultiRoutesTests(TestCase):
    def setUp(self):
        from test_routes import routes, Repo
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        self.routes=routes
        class PanelRepo(Repo):
            histories=[]
            def latest_run(self,user,carrier):
                return dict(self.report()['run'],id='pr' if carrier=='ROBR_PR' else 'sc',
                            transportador=carrier,status='EXECUTANDO' if carrier=='ROBR_PR' else 'CONCLUIDA')
            def history(self,*args):self.histories.append(args);return []
        self.PanelRepo=PanelRepo
        app=FastAPI();app.add_middleware(SessionMiddleware,secret_key='test-only');app.include_router(routes.router)
        self.http=TestClient(app)
    def mocks(self):
        stack=ExitStack()
        stack.enter_context(mock.patch.object(self.routes,'client',return_value=SimpleNamespace(usuario='operator')))
        stack.enter_context(mock.patch.object(self.routes,'Repository',self.PanelRepo))
        stack.enter_context(mock.patch.object(self.routes.Settings,'from_env',side_effect=lambda code='SC':SC if code=='SC' else PR))
        return stack
    def csrf(self):
        import re
        return re.search(r'name="csrf_token" value="([^"]+)"',self.http.get('/lg-pod').text)[1]
    def test_both_operation_cards_and_selector_render_without_secrets(self):
        with self.mocks():r=self.http.get('/lg-pod?execucao=sc')
        self.assertEqual(r.status_code,200)
        self.assertIn('SC + PR',r.text)
        self.assertIn('ROBR_SC',r.text);self.assertIn('ROBR_PR',r.text)
        self.assertNotIn('SC_SECRET',r.text);self.assertNotIn('PR_SECRET',r.text)
    def test_polling_continues_when_selected_sc_done_but_pr_running(self):
        with self.mocks():r=self.http.get('/lg-pod/estado?execucao=sc')
        self.assertTrue(r.json()['active'])
        self.assertIn('PR',r.json()['parts']['accounts'])
        self.assertIn('Em andamento',r.json()['parts']['accounts'])
    def test_history_filter_uses_selected_profile_identity(self):
        with self.mocks():r=self.http.get('/lg-pod/estado?execucao=pr&operacao=PR')
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.PanelRepo.histories[-1],(PR.user,PR.carrier))
    def test_both_post_starts_group_in_parallel(self):
        with self.mocks(),mock.patch.object(self.routes,'start_group',return_value=['sc-id','pr-id']) as group:
            token=self.csrf()
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='AMBAS',csrf_token=token),follow_redirects=False)
        self.assertEqual(r.status_code,303)
        self.assertEqual(group.call_args.kwargs,dict(dry_run=True,actor='operator',parallel=True))
        self.assertEqual(r.headers['location'],'/lg-pod?execucao=sc-id')
    def test_pr_post_starts_only_pr(self):
        with self.mocks(),mock.patch.object(self.routes,'start_execution',return_value='pr-id') as start:
            token=self.csrf()
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='PR',csrf_token=token),follow_redirects=False)
        self.assertEqual(r.status_code,303);self.assertEqual(start.call_args.kwargs['profile'],'PR')
        self.assertIn('operacao=PR',r.headers['location'])
    def test_invalid_profile_and_bad_csrf_do_not_start_anything(self):
        with self.mocks(),mock.patch.object(self.routes,'start_group') as group,mock.patch.object(self.routes,'start_execution') as start:
            token=self.csrf()
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='XX',csrf_token=token))
            self.assertEqual(r.status_code,400)
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='AMBAS',csrf_token='wrong'))
            self.assertEqual(r.status_code,403)
        group.assert_not_called();start.assert_not_called()
    def test_invalid_operation_filter_is_rejected(self):
        with self.mocks():r=self.http.get('/lg-pod/estado?execucao=sc&operacao=XX')
        self.assertEqual(r.status_code,400)
    def test_manual_resolution_with_wrong_profile_is_refused(self):
        from scripts.resolver_lg_pod import resolve_item
        from test_mapping_recovery import ResolveRepo
        repo=ResolveRepo()
        with self.assertRaisesRegex(ValueError,'perfil'):
            resolve_item(repo,1081,'enviado','Conferência manual por operador',settings=PR)
        self.assertFalse(repo.updates)

class ExtraIsolationTests(TestCase):
    def test_lost_sc_lock_does_not_poison_pr_connection(self):
        server=LockServer()
        with Repository(connection_factory=server.connect,account=(SC.user,SC.carrier)) as sc, \
             Repository(connection_factory=server.connect,account=(PR.user,PR.carrier)) as pr:
            self.assertTrue(sc.lock());self.assertTrue(pr.lock())
            with server.mutex:server.locks.pop(account_lock(SC.user))
            with self.assertRaisesRegex(RuntimeError,'perdido'):sc.assert_lock()
            pr.assert_lock()
            self.assertEqual(pr.query('SELECT value',one=True),{'value':42})
    def test_failed_group_preparation_does_not_launch_any_portal(self):
        first=BatchRepo();recovery=BatchRepo()
        create=first.create_run
        def add(*args):
            if first.runs:raise RuntimeError('DB unavailable')
            create(*args)
        first.create_run=add
        with mock.patch.object(service.Settings,'from_env',side_effect=lambda code:SC if code=='SC' else PR), \
             mock.patch.object(service,'Repository',side_effect=[first,recovery]),mock.patch.object(service,'execute') as execute:
            with self.assertRaises(RuntimeError):service.start_group(*PERIOD,background=False)
        execute.assert_not_called()
        self.assertEqual(recovery.status[0][1],'ERRO')
    def test_account_card_prefers_running_job_over_a_newer_blocked_attempt(self):
        r=Repository.__new__(Repository);r.query=mock.Mock(return_value=None)
        r.latest_run(SC.user,SC.carrier)
        sql,params=r.query.call_args.args
        self.assertIn("WHEN status='EXECUTANDO' THEN 0",sql)
        self.assertIn("WHEN status='AGUARDANDO' THEN 1",sql)
        self.assertEqual(params,(SC.user,SC.carrier))
