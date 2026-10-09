from modules.ocorrencia_73.parser import filtrar_registros


def registro(ctrc, emissora, cidade, uf, ocorrencia):
    return {
        "Serie/Numero CTRC": ctrc,
        "Unidade Emissora": emissora,
        "Cidade do Destinatario": cidade,
        "UF do Remetente": uf,
        "Codigo da Ultima Ocorrencia": ocorrencia,
    }


def test_filtro_cwb():
    base = [
        registro("CWB100001-1", "BHZ", "Curitiba", "PR", "63"),
        registro("CWB100002-1", "JOI", "São José dos Pinhais", "SC", "63"),
        registro("CWB100003-1", "CWB", "Curitiba", "PR", "47"),
        registro("CWB100004-1", "APU", "Curitiba", "PR", "63"),
    ]

    resultado = filtrar_registros(base, fluxo="CWB")

    assert len(resultado) == 2
    assert {item["numero"] for item in resultado} == {
        "100001",
        "100002",
    }


def test_filtro_big():
    base = [
        registro("BIG100004-1", "APU", "Florianópolis", "SC", "64"),
        registro("BIG100005-1", "JOI", "Blumenau", "PR", "64"),
        registro("BIG100006-1", "BHZ", "Joinville", "SC", "63"),
    ]

    resultado = filtrar_registros(base, fluxo="BIG")

    assert len(resultado) == 1
    assert resultado[0]["numero"] == "100004"


def test_fluxo_invalido():
    try:
        filtrar_registros([], fluxo="MTZ")
    except ValueError:
        pass
    else:
        raise AssertionError("Deveria rejeitar fluxo inválido")