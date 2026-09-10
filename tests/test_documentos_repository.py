from pathlib import Path
import tempfile
import unittest

from modules.documentos.repository import _sha256_values, sha256_file


class DocumentRepositoryTest(unittest.TestCase):
    def test_file_hash_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "report.csv"
            file_path.write_bytes(b"documentos-gce")
            first = sha256_file(file_path)
            second = sha256_file(file_path)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_occurrence_hash_changes_when_identity_changes(self):
        first = _sha256_values("CWB1-1", "100", "01", "2026-06-01")
        repeated = _sha256_values("CWB1-1", "100", "01", "2026-06-01")
        changed = _sha256_values("CWB1-1", "100", "93", "2026-06-01")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
