from datetime import date, datetime
import unittest

from modules.documentos.repository import portal_document_source_key
from modules.documentos.schemas import PortalDocument


def portal_document(report_type: str) -> PortalDocument:
    pending = report_type == "AGUARDANDO_SOLUCAO"
    return PortalDocument(
        report_type=report_type,
        carrier_cnpj="02141029000623",
        ctrc="8180163",
        ctrc_raw="8180163",
        invoice_number="6669655",
        invoice_series="1",
        transport_number="254358781",
        issue_date=date(2026, 9, 10),
        recipient_name="ALEXANDRE JOSE MADEIRA",
        recipient_cnpj="62058318000695",
        recipient_state="RS",
        pending_at=(datetime(2026, 10, 7, 8, 20, 4) if pending else None),
        pending_user=("KARLA" if pending else None),
        pending_reason=("SEM DATA DE ENTREGA" if pending else None),
    )


class PortalDocumentIdentityTest(unittest.TestCase):
    def test_report_type_does_not_change_document_identity(self):
        solucionar = portal_document("SOLUCIONAR")
        pendente = portal_document("AGUARDANDO_SOLUCAO")

        self.assertEqual(
            portal_document_source_key(solucionar),
            portal_document_source_key(pendente),
        )

    def test_invoice_still_changes_document_identity(self):
        first = portal_document("SOLUCIONAR")
        second = portal_document("SOLUCIONAR")
        second.invoice_number = "6669656"

        self.assertNotEqual(
            portal_document_source_key(first),
            portal_document_source_key(second),
        )


if __name__ == "__main__":
    unittest.main()
