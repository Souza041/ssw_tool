import copy
from dataclasses import replace
from datetime import date
from pathlib import Path
import unittest
from modules.lg_pod.config import Settings, validate_period, today
from modules.lg_pod.parser import (parse_html, eligibility, invoice_from_filename,
                                    choose_invoice, mapping_success, approval_success, Row)
from modules.lg_pod.portal import encode_password
from modules.lg_pod.service import process

FIXTURES = Path(__file__).parent / 'fixtures'
SETTINGS = Settings('usuario_teste', 'senha_teste', 'ROBR_SC', date(2025,1,1), True)
PERIOD = (date(2026,11,3), date(2026,11,4))


def pod(nf='000138810', state='PENDING', code='0', name=None):
    return Row('pod', dict(ID_POD='1717057', IMAGE_NAME=name or f'ROBR_SC_{nf}.png',
                          STATUS=state, POD_STATUS=code, P_CARR_CD='ROBR_SC', NOTAS_MAPEADAS=''))


def invoice():
    return Row('invoice', dict(KEY_CODE='000138810|33|ROBR_SC|20261029', LD_NF_NO='000138810',
                              LD_NF_SERIE='33', LD_NOTA_CARRIER='ROBR_SC'))


class Repo:
    def __init__(self, previous=None, unresolved=()):
        self.old = previous
        self.missing = unresolved
        self.events = []
        self.allow = True

    def previous(self, *args):
        return self.old

    def unresolved(self, *args):
        return self.missing

    def assert_lock(self):
        if not self.allow:
            raise RuntimeError('Lock perdido')

    def item(self, run, settings, row, nf, stage, message='', inv=None):
        self.assert_lock()
        self.events.append((stage, row.pod_id, nf))


class Portal:
    def __init__(self, row=None):
        self.row = row or pod()
        self.calls = []
        self.error = None
        self.ambiguous = False
        self.repo = None

    def search(self, *args):
        return [copy.deepcopy(self.row)]

    def refresh_pod(self, *args):
        return copy.deepcopy(self.row)

    def find_invoice(self, nf):
        self.calls.append('search_nf')
        if self.ambiguous:
            raise ValueError('Busca retornou múltiplas notas.')
        return invoice()

    def map(self, row, inv):
        if self.repo:
            assert self.repo.events[-1][0] == 'MAPEAMENTO_SOLICITADO'
        self.calls.append('map')
        if self.error == 'map':
            raise TimeoutError()
        self.row.values.update(STATUS='MAPPED', POD_STATUS='2', NOTAS_MAPEADAS=inv.nf)
        return 'nota_number:000138810 nota_serie:33 SUCCESS'

    def approve(self, row):
        if self.repo:
            assert self.repo.events[-1][0] == 'ENVIO_SOLICITADO'
        self.calls.append('approve')
        if self.error == 'approve':
            raise TimeoutError()
        return 'Sucesso'


def old(stage):
    return dict(etapa=stage, arquivo='ROBR_SC_000138810.png', nf='000138810',
                serie='33', chave_nota=invoice().key, pod_id='1717057')


class ParserTests(unittest.TestCase):
    def test_prefix_and_pending(self):
        doc = parse_html((FIXTURES/'pods.html').read_text())
        self.assertEqual(len(doc.pods), 6)
        self.assertIsNone(eligibility(doc.pods[0], 'ROBR_SC')[0])
        self.assertEqual(eligibility(doc.pods[3], 'ROBR_SC')[0], '000138810')

    def test_split_invoice_is_one_row(self):
        doc = parse_html((FIXTURES/'invoice.html').read_text())
        self.assertEqual(len(doc.invoices), 1)
        selected = choose_invoice(doc.invoices, '000138810', 'ROBR_SC', 1)
        self.assertEqual(selected.values['POD_RECEIPT_DATE_CHAR'], '03/11/2026')
        self.assertEqual(selected.values['LD_NF_SERIE'], '33')

    def test_mapped_status(self):
        row = parse_html((FIXTURES/'mapped.html').read_text()).pods[0]
        self.assertEqual(row.status, 'MAPPED')
        self.assertIsNone(eligibility(row, 'ROBR_SC')[0])
        self.assertEqual(row.values['NOTAS_MAPEADAS'], '000138810')

    def test_zeroes_and_bad_names(self):
        self.assertEqual(invoice_from_filename('ROBR_SC_000138766.png'), '000138766')
        for filename in ['137554.png', 'XROBR_0001.png', 'ROBR_SC_138_1.png', 'ROBR_SC_teste.png']:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                invoice_from_filename(filename)

    def test_status_and_carrier_both_checked(self):
        for row in [pod(state='APPROVED'), pod(code='2')]:
            self.assertIsNone(eligibility(row, 'ROBR_SC')[0])
        self.assertIsNone(eligibility(pod(), 'ROBR_SP')[0])

    def test_ambiguous_or_wrong_identity(self):
        for total, rows in [(0, []), (2, [invoice(),invoice()])]:
            with self.assertRaises(ValueError):
                choose_invoice(rows, '000138810', 'ROBR_SC', total)
        with self.assertRaises(ValueError):
            choose_invoice([invoice()], '000000001', 'ROBR_SC', 1)

    def test_success_requires_expected_identity(self):
        self.assertTrue(mapping_success('nota_number:000138810 nota_serie:33 SUCCESS &#010', '000138810','33'))
        self.assertFalse(mapping_success('nota_number:000138811 nota_serie:33 SUCCESS','000138810','33'))
        self.assertTrue(approval_success('Sucesso &#010 &#010'))
        self.assertFalse(approval_success('Sucesso parcial; erro no envio'))
        self.assertFalse(approval_success(''))

    def test_login_encoding_matches_captured_algorithm(self):
        js = 'var basicEncryptionPublicKey="4435841";var basicEncryptionGCD="5";var basicEncryptionSeparator="|";'
        encrypted, disguised = encode_password('Ab9!', js)
        self.assertEqual(encrypted, '|'.join(str(pow(ord(x),5,4435841)) for x in 'Ab9!')+'|')
        self.assertEqual(disguised, '****')
        with self.assertRaises(RuntimeError):
            encode_password('test','var bad=1;')

    def test_reversed_dates(self):
        with self.assertRaises(ValueError):
            validate_period(today(), date(2020,1,1))


class FlowTests(unittest.TestCase):
    def run_process(self, repo, portal, dry=False):
        portal.repo = repo
        return process(repo, portal, 'run', SETTINGS, *PERIOD, dry)

    def test_simulation_does_not_write_portal(self):
        repo, portal = Repo(), Portal()
        self.assertFalse(self.run_process(repo, portal, True))
        self.assertEqual(portal.calls, ['search_nf'])
        self.assertEqual(repo.events[-1][0], 'SIMULADO')

    def test_full_flow_records_intent_first(self):
        repo, portal = Repo(), Portal()
        self.assertFalse(self.run_process(repo, portal))
        self.assertEqual(portal.calls, ['search_nf','map','approve'])
        self.assertEqual([x[0] for x in repo.events], ['CONSULTANDO','MAPEAMENTO_SOLICITADO','MAPEADO','ENVIO_SOLICITADO','ENVIADO'])

    def test_numeric_image_never_searched(self):
        repo, portal = Repo(), Portal(pod(name='137554.png'))
        self.run_process(repo, portal)
        self.assertEqual(portal.calls, [])
        self.assertEqual(repo.events[-1][0], 'IGNORADO')

    def test_existing_mapped_without_own_journal_is_ignored(self):
        repo, portal = Repo(), Portal(pod(state='MAPPED',code='2'))
        self.run_process(repo, portal)
        self.assertEqual(portal.calls, [])

    def test_resume_only_verified_mapping(self):
        repo, portal = Repo(old('MAPEADO')), Portal(pod(state='MAPPED',code='2'))
        portal.row.values['NOTAS_MAPEADAS'] = '000138810'
        self.run_process(repo, portal)
        self.assertEqual(portal.calls, ['approve'])

    def test_wrong_mapped_nf_not_sent(self):
        repo, portal = Repo(old('MAPEADO')), Portal(pod(state='MAPPED',code='2'))
        portal.row.values['NOTAS_MAPEADAS'] = '000138811'
        self.run_process(repo, portal)
        self.assertEqual(portal.calls, [])

    def test_uncertain_result_never_retried(self):
        for stage in ['CONFERIR','ENVIO_SOLICITADO','MAPEAMENTO_SOLICITADO']:
            repo, portal = Repo(old(stage)), Portal()
            self.assertTrue(self.run_process(repo, portal))
            self.assertEqual(portal.calls, [])
            self.assertEqual(repo.events[-1][0], 'CONFERIR')

    def test_known_success_never_resent(self):
        repo, portal = Repo(old('ENVIADO')), Portal()
        self.run_process(repo, portal)
        self.assertEqual(portal.calls, [])

    def test_map_timeout_stops_before_approval(self):
        repo, portal = Repo(), Portal()
        portal.error = 'map'
        with self.assertRaises(RuntimeError):
            self.run_process(repo, portal)
        self.assertNotIn('approve', portal.calls)
        self.assertEqual(repo.events[-1][0], 'CONFERIR')

    def test_approval_timeout_is_not_success(self):
        repo, portal = Repo(), Portal()
        portal.error = 'approve'
        with self.assertRaises(RuntimeError):
            self.run_process(repo, portal)
        self.assertEqual(repo.events[-1][0], 'CONFERIR')

    def test_ambiguous_invoice_not_mapped(self):
        repo, portal = Repo(), Portal()
        portal.ambiguous = True
        self.assertTrue(self.run_process(repo, portal))
        self.assertEqual(portal.calls, ['search_nf'])
        self.assertEqual(repo.events[-1][0], 'ERRO_CONSULTA')

    def test_missing_uncertain_pod_is_visible(self):
        repo, portal = Repo(unresolved=[dict(old('ENVIO_SOLICITADO'), pod_id='999')]), Portal()
        self.run_process(repo, portal, True)
        self.assertEqual(repo.events[0], ('CONFERIR','999','000138810'))

    def test_lost_database_lock_blocks_writes(self):
        repo, portal = Repo(), Portal()
        repo.allow = False
        with self.assertRaises(RuntimeError):
            self.run_process(repo, portal)
        self.assertEqual(portal.calls, [])


if __name__ == '__main__':
    unittest.main()
