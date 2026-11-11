from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
import unittest
from urllib.parse import urlsplit,parse_qs,parse_qsl
from unittest import mock
import xml.etree.ElementTree as ET

import requests
from modules.lg_pod.portal import Portal,PortalError,encode_password
from modules.lg_pod.config import Settings
from modules.lg_pod.jda_form import JDAForm

FIX = Path(__file__).parent/'fixtures/http'
SCRIPT = 'var basicEncryptionPublicKey="4435841";var basicEncryptionGCD="5";var basicEncryptionSeparator="|";'
SETTINGS = Settings('HTTP_TEST','dummy-password','ROBR_SC',date(2025,1,1),True)
PERIOD = (date(2026,11,3),date(2026,11,4))


def fixture(name):return (FIX/(name+'.html')).read_text()


def response(request,body='',status=200,headers=None):
    result=requests.Response()
    result.status_code=status
    result.url=request.url
    result.request=request
    result.headers.update({'Content-Type':'text/html; charset=utf-8'})
    result.headers.update(headers or {})
    result._content=body.encode('utf-8')
    result._content_consumed=True
    result.encoding='utf-8'
    return result


class ReplaySession(requests.Session):
    """Usa preparação real do Requests, substitui SEND antes de qualquer rede."""
    def __init__(self):
        super().__init__()
        self.calls=[]
        self.mapped=False
        self.approved=False
        self.mode='normal'
        self.active='pending'
        self.hook=None

    def send(self, request, **kwargs):
        self.calls.append((request,kwargs))
        if self.hook:
            result=self.hook(request)
            if result is not None:return result
        path=urlsplit(request.url).path
        if path=='/tm/admin/LoginView.jsp':
            self.cookies.set('JSESSIONID','test-session')
            return response(request,'<form>Login</form>')
        if path.endswith('i2tmgenerated.js'):return response(request,SCRIPT)
        if path.endswith('LoginViewController.jsp'):
            assert 'JSESSIONID=test-session' in request.headers.get('Cookie','')
            return response(request,status=302,headers={'Location':'/tm/entry/main.gsm'})
        if path=='/tm/entry/main.gsm':return response(request,'Main')
        if path=='/base/start.x2ps':return response(request,fixture('start'))
        if path!='/base/view.x2ps':raise AssertionError('Rota não gravada')
        action=parse_qs(urlsplit(request.url).query)['action'][0]
        xml=ET.fromstring(request.body)
        assert xml.find('BUTTON_ID').get('Value')==action
        assert xml.find('AJAX_UPDATE').get('Value')=='true'
        if action=='SearchLeft_button':
            return response(request,fixture('mapped' if self.mapped else 'pending'))
        if action=='SearchRight_button':
            return response(request,fixture('not_found' if self.mode=='not_found' else 'pending'))
        if action=='Map':
            if self.mode=='map_timeout':raise requests.exceptions.ReadTimeout('test only')
            self.mapped=True
            return response(request,fixture('map_success'))
        if action=='btnApprove':
            if self.mode=='approve_timeout':raise requests.exceptions.ConnectionError('test only')
            self.approved=True
            return response(request,fixture('approve_success'))
        raise AssertionError('Ação não gravada: '+action)

    def actions(self):
        return [parse_qs(urlsplit(r.url).query)['action'][0] for r,k in self.calls
                if urlsplit(r.url).path=='/base/view.x2ps']

    def action_xml(self, action):
        return ET.fromstring(next(r.body for r,k in self.calls
            if parse_qs(urlsplit(r.url).query).get('action')==[action]))


class FormTests(unittest.TestCase):
    def test_payload_selects_exactly_one_pod_and_one_invoice(self):
        form=JDAForm(fixture('pending'))
        key='000138810|33|ROBR_SC|20261029'
        root=ET.fromstring(form.xml('Map',selected_left='1717057',selected_right=key))
        left=root.findall('./DATA/pod_table_list/pod_table_row')
        right=root.findall('./DATA/nota_table_list/nota_table_row')
        self.assertEqual(len(left),1)
        self.assertEqual(len(right),1)
        self.assertEqual(left[0].find('SELECTED_KEY_LEFT').get('Checked'),'true')
        self.assertEqual(root.find('SELECTED_KEY_LEFT').get('Value'),'1717057')
        self.assertEqual(right[0].find('SELECTED_KEY_CD').get('Value'),key)
        self.assertEqual(right[0].find('POD_RECEIPT_DATE_CHAR').get('Value'),'03/11/2026')
        self.assertEqual(right[0].find('PROTOCOL_NUMBER_IN').get('Value'),'PROTOCOLO_TESTE')

    def test_consult_clears_all_checkboxes_and_global_selection(self):
        root=ET.fromstring(JDAForm(fixture('pending')).xml('SearchRight_button'))
        self.assertIsNone(root.find('SELECTED_KEY_LEFT'))
        self.assertIsNone(root.find('SELECTED_KEY_CD'))
        self.assertTrue(all(n.get('Checked')=='false' for n in root.findall('.//*[@Checked]')))

    def test_xml_escapes_remarks_and_preserves_zeroes(self):
        form=JDAForm(fixture('pending'))
        text='Texto " & < > com acento ç e\nlinha'
        form.set('CARRIER_REMARK',text)
        root=ET.fromstring(form.xml('Map',selected_left='1717057',selected_right='000138810|33|ROBR_SC|20261029'))
        self.assertEqual(root.find('.//nota_table_row/CARRIER_REMARK').get('Value'),text)
        self.assertEqual(root.find('.//nota_table_row/LD_NF_NO').get('Value'),'000138810')

    def test_unknown_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            JDAForm(fixture('pending')).xml('Map',selected_left='999',selected_right='wrong')

    def test_duplicate_root_fields_are_preserved(self):
        root=ET.fromstring(JDAForm(fixture('start')).xml('SearchLeft_button'))
        self.assertEqual(len(root.findall('./DATA/DO_SEARCH')),2)
        self.assertEqual(len(root.findall('./DATA/VISIBILITY')),2)

    def test_invalid_xml_characters_are_rejected(self):
        form=JDAForm(fixture('pending'));form.set('NF_NO','bad\x00value')
        with self.assertRaises(ValueError):form.xml('SearchRight_button')


class HttpPortalTests(unittest.TestCase):
    def portal(self,session,settings=SETTINGS):return Portal(settings,session_factory=lambda:session)

    def test_login_uses_encrypted_password_cookie_and_runtime_workflow(self):
        session=ReplaySession()
        with self.portal(session) as portal:portal.login()
        request=next(r for r,k in session.calls if r.url.endswith('LoginViewController.jsp'))
        form=dict(parse_qsl(request.body))
        self.assertEqual(form['loginPassword'],encode_password(SETTINGS.password,SCRIPT)[0])
        self.assertEqual(form['dspLoginPassword'],'*'*len(SETTINGS.password))
        self.assertNotIn(SETTINGS.password,request.body)
        self.assertTrue(all(k['verify'] is True for r,k in session.calls))
        self.assertTrue(all(k['allow_redirects'] is False for r,k in session.calls))

    def test_consult_has_no_write_actions_and_keeps_nf_identity(self):
        session=ReplaySession()
        with self.portal(session) as portal:
            portal.login();pods=portal.search(*PERIOD);nf=portal.find_invoice('000138810')
        self.assertEqual(len(pods),1)
        self.assertEqual(nf.nf,'000138810')
        self.assertEqual(session.actions(),['SearchLeft_button','SearchRight_button'])

    def test_full_map_refresh_approve_uses_only_confirmed_identity(self):
        session=ReplaySession()
        with self.portal(session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            self.assertIn('SUCCESS',portal.map(pod,nf))
            mapped=portal.refresh_pod(pod.pod_id)
            self.assertEqual(mapped.status,'MAPPED')
            self.assertIn('Sucesso',portal.approve(mapped))
        approve=session.action_xml('btnApprove')
        self.assertEqual(approve.find('SELECTED_KEY_LEFT').get('Value'),'1717057')
        self.assertIsNone(approve.find('SELECTED_KEY_CD'))
        self.assertEqual(approve.find('.//pod_table_row/POD_STATUS').get('Value'),'2')

    def test_nf_not_found_remains_query_error(self):
        session=ReplaySession();session.mode='not_found'
        with self.portal(session) as portal:
            portal.login();portal.search(*PERIOD)
            with self.assertRaisesRegex(ValueError,'NF não encontrada'):portal.find_invoice('000138516')
        self.assertNotIn('Map',session.actions())

    def test_disabled_send_does_not_post_map(self):
        session=ReplaySession()
        with self.portal(session,replace(SETTINGS,send_enabled=False)) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            with self.assertRaisesRegex(PortalError,'desabilitado'):portal.map(pod,nf)
        self.assertNotIn('Map',session.actions())

    def test_map_timeout_never_retries_or_approves(self):
        session=ReplaySession();session.mode='map_timeout'
        with self.portal(session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            with self.assertRaises(PortalError):portal.map(pod,nf)
            with self.assertRaises(PortalError):portal.map(pod,nf)
        self.assertEqual(session.actions().count('Map'),1)
        self.assertNotIn('btnApprove',session.actions())

    def test_approval_timeout_never_retries(self):
        session=ReplaySession();session.mode='approve_timeout'
        with self.portal(session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            portal.map(pod,nf);mapped=portal.refresh_pod(pod.pod_id)
            with self.assertRaises(PortalError):portal.approve(mapped)
            with self.assertRaises(PortalError):portal.approve(mapped)
        self.assertEqual(session.actions().count('btnApprove'),1)

    def test_redirect_on_write_is_not_followed(self):
        session=ReplaySession()
        def hook(req):
            if parse_qs(urlsplit(req.url).query).get('action')==['Map']:
                return response(req,status=302,headers={'Location':'/tm/admin/LoginView.jsp'})
        session.hook=hook
        with self.portal(session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            with self.assertRaisesRegex(PortalError,'Redirecionamento'):portal.map(pod,nf)
        self.assertEqual(session.actions().count('Map'),1)

    def test_cross_origin_redirect_does_not_send_credentials_elsewhere(self):
        session=ReplaySession()
        session.hook=lambda req: response(req,status=302,headers={'Location':'https://other.invalid/'})
        with self.portal(session) as portal:
            with self.assertRaisesRegex(PortalError,'outro servidor'):portal.login()
        self.assertEqual(len(session.calls),1)

    def test_expired_session_200_login_page_is_rejected(self):
        session=ReplaySession()
        def hook(req):
            if urlsplit(req.url).path=='/base/view.x2ps':return response(req,'<form>Login</form>')
        session.hook=hook
        with self.portal(session) as portal:
            portal.login()
            with self.assertRaisesRegex(PortalError,'sessão expirada'):portal.search(*PERIOD)

    def test_success_for_wrong_nf_is_rejected(self):
        session=ReplaySession()
        def hook(req):
            if parse_qs(urlsplit(req.url).query).get('action')==['Map']:
                return response(req,fixture('map_success').replace('nota_number:000138810','nota_number:000000000'))
        session.hook=hook
        with self.portal(session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            with self.assertRaisesRegex(PortalError,'não confirmou'):portal.map(pod,nf)
        self.assertNotIn('btnApprove',session.actions())

    def test_workflow_change_is_rejected(self):
        session=ReplaySession()
        def hook(req):
            if urlsplit(req.url).path=='/base/view.x2ps':
                return response(req,fixture('pending').replace('WORKFLOW_TEST','WORKFLOW_OTHER'))
        session.hook=hook
        with self.portal(session) as portal:
            portal.login()
            with self.assertRaises(PortalError):portal.search(*PERIOD)

    def test_certificate_failure_has_safe_message(self):
        session=ReplaySession()
        def hook(req):raise requests.exceptions.SSLError('private details')
        session.hook=hook
        with self.portal(session) as portal:
            with self.assertRaisesRegex(PortalError,'LG_POD_CA_BUNDLE') as exc:portal.login()
        self.assertNotIn('private details',str(exc.exception))

    def test_proxy_credentials_are_quoted_and_no_ambient_netrc(self):
        session=ReplaySession()
        settings=replace(SETTINGS,proxy_server='http://proxy.invalid:3128',proxy_user='a@b',proxy_password='x/y',ca_bundle='corporate.pem')
        with self.portal(session,settings) as portal:
            portal.login()
            self.assertEqual(session.proxies['https'],'http://a%40b:x%2Fy@proxy.invalid:3128')
            self.assertFalse(session.trust_env)
        self.assertTrue(all(k['verify']=='corporate.pem' for r,k in session.calls))




class PaginationTests(unittest.TestCase):
    def prepare(self, session, *, total=2, duplicate=False, changed=False, empty=False):
        full=fixture('pending').replace('name="RECORD_COUNT" path="/DATA/pod_table_list" type="hidden" value="1"',
                 'name="RECORD_COUNT" path="/DATA/pod_table_list" type="hidden" value="2"')
        # Extrair a única linha e metadados como os fragmentos reais SYS_REFRESH.
        from re import search
        row=search(r'<table><tr path="/DATA/pod_table_list/pod_table_row/3">.*?</tr></table>',full,flags=16).group(0)
        row=row.replace('/pod_table_row/3','/pod_table_row/50')
        if not duplicate:row=row.replace('1717057','9999999').replace('ROBR_SC_000138810.png','9999999.png')
        fragment='''<div id="MPpb">
            <input name="START_COUNT" path="/DATA/pod_table_list" value="1">
            <input name="RECORD_COUNT" path="/DATA/pod_table_list" value="%s">
            <input name="MAX_ROWS" path="/DATA/pod_table_list" value="50">
            <input name="NO_OF_ROWS" path="/DATA/pod_table_list" value="1">%s</div>''' % (
                3 if changed else total,'' if empty else row)
        def hook(req):
            action=parse_qs(urlsplit(req.url).query).get('action')
            if action==['SearchLeft_button']:return response(req,full)
            if action==['SYS_REFRESH']:return response(req,fragment)
        session.hook=hook

    def test_second_page_is_merged_and_current_xml_contains_both_ids(self):
        session=ReplaySession();self.prepare(session)
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login();rows=portal.search(*PERIOD)
            self.assertEqual([r.pod_id for r in rows],['1717057','9999999'])
            xml=ET.fromstring(portal.form.xml('Map',selected_left='9999999'))
            appended=xml.find('.//pod_table_row/ID_POD[@Value="9999999"]/..')
            self.assertEqual(appended.find('ID_POD').get('OldValue'),'')
            self.assertEqual(appended.find('SELECTED_KEY_LEFT').get('OldChecked'),'')
            refresh=session.action_xml('SYS_REFRESH')
            self.assertEqual(refresh.find('START_COUNT').get('Value'),'1')
            self.assertEqual(refresh.find('COMPONENT_NBR').get('Value'),'undefined')
        self.assertEqual(session.actions(),['SearchLeft_button','SYS_REFRESH'])

    def test_duplicate_id_fails_before_any_map(self):
        session=ReplaySession();self.prepare(session,duplicate=True)
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login()
            with self.assertRaisesRegex(PortalError,'duplicados'):portal.search(*PERIOD)
        self.assertNotIn('Map',session.actions())

    def test_changed_total_is_not_silently_accepted(self):
        session=ReplaySession();self.prepare(session,changed=True)
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login()
            with self.assertRaisesRegex(PortalError,'mudou'):portal.search(*PERIOD)

    def test_no_page_progress_is_rejected(self):
        session=ReplaySession();self.prepare(session,empty=True)
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login()
            with self.assertRaisesRegex(PortalError,'não avançou'):portal.search(*PERIOD)

    def test_workflow_operates_without_playwright_available(self):
        session=ReplaySession()
        with mock.patch.dict(sys.modules,{'playwright':None,'playwright.sync_api':None}):
            with Portal(SETTINGS,session_factory=lambda:session) as portal:
                portal.login();rows=portal.search(*PERIOD)
        self.assertEqual(len(rows),1)


class MutationBoundaryTests(unittest.TestCase):
    def test_low_level_write_requires_explicit_single_selection(self):
        session=ReplaySession()
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login()
            for action in ('Map','btnApprove'):
                with self.assertRaises(PortalError):portal.action(action)
        self.assertEqual(session.actions(),[])

    def test_multiple_mapped_nfs_are_not_approved(self):
        session=ReplaySession()
        with Portal(SETTINGS,session_factory=lambda:session) as portal:
            portal.login();pod=portal.search(*PERIOD)[0];nf=portal.find_invoice('000138810')
            portal.map(pod,nf);mapped=portal.refresh_pod(pod.pod_id)
            mapped.values['NOTAS_MAPEADAS']='000138810 000138811'
            with self.assertRaises(PortalError):portal.approve(mapped)
        self.assertNotIn('btnApprove',session.actions())


if __name__ == '__main__':unittest.main()
