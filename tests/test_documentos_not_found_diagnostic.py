from datetime import date
from pathlib import Path
import csv
import tempfile
import unittest

from modules.documentos.services.not_found_diagnostic import (
    NotFoundDiagnostic,
    classify_not_found,
    export_diagnostics_csv,
)


class NotFoundDiagnosticTest(unittest.TestCase):
    def test_classification_priority(self):
        cause, _ = classify_not_found(
            op455_invoice_candidates=2,
            op455_exact_ctrc_candidates=1,
            op930_invoice_occurrences=3,
            op930_ctrc_occurrences=4,
        )
        self.assertEqual(cause, "MULTIPLOS_CTRCS_NA_OP455")

    def test_absent_from_both_ssw_sources(self):
        cause, _ = classify_not_found(
            op455_invoice_candidates=0,
            op455_exact_ctrc_candidates=0,
            op930_invoice_occurrences=0,
            op930_ctrc_occurrences=0,
        )
        self.assertEqual(cause, "AUSENTE_NAS_BASES_SSW")

    def test_csv_is_excel_compatible(self):
        row = NotFoundDiagnostic(
            1, "SOLUCIONAR", "123", "1", "456", "000456",
            date(2026, 9, 10), "12345678000199", "CLIENTE Ç",
            0, "", "", "", 0, 0, "", 0,
            "AUSENTE_NAS_BASES_SSW", "Ampliar período.",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostico.csv"
            summary = export_diagnostics_csv([row], path)
            self.assertEqual(summary["total"], 1)
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as handle:
                parsed = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(parsed[0]["Destinatário Portal"], "CLIENTE Ç")


if __name__ == "__main__":
    unittest.main()
