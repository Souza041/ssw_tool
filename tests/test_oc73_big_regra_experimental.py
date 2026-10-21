from modules.ocorrencia_73.parser import filtrar_registros


def registro(
    ctrc,
    ocorrencia,
    receptora,
    uf,
    emissora="JOI",
):
    return {
        "Serie/Numero CTRC": ctrc,
        "Codigo Ultima Ocorrencia": ocorrencia,
        "Unidade Receptora": receptora,
        "UF do Remetente": uf,
        "Unidade Emissora": emissora,
        "Cidade de Entrega": "CURITIBA",
        "Cliente Pagador": "TESTE",
    }


def test_big_experimental_aceita_mg_sp():
    registros = [
        registro("JOI123456", "64", "BIG", "MG"),
        registro("JOI123457", "64", "BIG", "SP"),
    ]

    resultado = filtrar_registros(
        registros,
        fluxo="BIG",
        regra_big_experimental=True,
    )

    assert len(resultado) == 2


def test_big_experimental_exige_receptora_big():
    registros = [
        registro("JOI123456", "64", "JOI", "MG"),
    ]

    resultado = filtrar_registros(
        registros,
        fluxo="BIG",
        regra_big_experimental=True,
    )

    assert resultado == []


def test_big_experimental_exige_oc64():
    registros = [
        registro("JOI123456", "63", "BIG", "MG"),
    ]

    resultado = filtrar_registros(
        registros,
        fluxo="BIG",
        regra_big_experimental=True,
    )

    assert resultado == []


def test_big_padrao_continua_exigindo_sc():
    registros = [
        registro("JOI123456", "64", "BIG", "MG"),
    ]

    resultado = filtrar_registros(
        registros,
        fluxo="BIG",
    )

    assert resultado == []


def test_cwb_nao_aceita_oc64():
    registros = [
        registro("CWB123456", "64", "CWB", "PR"),
    ]

    resultado = filtrar_registros(
        registros,
        fluxo="CWB",
        regra_big_experimental=True,
    )

    assert resultado == []