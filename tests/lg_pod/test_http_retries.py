"""Retries de consultas com transporte gravado; nenhuma rede ou espera real."""
from dataclasses import replace
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit
import requests
from modules.lg_pod.config import Settings
from modules.lg_pod.portal import Portal, PortalError
from test_http_portal import ReplaySession, SETTINGS, PERIOD, response, fixture
from test_lg_pod import Repo, Portal as FlowPortal, SETTINGS as FLOW_SETTINGS
from modules.lg_pod.service import process


def action(req):
    return parse_qs(urlsplit(req.url).query).get('action', [''])[0]


class RetryTests(unittest.TestCase):
    def ready(self, settings=SETTINGS):
        self.session=ReplaySession()
        self.delays=[]
        self.portal=Portal(settings,lambda:self.session,sleep_fn=self.delays.append)
        self.portal.__enter__()
        self.addCleanup(self.portal.__exit__)
        self.portal.login()
        return self.portal
    def test_proxy_failure_recovers_after_two_attempts(self):
        p=self.ready()
        attempts=0
        def hook(req):
            nonlocal attempts
            if action(req)=='SearchLeft_button':
                attempts+=1
                if attempts<3:raise requests.exceptions.ProxyError('private details')
        self.session.hook=hook
        messages=[];p.on_retry=messages.append
        with self.assertLogs('modules.lg_pod.portal',level='WARNING') as logs:
            rows=p.search(*PERIOD)
        self.assertEqual(len(rows),1)
        self.assertEqual(self.delays,[5,10])
        self.assertEqual(attempts,3)
        self.assertFalse(p.failed)
        self.assertIn('3/4',messages[-1])
        self.assertNotIn('private details',str(logs.output))
        self.assertNotIn('Map',self.session.actions())
    def test_exhaustion_keeps_session_failed_and_no_more_requests(self):
        p=self.ready()
        def hook(req):
            if action(req)=='SearchLeft_button':raise requests.exceptions.ReadTimeout('private')
        self.session.hook=hook
        with self.assertLogs('modules.lg_pod.portal',level='WARNING'):
            with self.assertRaisesRegex(PortalError,'4 tentativa'):
                p.search(*PERIOD)
        self.assertEqual(self.delays,[5,10,20])
        self.assertEqual(self.session.actions().count('SearchLeft_button'),4)
        before=len(self.session.calls)
        with self.assertRaises(PortalError):p.search(*PERIOD)
        self.assertEqual(len(self.session.calls),before)
        self.assertTrue(p.failed)
    def test_map_timeout_is_sent_once(self):
        p=self.ready()
        row=p.search(*PERIOD)[0];inv=p.find_invoice(row.nf or '000138810')
        self.session.mode='map_timeout'
        with self.assertRaises(PortalError):p.map(row,inv)
        self.assertEqual(self.session.actions().count('Map'),1)
        self.assertEqual(self.delays,[])
        self.assertTrue(p.mutation_attempted)
    def test_approval_timeout_is_sent_once(self):
        p=self.ready()
        row=p.search(*PERIOD)[0];inv=p.find_invoice('000138810');p.map(row,inv)
        mapped=p.refresh_pod(row.pod_id)
        self.session.mode='approve_timeout'
        with self.assertRaises(PortalError):p.approve(mapped)
        self.assertEqual(self.session.actions().count('btnApprove'),1)
        self.assertEqual(self.delays,[])
        self.assertTrue(p.mutation_attempted)
    def test_invoice_read_retries_then_exact_single_map(self):
        p=self.ready();row=p.search(*PERIOD)[0]
        attempts=0
        def hook(req):
            nonlocal attempts
            if action(req)=='SearchRight_button':
                attempts+=1
                if attempts==1:raise requests.exceptions.ConnectionError('reset')
        self.session.hook=hook
        inv=p.find_invoice('000138810');p.map(row,inv)
        self.assertEqual(self.delays,[5])
        self.assertEqual(self.session.actions().count('Map'),1)
        self.assertFalse(p.failed)
    def test_page_read_retries_without_duplicate_append(self):
        import test_http_portal as http
        p=self.ready();http.PaginationTests().prepare(self.session)
        old=self.session.hook
        attempts=0
        def hook(req):
            nonlocal attempts
            if action(req)=='SYS_REFRESH':
                attempts+=1
                if attempts==1:raise requests.exceptions.ProxyError('reset')
            return old(req)
        self.session.hook=hook
        rows=p.search(*PERIOD)
        self.assertEqual([r.pod_id for r in rows],['1717057','9999999'])
        self.assertEqual(self.session.actions().count('SYS_REFRESH'),2)
        self.assertEqual(self.delays,[5])
    def test_partial_response_is_closed_before_repeating(self):
        p=self.ready();closed=[]
        class BrokenBody(requests.Response):
            def iter_content(self,*args,**kwargs):
                yield b'<html>partial'
                raise requests.exceptions.ChunkedEncodingError('truncated')
            def close(self):closed.append(True)
        attempt=0
        def hook(req):
            nonlocal attempt
            if action(req)=='SearchLeft_button':
                attempt+=1
                if attempt==1:
                    r=BrokenBody();r.status_code=200;r.headers['Content-Type']='text/html';return r
        self.session.hook=hook
        self.assertEqual(len(p.search(*PERIOD)),1)
        self.assertEqual(closed,[True]);self.assertEqual(self.delays,[5])
    def test_transient_statuses_recover(self):
        for code in (429,502,503,504):
            with self.subTest(code=code):
                p=self.ready();attempts=0
                def hook(req):
                    nonlocal attempts
                    if action(req)=='SearchLeft_button':
                        attempts+=1
                        if attempts==1:return response(req,status=code)
                self.session.hook=hook
                self.assertEqual(len(p.search(*PERIOD)),1)
                self.assertEqual(self.delays,[5])
    def test_auth_client_errors_and_certificate_are_not_retried(self):
        for error in (401,403,407,400,500,'ssl','proxy407','proxy403'):
            with self.subTest(error=error):
                p=self.ready()
                def hook(req):
                    if action(req)!='SearchLeft_button':return None
                    if error=='ssl':raise requests.exceptions.SSLError('private certificate')
                    if error=='proxy407':raise requests.exceptions.ProxyError('Tunnel connection failed: 407 Proxy Authentication Required')
                    if error=='proxy403':raise requests.exceptions.ProxyError('Tunnel connection failed: 403 Forbidden')
                    return response(req,status=error)
                self.session.hook=hook
                with self.assertRaises(PortalError):p.search(*PERIOD)
                self.assertEqual(self.delays,[])
                self.assertEqual(self.session.actions().count('SearchLeft_button'),1)
    def test_retry_after_is_respected_or_stops_above_cap(self):
        for value,delay in [('12',12),('120',None),('invalid',None)]:
            with self.subTest(value=value):
                p=self.ready();attempts=0
                def hook(req):
                    nonlocal attempts
                    if action(req)=='SearchLeft_button':
                        attempts+=1
                        if attempts==1:return response(req,status=429,headers={'Retry-After':value})
                self.session.hook=hook
                if delay is None:
                    with self.assertRaisesRegex(PortalError,'Retry-After'):p.search(*PERIOD)
                    self.assertEqual(self.delays,[])
                else:
                    self.assertEqual(len(p.search(*PERIOD)),1)
                    self.assertEqual(self.delays,[delay])
    def test_max_delay_and_attempt_limit_are_configurable(self):
        p=self.ready(replace(SETTINGS,read_attempts=5,read_retry_delay_s=2,read_retry_max_delay_s=3))
        self.session.hook=lambda req: response(req,status=503) if action(req)=='SearchLeft_button' else None
        with self.assertRaisesRegex(PortalError,'5 tentativa'):p.search(*PERIOD)
        self.assertEqual(self.delays,[2,3,3,3])
    def test_one_attempt_disables_retries(self):
        p=self.ready(replace(SETTINGS,read_attempts=1))
        self.session.hook=lambda req: response(req,status=503) if action(req)=='SearchLeft_button' else None
        with self.assertRaises(PortalError):p.search(*PERIOD)
        self.assertEqual(self.delays,[])
        self.assertEqual(self.session.actions().count('SearchLeft_button'),1)
    def test_session_expiration_is_not_retried(self):
        p=self.ready()
        self.session.hook=lambda req:response(req,'<form>Login</form>') if action(req)=='SearchLeft_button' else None
        with self.assertRaisesRegex(PortalError,'sessão expirada'):p.search(*PERIOD)
        self.assertEqual(self.delays,[])
    def test_retry_callback_failure_stops_before_next_request(self):
        p=self.ready()
        self.session.hook=lambda req:response(req,status=503) if action(req)=='SearchLeft_button' else None
        p.on_retry=mock.Mock(side_effect=RuntimeError('Lock perdido'))
        with self.assertRaisesRegex(RuntimeError,'Lock perdido'):p.search(*PERIOD)
        self.assertEqual(self.delays,[])
        self.assertEqual(self.session.actions().count('SearchLeft_button'),1)
    def test_proxy_diagnostic_does_not_expose_secrets_or_urls(self):
        p=self.ready()
        self.session.hook=lambda req: (_ for _ in ()).throw(requests.exceptions.ProxyError('http://user:password@proxy.invalid:3128 reset')) if action(req)=='SearchLeft_button' else None
        with self.assertLogs('modules.lg_pod.portal',level='WARNING') as logs:
            with self.assertRaises(PortalError) as result:p.search(*PERIOD)
        text=str(logs.output)+str(result.exception)
        for secret in ('password','user:','proxy.invalid','http://'):
            self.assertNotIn(secret,text)
    def test_http_error_on_mutation_is_not_repeated(self):
        for mutation in ('Map','btnApprove'):
            with self.subTest(mutation=mutation):
                p=self.ready();row=p.search(*PERIOD)[0];inv=p.find_invoice('000138810')
                if mutation=='btnApprove':p.map(row,inv);row=p.refresh_pod(row.pod_id)
                self.session.hook=lambda req:response(req,status=503) if action(req)==mutation else None
                with self.assertRaises(PortalError):
                    p.map(row,inv) if mutation=='Map' else p.approve(row)
                self.assertEqual(self.session.actions().count(mutation),1)
                self.assertEqual(self.delays,[])
    def test_mislabeled_mutation_url_is_not_read_retryable(self):
        p=self.ready()
        self.session.hook=lambda req:response(req,status=503)
        with self.assertRaises(PortalError):
            p._http('POST',SETTINGS.base_url+'/base/view.x2ps?action=Map',label='SearchLeft_button')
        self.assertEqual(self.delays,[])
    def test_get_and_login_post_are_not_automatically_repeated(self):
        for method,url,label in [('GET','/tm/admin/LoginView.jsp','abertura do login'),('POST','/tm/admin/LoginViewController.jsp','login LG')]:
            with self.subTest(method=method):
                p=self.ready();before=len(self.session.calls)
                self.session.hook=lambda req:response(req,status=503)
                with self.assertRaises(PortalError):p._http(method,SETTINGS.base_url+url,label=label)
                self.assertEqual(len(self.session.calls),before+1);self.assertEqual(self.delays,[])
    def test_successful_retry_keeps_original_workflow_validation(self):
        p=self.ready();attempts=0
        def hook(req):
            nonlocal attempts
            if action(req)=='SearchLeft_button':
                attempts+=1
                if attempts==1:raise requests.exceptions.ProxyError('reset')
                return response(req,fixture('pending').replace('HTTP_TEST','OTHER_USER'))
        self.session.hook=hook
        with self.assertRaises(PortalError):p.search(*PERIOD)
        self.assertTrue(p.failed)
        self.assertNotIn('Map',self.session.actions())


class RetryConfigTests(unittest.TestCase):
    def test_defaults_and_env(self):
        with mock.patch.dict(os.environ,{'LG_POD_READ_ATTEMPTS':'3','LG_POD_READ_RETRY_DELAY_S':'2','LG_POD_READ_RETRY_MAX_DELAY_S':'12'}):
            with mock.patch('modules.lg_pod.config.load_env'):
                settings=Settings.from_env()
        self.assertEqual((settings.read_attempts,settings.read_retry_delay_s,settings.read_retry_max_delay_s),(3,2,12))
        self.assertEqual(SETTINGS.read_attempts,4)
    def test_unbounded_or_nonfinite_settings_are_rejected(self):
        for changes in [dict(read_attempts=0),dict(read_attempts=7),dict(read_retry_delay_s=0),dict(read_retry_max_delay_s=61),dict(read_retry_delay_s=10,read_retry_max_delay_s=5),dict(read_retry_delay_s=float('nan')),dict(read_retry_max_delay_s=float('inf'))]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):replace(SETTINGS,**changes).validate()

class ExecuteRetryTests(unittest.TestCase):
    def test_retry_updates_run_and_completes_one_actual_send(self):
        from modules.lg_pod.service import execute
        class RunRepo(Repo):
            def __init__(self):super().__init__();self.status=[]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def lock(self):return True
            def interrupt_old(self,*args):pass
            def run_status(self,*args):self.status.append(args)
        repo=RunRepo();session=ReplaySession();delays=[];attempts=0
        def hook(req):
            nonlocal attempts
            if action(req)=='SearchRight_button':
                attempts+=1
                if attempts==1:raise requests.exceptions.ProxyError('reset')
        session.hook=hook
        execute('run',SETTINGS,*PERIOD,False,repo_factory=lambda **kwargs:repo,
                portal_factory=lambda s:Portal(s,lambda:session,sleep_fn=delays.append))
        self.assertEqual(repo.status[-1][1],'CONCLUIDA')
        self.assertTrue(any('Nova tentativa 2/4' in r[2] and 'Consultando NF 000138810' in r[2] for r in repo.status))
        self.assertTrue(any('restabelecida' in r[2] for r in repo.status))
        self.assertEqual(session.actions().count('Map'),1)
        self.assertEqual(session.actions().count('btnApprove'),1)
        self.assertEqual(repo.events[-1][0],'ENVIADO')
        self.assertEqual(delays,[5])
    def test_map_uncertainty_is_preserved_and_batch_stops(self):
        from modules.lg_pod.service import execute
        class RunRepo(Repo):
            def __init__(self):super().__init__();self.status=[]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def lock(self):return True
            def interrupt_old(self,*args):pass
            def run_status(self,*args):self.status.append(args)
        repo=RunRepo();session=ReplaySession();session.mode='map_timeout';delays=[]
        execute('run',SETTINGS,*PERIOD,False,repo_factory=lambda **kwargs:repo,
                portal_factory=lambda s:Portal(s,lambda:session,sleep_fn=delays.append))
        self.assertEqual(repo.status[-1][1],'ERRO')
        self.assertEqual(repo.events[-1][0],'CONFERIR')
        self.assertEqual(session.actions().count('Map'),1)
        self.assertNotIn('btnApprove',session.actions())
        self.assertEqual(delays,[])
