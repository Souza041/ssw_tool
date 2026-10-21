import pytest

from modules.ocorrencia_73.service import (
    obter_ultima_ocorrencia_op101,
)
from operations.op101.ocorrencias import OcorrenciaHistorico


def oc(codigo, data="20/10/26 10:00"):
    return OcorrenciaHistorico(
        codigo=codigo,
        descricao="TESTE SINTETICO",
        data_hora=data,
        familia="ROD",
        unidade="CWB",
        usuario="TESTE",
        complemento="",
        ocorrencia_ssw="",
    )


@pytest.mark.parametrize(
    "nome,historico,esperado",
    [
        ("empate_63_47", [oc("63"), oc("47")], None),
        ("empate_47_63", [oc("47"), oc("63")], None),
        ("empate_63_63", [oc("63"), oc("63")], "63"),
        (
            "empate_antigo",
            [
                oc("47", "20/10/26 09:00"),
                oc("64", "20/10/26 09:00"),
                oc("63", "20/10/26 10:00"),
            ],
            "63",
        ),
        (
            "data_invalida",
            [oc("47", "INVALIDA"), oc("63")],
            None,
        ),
        ("historico_vazio", [], None),
    ],
)
def test_ultima_ocorrencia_segura(nome, historico, esperado):
    resultado = obter_ultima_ocorrencia_op101(historico)

    obtido = resultado.codigo if resultado else None

    assert obtido == esperado, nome