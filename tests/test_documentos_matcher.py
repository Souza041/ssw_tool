from datetime import date
import unittest

from modules.documentos.schemas import (
    InvoiceReference,
    OP455Document,
    OP930Occurrence,
    PortalDocument,
)
from modules.documentos.services.matcher import DocumentMatcher


def op455(ctrc: str, invoice: str, recipient: str) -> OP455Document:
    return OP455Document(
        ctrc=ctrc,
        ctrc_raw=f"CWB{ctrc}-1",
        cte_number=None,
        document_type=None,
        issue_date=date(2026, 6, 2),
        issuing_unit="CWB",
        receiving_unit="GRU",
        payer_cnpj=None,
        payer_name=None,
        recipient_cnpj=None,
        recipient_name=recipient,
        recipient_state="SP",
        invoices=[InvoiceReference(number=invoice, series="1")],
    )


def portal(invoice: str, recipient: str) -> PortalDocument:
    return PortalDocument(
        report_type="SOLUCIONAR",
        carrier_cnpj="02141029000119",
        ctrc="6140725",
        ctrc_raw="6140725",
        invoice_number=invoice,
        invoice_series="1",
        transport_number="254000001",
        issue_date=date(2026, 6, 2),
        recipient_name=recipient,
        recipient_cnpj=None,
        recipient_state="SP",
    )


class DocumentMatcherTest(unittest.TestCase):
    def test_matches_by_invoice_and_recipient_not_portal_ctrc(self):
        first = op455("404436", "1430501", "CLIENTE CORRETO")
        second = op455("999999", "1430501", "OUTRO CLIENTE")
        occurrence = OP930Occurrence(
            ctrc="404436",
            ctrc_raw="CWB404436-3",
            invoice_number="1430501",
            invoice_series="1",
            occurrence_code="01",
            occurrence_description=None,
            occurrence_complement=None,
            occurrence_at=None,
            included_at=None,
            occurrence_user=None,
            occurrence_company=None,
            occurrence_unit=None,
            delivery_date=None,
            payer_cnpj=None,
            payer_name=None,
        )

        result = DocumentMatcher([first, second], [occurrence]).match(
            portal("1430501", "CLIENTE CORRETO")
        )

        self.assertEqual(result.status, "MATCHED")
        self.assertEqual(result.op455.ctrc, "404436")
        self.assertEqual(len(result.occurrences), 1)


if __name__ == "__main__":
    unittest.main()
