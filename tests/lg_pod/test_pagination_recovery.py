"""Recomeçar somente consultas quando o portal altera a paginação."""
import unittest
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
import requests
from modules.lg_pod.portal import Portal, PortalError, PaginationChanged
import test_http_portal as http
from test_http_portal import ReplaySession, SETTINGS, PERIOD, response


class PaginationRecoveryTests(unittest.TestCase):
    def hooks(self, session):
        helper = http.PaginationTests()
        helper.prepare(session, changed=True)
        changed = session.hook
        helper.prepare(session)
        stable = session.hook
        return changed, stable

    def portal(self, session):
        portal = Portal(SETTINGS, lambda: session)
        portal.__enter__()
        self.addCleanup(portal.__exit__)
        portal.login()
        return portal

    def test_search_discards_partial_pages_and_restarts(self):
        session = ReplaySession()
        changed, stable = self.hooks(session)
        calls = 0
        def hook(req):
            nonlocal calls
            action = parse_qs(urlsplit(req.url).query).get('action')
            if action == ['SYS_REFRESH']:
                calls += 1
                return (changed if calls == 1 else stable)(req)
            return stable(req)
        session.hook = hook
        portal = self.portal(session)
        rows = portal.search(*PERIOD)
        self.assertEqual([r.pod_id for r in rows], ['1717057', '9999999'])
        self.assertEqual(session.actions(), ['SearchLeft_button','SYS_REFRESH','SearchLeft_button','SYS_REFRESH'])
        self.assertIsNone(portal.mutation_attempted)
        self.assertEqual(len(portal.doc.pods), 2)

    def test_constant_change_stops_after_three_read_attempts(self):
        session = ReplaySession()
        changed, stable = self.hooks(session)
        session.hook = changed
        portal = self.portal(session)
        with self.assertRaisesRegex(PaginationChanged, '2/3'):
            portal.search(*PERIOD)
        self.assertEqual(session.actions().count('SearchLeft_button'), 3)
        self.assertEqual(session.actions().count('SYS_REFRESH'), 3)
        self.assertNotIn('Map', session.actions())

    def mapping_ready(self, *, constant=False, wrong_invoice=False):
        session = ReplaySession()
        changed, stable = self.hooks(session)
        portal = self.portal(session)
        row = portal.search(*PERIOD)[0]
        invoice = portal.find_invoice('000138810')
        refresh_count = 0
        right_count = 0
        def hook(req):
            nonlocal refresh_count, right_count
            action = parse_qs(urlsplit(req.url).query).get('action')
            if action == ['SYS_REFRESH']:
                refresh_count += 1
                return (changed if constant or refresh_count == 1 else stable)(req)
            if action == ['SearchRight_button']:
                right_count += 1
                # Mesmo redesenho completo, agora com a lista da esquerda em 2 páginas.
                from types import SimpleNamespace
                fake_req = SimpleNamespace(url=req.url.replace('SearchRight_button', 'SearchLeft_button'))
                full = stable(fake_req).text
                if wrong_invoice:
                    full = full.replace('000138810|33|ROBR_SC|20261029', '000138810|44|ROBR_SC|20261029').replace('value="33"', 'value="44"')
                return response(req, full)
            return stable(req)
        session.hook = hook
        # Estado após SearchRight: primeira página apenas, duas linhas esperadas.
        portal.action('SearchLeft_button')
        session.calls.clear()
        return portal, session, row, invoice

    def test_map_requeries_then_sends_exactly_once(self):
        portal, session, row, invoice = self.mapping_ready()
        portal.map(row, invoice)
        actions = session.actions()
        self.assertEqual(actions.count('Map'), 1)
        self.assertEqual(actions[-4:], ['SearchLeft_button', 'SearchRight_button', 'SYS_REFRESH', 'Map'])
        self.assertIs(portal.mutation_attempted, True)
        self.assertNotIn('btnApprove', actions)
        xml = session.action_xml('Map')
        selected = xml.findall('.//SELECTED_KEY_LEFT[@Checked="true"]')
        self.assertEqual([v.get('Value') for v in selected], ['1717057'])

    def test_map_constant_change_never_sends(self):
        portal, session, row, invoice = self.mapping_ready(constant=True)
        with self.assertRaises(PaginationChanged):
            portal.map(row, invoice)
        self.assertIs(portal.mutation_attempted, False)
        self.assertEqual(session.actions().count('SYS_REFRESH'), 3)
        self.assertNotIn('Map', session.actions())

    def test_changed_invoice_identity_is_not_mapped(self):
        portal, session, row, invoice = self.mapping_ready(wrong_invoice=True)
        with self.assertRaises((PortalError, ValueError)):
            portal.map(row, invoice)
        self.assertIs(portal.mutation_attempted, False)
        self.assertNotIn('Map', session.actions())

    def test_network_failure_is_not_retried_as_list_change(self):
        session = ReplaySession()
        changed, stable = self.hooks(session)
        def hook(req):
            if parse_qs(urlsplit(req.url).query).get('action') == ['SYS_REFRESH']:
                raise requests.exceptions.ReadTimeout('test')
            return stable(req)
        session.hook = hook
        portal = Portal(replace(SETTINGS, read_attempts=1), lambda: session)
        portal.__enter__()
        self.addCleanup(portal.__exit__)
        portal.login()
        with self.assertRaises(PortalError): portal.search(*PERIOD)
        self.assertEqual(session.actions().count('SearchLeft_button'), 1)
        self.assertEqual(session.actions().count('SYS_REFRESH'), 1)

    def test_target_disappears_after_restart_no_map(self):
        portal, session, row, invoice = self.mapping_ready()
        initial_hook = session.hook
        def hook(req):
            result = initial_hook(req)
            action = parse_qs(urlsplit(req.url).query).get('action')
            if result is not None and action in [['SearchLeft_button'], ['SearchRight_button']]:
                return response(req, result.text.replace('1717057', '1234567'))
            return result
        session.hook = hook
        with self.assertRaisesRegex(PortalError, 'comprovante mudou'):
            portal.map(row, invoice)
        self.assertNotIn('Map', session.actions())
        self.assertIs(portal.mutation_attempted, False)


if __name__ == '__main__': unittest.main()
