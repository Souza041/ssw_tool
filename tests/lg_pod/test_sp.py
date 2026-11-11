"""SP e três operações simultâneas: configuração, sessão, lock e interface."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from unittest import TestCase, mock
from modules.lg_pod.config import Settings, PROFILES
from modules.lg_pod.repository import Repository
from modules.lg_pod import service
from modules.lg_pod.portal import Portal
from test_http_portal import PERIOD
import test_multiaccount as multi
from test_multiaccount import SC, PR, ProfileSession, RunRepo, BatchRepo, LockServer

SP=replace(PR,user='SP_LOGIN',password='SP_SECRET',carrier='ROBR_SP',operation='SP')
CONFIGS={'SC':SC,'PR':PR,'SP':SP}

class SPConfigTests(TestCase):
    def test_sp_has_own_credentials_carrier_and_send_flag(self):
        env={'LG_POD_USER':'sc','LG_POD_PASSWORD':'scpass','LG_POD_CARRIER':'ROBR_SC','LG_POD_ENVIO_HABILITADO':'true',
             'LG_POD_PR_USER':'pr','LG_POD_PR_PASSWORD':'prpass','LG_POD_PR_ENVIO_HABILITADO':'true',
             'LG_POD_SP_USER':'sp','LG_POD_SP_PASSWORD':'sppass','LG_POD_SP_TIMEOUT_MS':'45000'}
        with mock.patch.dict(os.environ,env,clear=True),mock.patch('modules.lg_pod.config.load_env'):
            before=dict(os.environ);s=Settings.from_env('SP');self.assertEqual(dict(os.environ),before)
        self.assertEqual((s.user,s.password,s.carrier,s.operation,s.timeout_ms,s.send_enabled),('sp','sppass','ROBR_SP','SP',45000,False))
    def test_sp_missing_password_never_uses_sc_or_pr_password(self):
        env={'LG_POD_USER':'sc','LG_POD_PASSWORD':'scpass','LG_POD_PR_PASSWORD':'prpass','LG_POD_SP_USER':'sp'}
        with mock.patch.dict(os.environ,env,clear=True),mock.patch('modules.lg_pod.config.load_env'):
            s=Settings.from_env('SP')
        self.assertEqual(s.password,'')
        with self.assertRaises(ValueError):s.validate()
    def test_sp_send_requires_explicit_enable(self):
        env={'LG_POD_SP_USER':'sp','LG_POD_SP_PASSWORD':'spsecret','LG_POD_SP_ENVIO_HABILITADO':'true'}
        with mock.patch.dict(os.environ,env,clear=True),mock.patch('modules.lg_pod.config.load_env'):
            s=Settings.from_env('SP')
        s.validate(real=True);self.assertTrue(s.send_enabled)
    def test_sp_password_is_redacted(self):
        from modules.lg_pod.db_worker import safe_error_message
        with mock.patch.dict(os.environ,{'LG_POD_SP_PASSWORD':'SP_SECRET'}):
            self.assertNotIn('SP_SECRET',safe_error_message(RuntimeError('SP_SECRET')))

class ThreeAccountTests(TestCase):
    def test_three_worker_locks_can_coexist_and_sp_duplicate_is_blocked(self):
        server=LockServer()
        with ExitStack() as stack:
            repos=[stack.enter_context(Repository(connection_factory=server.connect,account=(s.user,s.carrier))) for s in CONFIGS.values()]
            for r in repos:self.assertTrue(r.lock())
            duplicate=stack.enter_context(Repository(connection_factory=server.connect,account=(SP.user,SP.carrier)))
            self.assertFalse(duplicate.lock())
            for r in repos:r.assert_lock()
            self.assertEqual(len(server.locks),3)
        self.assertFalse(server.locks)
    def test_three_http_flows_and_sp_failure_isolation(self):
        for fail_sp in (False,True):
            with self.subTest(fail_sp=fail_sp):
                barrier=threading.Barrier(3);repos={};sessions={}
                def task(settings):
                    repo=RunRepo();repos[settings.operation]=repo
                    session=ProfileSession(settings,fail=settings.operation=='SP' and fail_sp);sessions[settings.operation]=session
                    class Client(Portal):
                        def login(self):barrier.wait(3);return super().login()
                    def factory(**kwargs):
                        if kwargs:repo.scopes.append(kwargs['account'])
                        return repo
                    service.execute(settings.operation,settings,*PERIOD,False,repo_factory=factory,
                                    portal_factory=lambda s:Client(s,lambda:session,sleep_fn=lambda seconds:None))
                with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(task,CONFIGS.values()))
                self.assertEqual(len({id(s.cookies) for s in sessions.values()}),3)
                for code,settings in CONFIGS.items():
                    failed=fail_sp and code=='SP'
                    self.assertEqual(repos[code].status[-1][1],'ERRO' if failed else 'CONCLUIDA')
                    self.assertEqual(sessions[code].actions().count('Map'),0 if failed else 1)
                    self.assertEqual(sessions[code].actions().count('btnApprove'),0 if failed else 1)
                    self.assertEqual(repos[code].scopes[0],(settings.user,settings.carrier))
    def test_todas_orchestrates_three_threads(self):
        barrier=threading.Barrier(3);seen=[];repo=BatchRepo()
        def execute(run,s,*args):barrier.wait(3);seen.append((s.operation,threading.get_ident()))
        with mock.patch.object(service.Settings,'from_env',side_effect=CONFIGS.__getitem__), \
             mock.patch.object(service,'Repository',return_value=repo),mock.patch.object(service,'execute',side_effect=execute):
            ids=service.start_group(*PERIOD,background=False,profiles=PROFILES)
        self.assertEqual(len(set(ids)),3)
        self.assertEqual({c for c,t in seen},set(PROFILES))
        self.assertEqual(len({t for c,t in seen}),3)
    def test_invalid_sp_prevents_starting_sc_and_pr(self):
        configs=dict(CONFIGS,SP=replace(SP,send_enabled=False))
        with mock.patch.object(service.Settings,'from_env',side_effect=configs.__getitem__), \
             mock.patch.object(service,'Repository') as repo,mock.patch.object(service,'execute') as execute:
            with self.assertRaises(ValueError):service.start_group(*PERIOD,dry_run=False,background=False,profiles=PROFILES)
        repo.assert_not_called();execute.assert_not_called()
    def test_duplicate_sp_login_is_rejected(self):
        configs=dict(CONFIGS,SP=replace(SP,user=PR.user.lower()))
        with mock.patch.object(service.Settings,'from_env',side_effect=configs.__getitem__),mock.patch.object(service,'Repository') as repo:
            with self.assertRaises(ValueError):service.start_group(*PERIOD,background=False,profiles=PROFILES)
        repo.assert_not_called()
    def test_sp_history_filter_uses_sp_user_and_carrier(self):
        from modules.lg_pod.accounts import filtered_history
        repo=mock.Mock()
        with mock.patch.object(Settings,'from_env',return_value=SP):filtered_history(repo,'SP')
        repo.history.assert_called_once_with(SP.user,SP.carrier)

class SPRouteTests(TestCase):
    setUp = multi.MultiRoutesTests.setUp
    csrf = multi.MultiRoutesTests.csrf
    def mocks(self):
        stack=multi.MultiRoutesTests.mocks(self)
        stack.enter_context(mock.patch.object(self.routes.Settings,'from_env',side_effect=lambda code='SC':CONFIGS[code]))
        return stack
    def test_todas_post_starts_three_profiles(self):
        with self.mocks(),mock.patch.object(self.routes,'start_group',return_value=['sc','pr','sp']) as group:
            token=self.csrf()
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='TODAS',csrf_token=token),follow_redirects=False)
        self.assertEqual(r.status_code,303)
        self.assertEqual(group.call_args.kwargs['profiles'],PROFILES)
        self.assertTrue(group.call_args.kwargs['parallel'])
    def test_sp_post_starts_only_sp(self):
        with self.mocks(),mock.patch.object(self.routes,'start_execution',return_value='sp-id') as start:
            token=self.csrf()
            r=self.http.post('/lg-pod/executar',data=dict(data_inicial='2026-11-03',data_final='2026-11-04',
                            modo='simular',perfil='SP',csrf_token=token),follow_redirects=False)
        self.assertEqual(r.status_code,303)
        self.assertEqual(start.call_args.kwargs['profile'],'SP')
        self.assertIn('operacao=SP',r.headers['location'])
    def test_sp_filter_and_cards_do_not_expose_password(self):
        with self.mocks():r=self.http.get('/lg-pod/estado?execucao=sp&operacao=SP')
        self.assertEqual(r.status_code,200)
        self.assertIn('ROBR_SP',r.json()['parts']['accounts'])
        self.assertNotIn('SP_SECRET',r.text)
        self.assertEqual(self.PanelRepo.histories[-1],(SP.user,SP.carrier))
    def test_sp_active_keeps_polling_when_sc_pr_are_done(self):
        old_latest=self.PanelRepo.latest_run
        def latest(repo,user,carrier):
            r=old_latest(repo,user,carrier);r['status']='EXECUTANDO' if carrier=='ROBR_SP' else 'CONCLUIDA';return r
        with self.mocks(),mock.patch.object(self.PanelRepo,'latest_run',latest):r=self.http.get('/lg-pod/estado?execucao=sc')
        self.assertTrue(r.json()['active'])

    def test_both_group_choices_remain_enabled_with_three_configured_profiles(self):
        import re
        with self.mocks():r=self.http.get('/lg-pod?execucao=sc')
        for value in ('AMBAS','TODAS'):
            attributes=re.search(r'<option value="'+value+r'"([^>]*)>',r.text)[1]
            self.assertNotIn('disabled',attributes)
