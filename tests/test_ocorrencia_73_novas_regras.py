from modules.ocorrencia_73.parser import (
    filtrar_registros,
)


CLIENTES = {
    "WHIRLPOOL S/A",
}

ROTAS = {
    "CWB": {
        "CURITIBA",
    },
    "JOI": {
        "FLORIANOPOLIS",
    },
}


def registro(
    *,
    ctrc,
    emissora,
    receptora,
    cidade,
    ocorrencia,
    cliente="WHIRLPOOL S/A",
):
    return {
        "Cliente Pagador": cliente,
        "Cidade do Destinatario": cidade,
        "Unidade Emissora": emissora,
        "Unidade Receptora": receptora,
        "Serie/Numero CTRC": ctrc,
        "Codigo Ultima Ocorrencia": ocorrencia,
    }


def test_regras_ocorrencia_73():
    registros = [
        # 1 - Regra antiga CWB
        registro(
            ctrc="CWB100001-1",
            emissora="CWB",
            receptora="CWB",
            cidade="CURITIBA",
            ocorrencia="1",
        ),

        # 2 - Regra antiga JOI -> FLORIANOPOLIS
        # Não precisa OC64.
        registro(
            ctrc="JOI100002-2",
            emissora="JOI",
            receptora="JOI",
            cidade="FLORIANOPOLIS",
            ocorrencia="1",
        ),

        # 3 - Nova regra JOI -> BIG -> OC64
        # Cidade propositalmente diferente.
        registro(
            ctrc="JOI100003-3",
            emissora="JOI",
            receptora="BIG",
            cidade="PALHOCA",
            ocorrencia="64",
        ),

        # 4 - BIG, mas sem OC64
        registro(
            ctrc="JOI100004-4",
            emissora="JOI",
            receptora="BIG",
            cidade="PALHOCA",
            ocorrencia="63",
        ),

        # 5 - OC64, mas receptora não é BIG
        registro(
            ctrc="JOI100005-5",
            emissora="JOI",
            receptora="JOI",
            cidade="PALHOCA",
            ocorrencia="64",
        ),

        # 6 - Cidade da expansão antiga,
        # mas não atende mais nenhuma regra.
        registro(
            ctrc="JOI100006-6",
            emissora="JOI",
            receptora="JOI",
            cidade="SAO JOSE",
            ocorrencia="64",
        ),

        # 7 - Atenderia CWB, mas cliente não permitido
        registro(
            ctrc="CWB100007-7",
            emissora="CWB",
            receptora="CWB",
            cidade="CURITIBA",
            ocorrencia="1",
            cliente="CLIENTE QUALQUER LTDA",
        ),
    ]

    resultado = filtrar_registros(
        registros=registros,
        clientes_permitidos=CLIENTES,
        rotas_permitidas=ROTAS,
    )

    ctrcs = {
        item["ctrc_original"]
        for item in resultado
    }

    assert ctrcs == {
        "CWB100001-1",
        "JOI100002-2",
        "JOI100003-3",
        "CWB100007-7",
    }

    por_ctrc = {
        item["ctrc_original"]: item
        for item in resultado
    }

    assert (
        por_ctrc["CWB100001-1"]["regra_filtro"]
        == "rota_antiga"
    )

    assert (
        por_ctrc["JOI100002-2"]["regra_filtro"]
        == "rota_antiga"
    )

    assert (
        por_ctrc["JOI100003-3"]["regra_filtro"]
        == "joi_big_oc64"
    )

    assert (
        por_ctrc["JOI100003-3"]["unidade_receptora"]
        == "BIG"
    )

    assert (
        por_ctrc["JOI100003-3"]["ultima_ocorrencia"]
        == "64"
    )


def test_big_oc64_qualquer_emissora():
    registros = [
        registro(
            ctrc=f"{emissora}200001-1",
            emissora=emissora,
            receptora="BIG",
            cidade="PALHOCA",
            ocorrencia="64",
        )
        for emissora in ("BHZ", "GRU", "CWB", "JOI")
    ]

    resultado = filtrar_registros(
        registros=registros,
        clientes_permitidos=CLIENTES,
        rotas_permitidas=ROTAS,
    )

    assert {
        item["ctrc_original"]
        for item in resultado
    } == {
        "BHZ200001-1",
        "GRU200001-1",
        "CWB200001-1",
        "JOI200001-1",
    }

    assert all(
        item["regra_filtro"] == "joi_big_oc64"
        for item in resultado
    )


def test_big_oc64_cliente_nao_permitido():
    registros = [
        registro(
            ctrc="BHZ200002-2",
            emissora="BHZ",
            receptora="BIG",
            cidade="PALHOCA",
            ocorrencia="64",
            cliente="CLIENTE NAO PERMITIDO",
        ),
    ]

    resultado = filtrar_registros(
        registros=registros,
        clientes_permitidos=CLIENTES,
        rotas_permitidas=ROTAS,
    )

    assert resultado == []
