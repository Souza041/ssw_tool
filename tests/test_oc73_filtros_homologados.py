import pytest

from modules.ocorrencia_73.parser import filtrar_registros


def registro(ctrc, emissora, cidade, ocorrencia, uf_expedidor):
    return {
        "Cliente Pagador": "CLIENTE SINTETICO",
        "Cidade do Destinatario": cidade,
        "Cidade de Entrega": cidade,
        "Unidade Emissora": emissora,
        "Unidade Receptora": "CWB",
        "Serie/Numero CTRC": ctrc,
        "Codigo Ultima Ocorrencia": ocorrencia,
        "UF do Expedidor": uf_expedidor,
        "UF do Remetente": "PR",
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

    assert len(resultado) == (1 if esperado else 0)

    if esperado:
        assert resultado[0]["fluxo"] == fluxo


@pytest.mark.parametrize(
    "destinatario,entrega,esperado",
    [
        ("CURITIBA", "SAO PAULO", True),
        ("SAO PAULO", "CURITIBA", False),
    ],
)
def test_cwb_utiliza_cidade_do_destinatario(
    destinatario, entrega, esperado
):
    item = registro(
        "CWB999999-1", "CWB", destinatario, "63", "PR"
    )
    item["Cidade de Entrega"] = entrega

    resultado = filtrar_registros([item], fluxo="CWB")

    assert len(resultado) == (1 if esperado else 0)

    if esperado:
        assert resultado[0]["cidade_destinatario"] == destinatario


@pytest.mark.parametrize(
    "expedidor,remetente,esperado",
    [
        ("SC", "PR", True),
        ("PR", "SC", False),
    ],
)
def test_big_utiliza_uf_do_expedidor(
    expedidor, remetente, esperado
):
    item = registro(
        "BIG999999-1", "BIG", "BIGUACU", "64", expedidor
    )
    item["UF do Remetente"] = remetente

    resultado = filtrar_registros([item], fluxo="BIG")

    assert len(resultado) == (1 if esperado else 0)

    if esperado:
        assert resultado[0]["uf_expedidor"] == expedidor
        assert resultado[0]["uf_remetente"] == remetente


@pytest.mark.parametrize(
    "fluxo,coluna",
    [
        ("CWB", "Cidade do Destinatario"),
        ("BIG", "UF do Expedidor"),
    ],
)
def test_ausencia_da_coluna_obrigatoria_bloqueia(fluxo, coluna):
    item = registro(
        "CWB999999-1",
        "CWB",
        "CURITIBA",
        "63" if fluxo == "CWB" else "64",
        "SC",
    )

    # Os campos antigos continuam presentes, mas não podem
    # substituir destinatário ou expedidor.
    del item[coluna]

    with pytest.raises(KeyError):
        filtrar_registros([item], fluxo=fluxo)


def test_big_regra_experimental_bloqueada():
    item = registro(
        "BIG999999-1", "BIG", "BIGUACU", "64", "PR"
    )
    item["Unidade Receptora"] = "BIG"

    with pytest.raises(ValueError, match="experimental"):
        filtrar_registros(
            [item],
            fluxo="BIG",
            regra_big_experimental=True,
        )
