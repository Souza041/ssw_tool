import pytest

from modules.ocorrencia_73.parser import filtrar_registros


def registro(ctrc, emissora, cidade, ocorrencia, uf):
    return {
        "Cliente Pagador": "CLIENTE SINTETICO",
        "Cidade de Entrega": cidade,
        "Unidade Emissora": emissora,
        "Unidade Receptora": "CWB",
        "Serie/Numero CTRC": ctrc,
        "Codigo Ultima Ocorrencia": ocorrencia,
        "UF do Remetente": uf,
    }


@pytest.mark.parametrize(
    "fluxo,item,esperado",
    [
        ("CWB", registro("CWB100001-1", "CWB", "CURITIBA", "63", "PR"), True),
        ("CWB", registro("CWB100002-1", "GRU", "PINHAIS", "63", "SP"), True),
        ("CWB", registro("CWB100003-1", "CWB", "CURITIBA", "64", "PR"), False),
        ("CWB", registro("CWB100004-1", "CWB", "SAO PAULO", "63", "SP"), False),
        ("CWB", registro("CWB100005-1", "XXX", "CURITIBA", "63", "PR"), False),
        ("BIG", registro("BIG200001-1", "BIG", "BIGUACU", "64", "SC"), True),
        ("BIG", registro("BIG200002-1", "JOI", "FLORIANOPOLIS", "64", "SC"), True),
        ("BIG", registro("BIG200003-1", "BIG", "BIGUACU", "64", "PR"), False),
        ("BIG", registro("BIG200004-1", "BIG", "BIGUACU", "63", "SC"), False),
    ],
)
def test_regras_oc73_homologadas(fluxo, item, esperado):
    resultado = filtrar_registros([item], fluxo=fluxo)

    assert (len(resultado) == 1) == esperado

    if esperado:
        assert resultado[0]["fluxo"] == fluxo


def test_cidade_entrega_tem_prioridade():
    item = registro(
        "CWB999999-1", "CWB", "CURITIBA", "63", "PR"
    )
    item["Cidade do Destinatario"] = "SAO PAULO"

    resultado = filtrar_registros([item], fluxo="CWB")

    assert len(resultado) == 1
    assert resultado[0]["cidade_destinatario"] == "CURITIBA"