from datetime import date, datetime
import unittest

from modules.documentos.services.normalizer import (
    combine_datetime,
    normalize_cnpj,
    normalize_ctrc,
    normalize_occurrence_code,
    parse_invoice_reference,
    split_grouped,
)


class DocumentNormalizerTest(unittest.TestCase):
    def test_normalize_ctrc_from_ssw_and_portal(self):
        self.assertEqual(normalize_ctrc("APU404986-1"), "404986")
        self.assertEqual(normalize_ctrc("008081527"), "8081527")

    def test_invoice_with_and_without_series(self):
        complete = parse_invoice_reference("1/632014")
        without_series = parse_invoice_reference("/1026")
        self.assertEqual((complete.series, complete.number), ("1", "632014"))
        self.assertEqual((without_series.series, without_series.number), (None, "1026"))

    def test_grouped_values(self):
        self.assertEqual(split_grouped("254277739 ,65031004,"), ["254277739", "65031004"])

    def test_cnpj_and_occurrence(self):
        self.assertEqual(normalize_cnpj("02.141.029/0001-19"), "02141029000119")
        self.assertEqual(normalize_occurrence_code("1"), "01")

    def test_combine_datetime(self):
        self.assertEqual(
            combine_datetime("03/06/26", "23:59"),
            datetime(2026, 6, 3, 23, 59),
        )


if __name__ == "__main__":
    unittest.main()

