import importlib.util
from datetime import date as D
from pathlib import Path
import unittest

ARQUIVO = Path(__file__).resolve().parents[1] / 'modules/documentos_lg/executar_incremental.py'
spec = importlib.util.spec_from_file_location('incremental', ARQUIVO)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TestLacunas(unittest.TestCase):
    def test_sem_cobertura(self):
        self.assertEqual(list(m.lacunas(D(2026, 10, 22), D(2026, 10, 25), [])),
                         [(D(2026, 10, 22), D(2026, 10, 25))])

    def test_base_concluida_ate_25(self):
        self.assertEqual(list(m.lacunas(D(2024, 1, 1), D(2026, 10, 25),
                         [(D(2024, 1, 1), D(2026, 10, 21)),
                          (D(2026, 10, 22), D(2026, 10, 25))])), [])

    def test_apenas_dia_seguinte(self):
        self.assertEqual(list(m.lacunas(D(2024, 1, 1), D(2026, 10, 26),
                         [(D(2024, 1, 1), D(2026, 10, 25))])),
                         [(D(2026, 10, 26), D(2026, 10, 26))])

    def test_lacuna_no_meio(self):
        self.assertEqual(list(m.lacunas(D(2026, 10, 1), D(2026, 10, 25),
                         [(D(2026, 10, 1), D(2026, 10, 10)),
                          (D(2026, 10, 12), D(2026, 10, 25))])),
                         [(D(2026, 10, 11), D(2026, 10, 11))])

    def test_sobrepostos_fora_de_ordem(self):
        self.assertEqual(list(m.lacunas(D(2026, 10, 1), D(2026, 10, 25),
                         [(D(2026, 10, 10), D(2026, 10, 30)),
                          (D(2026, 9, 1), D(2026, 10, 15))])), [])

    def test_intervalos_fora_do_escopo(self):
        self.assertEqual(list(m.lacunas(D(2026, 10, 22), D(2026, 10, 25),
                         [(D(2026, 9, 1), D(2026, 9, 30)),
                          (D(2026, 11, 1), D(2026, 11, 30))])),
                         [(D(2026, 10, 22), D(2026, 10, 25))])


if __name__ == '__main__':
    unittest.main()
