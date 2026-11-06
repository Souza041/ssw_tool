import pytest

from modules.ocorrencia_73.parser import filtrar_registros


def registro(uf, ocorrencia="64", receptora="BIG"):
    return {
        "Serie/Numero CTRC": "JOI123456",
        "Codigo Ultima Ocorrencia": ocorrencia,
        "Unidade Receptora": receptora,
        "UF do Expedidor": uf,
        "Unidade Emissora": "JOI",
        "Cidade do Destinatario": "FLORIANOPOLIS",
        "Cliente Pagador": "TESTE",
    }


@pytest.mark.parametrize(
    "fluxo,uf,ocorrencia,receptora",
    [
        ("BIG", "MG", "64", "BIG"),
        ("BIG", "SP", "64", "BIG"),
        ("BIG", "MG", "64", "JOI"),
        ("BIG", "MG", "63", "BIG"),
        ("CWB", "PR", "64", "CWB"),
    ],
)
def test_regra_experimental_permanece_bloqueada(
    fluxo, uf, ocorrencia, receptora
):
    with pytest.raises(ValueError, match="experimental desativada"):
        filtrar_registros(
            [registro(uf, ocorrencia, receptora)],
            fluxo=fluxo,
            regra_big_experimental=True,
        )


@pytest.mark.parametrize("uf,esperado", [("SC", 1), ("MG", 1), ("SP", 1)])
def test_big_padrao_independe_de_uf(uf, esperado):
    item = registro(uf)
    # UF do Remetente divergente não deve decidir a elegibilidade.
    item["UF do Remetente"] = "MG" if uf == "SC" else "SC"

    resultado = filtrar_registros([item], fluxo="BIG")

    assert len(resultado) == esperado


def test_cwb_nao_aceita_oc64():
    resultado = filtrar_registros([registro("PR")], fluxo="CWB")
    assert resultado == []
