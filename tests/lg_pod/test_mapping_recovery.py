"""Recuperação sem rede: transportes gravados e repositórios em memória."""
import copy
from dataclasses import replace
import unittest
from unittest.mock import patch
import requests
from modules.lg_pod.portal import Portal, PortalError
from modules.lg_pod.service import process, failure_detail
from scripts.resolver_lg_pod import resolve_item
from test_lg_pod import Repo, Portal as FlowPortal, SETTINGS as FLOW_SETTINGS, pod, old
from test_http_portal import ReplaySession, SETTINGS, PERIOD


class MutationMarkerTests(unittest.TestCase):
    def ready(self):
        session = ReplaySession()
        portal = Portal(SETTINGS, lambda: session)
        portal.__enter__()
        self.addCleanup(portal.__exit__)
        portal.login()
        row = portal.search(*PERIOD)[0]
        inv = portal.find_invoice('000138810')
        return portal, session, row, inv

    def test_preflight_failure_has_no_mutation_post(self):
        portal, session, row, inv = self.ready()
        with patch.object(portal, 'load_pods', side_effect=PortalError('Lista mudou')):
            with self.assertRaisesRegex(PortalError, 'Lista mudou'):
                portal.map(row, inv)
        self.assertIs(portal.mutation_attempted, False)
        self.assertNotIn('Map', session.actions())

    def test_map_timeout_is_uncertain(self):
        portal, session, row, inv = self.ready()
        session.mode = 'map_timeout'
        with self.assertRaises(PortalError):
            portal.map(row, inv)
        self.assertIs(portal.mutation_attempted, True)
        self.assertEqual(session.actions().count('Map'), 1)
        self.assertNotIn('btnApprove', session.actions())

    def test_marker_resets_before_approval_preflight(self):
        portal, session, row, inv = self.ready()
        portal.map(row, inv)
        self.assertIs(portal.mutation_attempted, True)
        with self.assertRaises(PortalError):
            portal.approve(row)  # stale PENDING identity is not the current mapped row
        self.assertIs(portal.mutation_attempted, False)
        self.assertNotIn('btnApprove', session.actions())

    def test_pending_with_link_is_rejected(self):
        portal, session, row, inv = self.ready()
        row.values['NOTAS_MAPEADAS'] = inv.nf
        with patch.object(portal, 'load_pods', return_value=[row]):
            with self.assertRaises(PortalError):
                portal.map(row, inv)
        self.assertIs(portal.mutation_attempted, False)
        self.assertNotIn('Map', session.actions())


class ErrorTests(unittest.TestCase):
    def test_failed_map_classification_and_original_cause(self):
        for marker, stage in [(False, 'ERRO_CONSULTA'), (True, 'CONFERIR'), (None, 'CONFERIR')]:
            with self.subTest(marker=marker):
                repo, portal = Repo(), FlowPortal()
                portal.mutation_attempted = marker
                with patch.object(portal, 'map', side_effect=PortalError('Lista mudou')):
                    with self.assertLogs('modules.lg_pod.service', level='ERROR') as logs:
                        with self.assertRaisesRegex(RuntimeError, 'PortalError: Lista mudou') as error:
                            process(repo, portal, 'run', FLOW_SETTINGS, *PERIOD, False)
                self.assertIsInstance(error.exception.__cause__, PortalError)
                self.assertIn('Lista mudou', ''.join(logs.output))
                self.assertEqual(repo.events[-1][0], stage)
                self.assertNotIn('approve', portal.calls)

    def test_approval_preflight_keeps_verified_mapping(self):
        repo, portal = Repo(), FlowPortal()
        portal.mutation_attempted = False
        with patch.object(portal, 'approve', side_effect=PortalError('Vínculo mudou')):
            with self.assertLogs('modules.lg_pod.service', level='ERROR'):
                with self.assertRaisesRegex(RuntimeError, 'Envio não iniciado'):
                    process(repo, portal, 'run', FLOW_SETTINGS, *PERIOD, False)
        self.assertEqual(repo.events[-1][0], 'MAPEADO')

    def test_secrets_urls_and_document_contents_are_not_logged(self):
        settings = replace(FLOW_SETTINGS, proxy_password='proxy-secret')
        detail = failure_detail(RuntimeError('senha_teste proxy-secret https://example.test/token'), settings)
        self.assertNotIn('senha_teste', detail)
        self.assertNotIn('proxy-secret', detail)
        self.assertNotIn('https://', detail)
        self.assertNotIn('SECRET', failure_detail(RuntimeError('<html>SECRET</html>'), settings))

    def test_journal_failure_keeps_portal_cause_in_log(self):
        repo, portal = Repo(), FlowPortal()
        original_item = repo.item
        def item(*args):
            if args[4] == 'CONFERIR':
                raise RuntimeError('Banco indisponível')
            return original_item(*args)
        with patch.object(repo, 'item', side_effect=item), patch.object(portal, 'map', side_effect=PortalError('Causa original')):
            with self.assertLogs('modules.lg_pod.service', level='ERROR') as logs:
                with self.assertRaisesRegex(RuntimeError, 'Causa original'):
                    process(repo, portal, 'run', FLOW_SETTINGS, *PERIOD, False)
        self.assertIn('Causa original', logs.output[0])
        self.assertIn('Banco indisponível', logs.output[1])

    def test_released_retries_and_sent_never_repeats(self):
        for stage, expected in [('LIBERADO', ['search_nf', 'map', 'approve']), ('ENVIADO', [])]:
            repo, portal = Repo(old(stage)), FlowPortal()
            process(repo, portal, 'run', FLOW_SETTINGS, *PERIOD, False)
            self.assertEqual(portal.calls, expected)


class ResolveRepo:
    def __init__(self):
        self.item = dict(id=1081, execucao_id='run', portal_user=SETTINGS.user,
                         transportador=SETTINGS.carrier, pod_id='1717057', arquivo='ROBR_SC_000138810.png',
                         nf='000138810', etapa='CONFERIR', mensagem='Evidência anterior')
        self.run = dict(id='run', simulacao=0, status='ERRO', portal_user=SETTINGS.user,
                        transportador=SETTINGS.carrier, data_inicial=PERIOD[0], data_final=PERIOD[1])
        self.latest = 1081
        self.locked = True
        self.updates = []
    def lock(self): return self.locked
    def assert_lock(self):
        if not self.locked: raise RuntimeError('Lock perdido')
    def previous(self, *args): return dict(self.item, id=self.latest)
    def query(self, sql, params=(), one=False):
        if sql.startswith('SELECT * FROM lg_pod_itens'): return copy.deepcopy(self.item)
        if sql.startswith('SELECT * FROM lg_pod_execucoes'): return self.run
        if sql.startswith('UPDATE lg_pod_itens'):
            self.updates.append(params)
            self.item.update(etapa=params[0], mensagem=params[1])
            return
        raise AssertionError(sql)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.repo = ResolveRepo()
        self.session = ReplaySession()
        self.factory = lambda settings: Portal(settings, lambda: self.session)
    def resolve(self):
        return resolve_item(self.repo, 1081, 'liberar', 'Dhionatas conferiu PENDING no portal', SETTINGS, self.factory)
    def test_release_verifies_read_only_and_preserves_evidence(self):
        self.assertEqual(self.resolve(), 'LIBERADO')
        self.assertEqual(len(self.repo.updates), 1)
        self.assertIn('Evidência anterior', self.repo.item['mensagem'])
        self.assertTrue(self.session.actions())
        self.assertNotIn('Map', self.session.actions())
        self.assertNotIn('btnApprove', self.session.actions())
    def test_mapped_portal_refuses_release(self):
        self.session.mapped = True
        with self.assertRaises(ValueError): self.resolve()
        self.assertFalse(self.repo.updates)
    def test_pending_with_mapped_nf_refuses_release(self):
        from test_http_portal import fixture, response
        from urllib.parse import parse_qs, urlsplit
        def hook(request):
            if parse_qs(urlsplit(request.url).query).get('action') == ['SearchLeft_button']:
                body = fixture('pending').replace('<td jsn="NOTAS_MAPEADAS"></td>',
                                                   '<td jsn="NOTAS_MAPEADAS">000138810</td>')
                self.assertNotEqual(body, fixture('pending'))
                return response(request, body)
        self.session.hook = hook
        with self.assertRaises(ValueError): self.resolve()
        self.assertFalse(self.repo.updates)
    def test_identity_mismatch_refuses_release(self):
        for field, value in [('pod_id','wrong'), ('arquivo','ROBR_SC_999.png'), ('nf','999'), ('portal_user','other'), ('transportador','ROBR_SP')]:
            with self.subTest(field=field):
                self.repo = ResolveRepo()
                self.repo.item[field] = value
                with self.assertRaises(ValueError): self.resolve()
                self.assertFalse(self.repo.updates)
    def test_nonlatest_simulation_active_or_sent_refuses(self):
        for case in ['old', 'simulation', 'active', 'sent', 'mapped', 'lock']:
            with self.subTest(case=case):
                self.repo = ResolveRepo()
                if case=='old': self.repo.latest = 2000
                if case=='simulation': self.repo.run['simulacao'] = 1
                if case=='active': self.repo.run['status'] = 'EXECUTANDO'
                if case=='sent': self.repo.item['etapa'] = 'ENVIADO'
                if case=='mapped': self.repo.item['etapa'] = 'MAPEADO'
                if case=='lock': self.repo.locked = False
                with self.assertRaises(ValueError): self.resolve()
                self.assertFalse(self.repo.updates)


if __name__ == '__main__': unittest.main()
