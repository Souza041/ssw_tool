from datetime import date
import unittest

from modules.documentos.services.daily_pipeline import resolve_period


class ResolvePeriodTests(unittest.TestCase):
    def test_default_period_uses_inclusive_overlap(self):
        period = resolve_period(date(2026, 9, 10), 7)
        self.assertEqual(period.start, date(2026, 9, 4))
        self.assertEqual(period.end, date(2026, 9, 10))

    def test_explicit_period_is_preserved(self):
        period = resolve_period(
            date(2026, 9, 10),
            7,
            date(2026, 8, 1),
            date(2026, 8, 31),
        )
        self.assertEqual(period.start, date(2026, 8, 1))
        self.assertEqual(period.end, date(2026, 8, 31))

    def test_requires_both_explicit_dates(self):
        with self.assertRaises(ValueError):
            resolve_period(date(2026, 9, 10), 7, date(2026, 9, 1), None)

    def test_rejects_inverted_period(self):
        with self.assertRaises(ValueError):
            resolve_period(
                date(2026, 9, 10),
                7,
                date(2026, 9, 10),
                date(2026, 9, 1),
            )


if __name__ == "__main__":
    unittest.main()
