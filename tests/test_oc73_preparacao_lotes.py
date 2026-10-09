from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.ocorrencia_73.service import Ocorrencia73Service


class OP455Simulada:
    def __init__(self):
        self.chamadas = []

    def gerar_e_baixar_ocorrencia_73(self, **kwargs):
        self.chamadas.append(kwargs)
        return Path(f"{kwargs['unidade'].lower()}.sswweb")


def executar_preparacao(data):
    op455 = OP455Simulada()
    service = Ocorrencia73Service()

    with patch(
        "modules.ocorrencia_73.service.carregar_relatorio",
        return_value=[],
    ):
        resultado = service.preparar_lotes_oc73(
            op455=op455,
            data_referencia=data,
            diretorio_original=Path("downloads/ocorrencia_73/teste"),
        )

    return op455.chamadas, resultado


def test_segunda_gera_cwb_e_big():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 12)
    )

    assert len(chamadas) == 2
    assert [c["unidade"] for c in chamadas] == ["CWB", "BIG"]

    for chamada in chamadas:
        assert chamada["data_inicial"] == "121026"
        assert chamada["data_final"] == "121026"

    assert resultado["total_filtrado"] == 0


def test_sabado_nao_gera_relatorios():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 10)
    )

    assert chamadas == []
    assert resultado["lotes"] == []


def test_domingo_consulta_sabado_e_domingo():
    chamadas, resultado = executar_preparacao(
        date(2026, 10, 11)
    )

    assert len(chamadas) == 1
    assert chamadas[0]["unidade"] == "CWB"
    assert chamadas[0]["data_inicial"] == "101026"
    assert chamadas[0]["data_final"] == "111026"
    assert resultado["total_filtrado"] == 0


def test_falha_download_nao_vira_zero_elegiveis():
    service = Ocorrencia73Service()

    class OP455ComErro:
        def gerar_e_baixar_ocorrencia_73(self, **kwargs):
            raise RuntimeError("Falha simulada na OP455")

    try:
        service.preparar_lotes_oc73(
            op455=OP455ComErro(),
            data_referencia=date(2026, 10, 12),
            diretorio_original=Path(
                "downloads/ocorrencia_73/teste"
            ),
        )
    except RuntimeError as erro:
        assert "Falha simulada" in str(erro)
    else:
        raise AssertionError(
            "A falha da OP455 não foi propagada."
        )